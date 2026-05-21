"""
Archive Agent Cassiopeia 리스너
- cassiopeia-sdk CassiopeiaClient.listen()으로 cassiopeia 디스패치 수신 (Redis Pub/Sub)
- UnifiedArchiveAgent.handle_dispatch()에 위임 후 cassiopeia /results로 결과 보고
- agent:archive_agent:health Redis Hash를 15초 주기로 갱신 (CassiopeiaManager HealthMonitor 연동)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
import redis.asyncio as aioredis
from cassiopeia_sdk.client import AgentMessage, CassiopeiaClient

from .unified_agent import UnifiedArchiveAgent

logger = logging.getLogger("archive_agent.cassiopeia_listener")

_AGENT_NAME = "archive_agent"
_HEALTH_KEY = f"agent:{_AGENT_NAME}:health"
_DLQ_KEY = "cassiopeia:dlq"
_HEARTBEAT_INTERVAL: int = int(os.environ.get("HEARTBEAT_INTERVAL", "15"))
_HTTP_REPORT_TIMEOUT: float = float(os.environ.get("HTTP_REPORT_TIMEOUT", "10.0"))
_DISPATCH_TIMEOUT: float = float(os.environ.get("DISPATCH_TIMEOUT", "60.0"))
_HEALTH_TTL: int = _HEARTBEAT_INTERVAL * 4

_NLU_DESCRIPTION = (
    "- archive_agent: Notion/Obsidian 자료 조회 및 저장 (Archive Hub)\n"
    "  - actions:\n"
    "    - list_databases: 연결된 모든 노션 데이터베이스 목록 조회\n"
    "    - get_database_schema: 특정 데이터베이스의 컬럼 구조 및 타입 파악 (params: database_id)\n"
    "    - query_database: 데이터베이스 항목 목록 조회 (params: database_id[선택])\n"
    "    - get_page: 특정 페이지 상세 내용 조회 (params: page_id[필수])\n"
    "    - create_page: 노션에 새 페이지 생성 또는 저장 (params: title[필수], database_id[선택], content[선택])\n"
    "    - search_objects: 노션 내의 페이지, DB를 가리지 않고 키워드 기반 통합 검색 (params: query)\n"
    "    - search: 노션/옵시디언 전체 검색 (params: query)\n"
    "    - read_file: 옵시디언 파일 내용 읽기 (params: page_id)\n"
    "    - write_file: 옵시디언 파일 생성/수정 (params: title[필수], content[선택])\n"
    "    - append_file: 옵시디언 파일에 내용 추가 (params: title[필수], content[필수])\n"
    "    - list_files: 옵시디언 볼트 파일 목록 검색 (params: query[선택])"
)


class ArchiveCassiopeiaListener:
    """
    CassiopeiaManager ↔ UnifiedArchiveAgent 연결 브리지 (Pub/Sub 기반).

    - cassiopeia-sdk listen()으로 agent:archive_agent 채널 구독
    - UnifiedArchiveAgent에게 위임 (스스로 Notion/Obsidian 판단)
    - HTTP POST {cassiopeia_url}/results 결과 보고
    - 15초 주기 heartbeat (agent:archive_agent:health)
    """

    def __init__(
        self,
        archive_agent: UnifiedArchiveAgent | None = None,
        redis_url: str | None = None,
        cassiopeia_url: str | None = None,
    ) -> None:
        self._agent = archive_agent or UnifiedArchiveAgent()

        _url = redis_url or os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
        self._redis_url = _url.replace("localhost", "127.0.0.1")
        self._cassiopeia_url = cassiopeia_url or os.environ.get(
            "CASSIOPEIA_URL", "http://127.0.0.1:8001"
        )
        self._redis: aioredis.Redis | None = None
        self._cassiopeia: CassiopeiaClient | None = None
        self._current_task_count: int = 0

    async def _ensure_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    async def _ensure_cassiopeia(self) -> CassiopeiaClient:
        if self._cassiopeia is None:
            self._cassiopeia = CassiopeiaClient(
                agent_id=_AGENT_NAME, redis_url=self._redis_url
            )
            await self._cassiopeia.connect()
        return self._cassiopeia

    async def close(self) -> None:
        if self._cassiopeia:
            await self._cassiopeia.disconnect()
            self._cassiopeia = None
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def listen_tasks(self) -> None:
        """
        agent:archive_agent Pub/Sub 채널을 구독하는 메인 루프.
        CancelledError를 수신하면 정상 종료합니다.
        """
        cassiopeia = await self._ensure_cassiopeia()
        logger.info("[ArchiveCassiopeiaListener] listen_tasks 시작 (channel: agent:%s)", _AGENT_NAME)

        try:
            async for msg in cassiopeia.listen():
                task = asyncio.create_task(self._handle_task(msg))
                task.add_done_callback(
                    lambda t: t.exception() if not t.cancelled() and t.exception() else None
                )
        except asyncio.CancelledError:
            logger.info("[ArchiveCassiopeiaListener] listen_tasks 정상 종료")
        except Exception as exc:
            logger.error("[ArchiveCassiopeiaListener] listen_tasks 오류: %s", exc)
            raise

    async def _handle_task(self, msg: AgentMessage) -> None:
        """
        수신한 AgentMessage를 파싱하고 ArchiveAgent에 위임한 뒤 결과를 보고합니다.

        payload 구조 (cassiopeia manager가 dict(dispatch)로 전송):
            {
                "task_id": "...",
                "action": "...",
                "params": { ... },
                ...
            }
        """
        task_id = "unknown"
        agent_result: dict[str, Any] = {
            "task_id": task_id,
            "agent": _AGENT_NAME,
            "status": "FAILED",
            "result_data": {},
            "reference_id": None,
            "payload_summary": None,
            "error": {"code": "INTERNAL_ERROR", "message": "처리 중 알 수 없는 오류", "traceback": None},
            "usage_stats": {},
        }
        try:
            dispatch_msg: dict[str, Any] = msg.payload if isinstance(msg.payload, dict) else {}
            task_id = dispatch_msg.get("task_id", "unknown")
            agent_result["task_id"] = task_id

            logger.info("[ArchiveCassiopeiaListener] 태스크 수신: task_id=%s action=%s", task_id, msg.action)

            self._current_task_count += 1
            await self._update_health("BUSY")

            try:
                result = await asyncio.wait_for(
                    self._agent.handle_dispatch(dispatch_msg),
                    timeout=_DISPATCH_TIMEOUT
                )
                agent_result = {**result, "agent": _AGENT_NAME, "task_id": task_id}
            except (asyncio.TimeoutError, TimeoutError):
                logger.error("[ArchiveCassiopeiaListener] 태스크 처리 시간 초과 task_id=%s", task_id)
                agent_result["error"] = {
                    "code": "TIMEOUT",
                    "message": f"태스크 처리가 시간 초과되었습니다 ({_DISPATCH_TIMEOUT}초).",
                    "traceback": None,
                }

        except asyncio.CancelledError:
            logger.warning("[ArchiveCassiopeiaListener] 태스크 취소됨: task_id=%s", task_id)
            agent_result["error"] = {
                "code": "CANCELLED",
                "message": "태스크가 취소되었습니다.",
                "traceback": None,
            }
            raise
        except Exception as exc:
            logger.error("[ArchiveCassiopeiaListener] 태스크 처리 실패 task_id=%s: %s", task_id, exc)
            agent_result["error"] = {
                "code": "INTERNAL_ERROR",
                "message": str(exc),
                "traceback": None,
            }
        finally:
            self._current_task_count = max(0, self._current_task_count - 1)
            if self._current_task_count == 0:
                await self._update_health("IDLE")
            try:
                await self._report_result(
                    task_id=agent_result.get("task_id", task_id),
                    agent=agent_result.get("agent", _AGENT_NAME),
                    result_data=agent_result.get("result_data", {}),
                    status=agent_result.get("status", "FAILED"),
                    error=agent_result.get("error"),
                    reference_id=agent_result.get("result_data", {}).get("reference_id"),
                    payload_summary=agent_result.get("result_data", {}).get("payload_summary"),
                )
            except Exception as exc:
                logger.error("[ArchiveCassiopeiaListener] 결과 보고 실패 task_id=%s: %s", task_id, exc)

    async def _report_result(
        self,
        task_id: str,
        agent: str,
        result_data: dict[str, Any],
        status: str,
        error: dict[str, Any] | None,
        reference_id: str | None = None,
        payload_summary: str | None = None,
    ) -> None:
        """
        처리 결과를 CassiopeiaManager POST /results 엔드포인트로 전송합니다.
        네트워크 오류 시 최대 3회 재시도 (1s, 2s, 4s 백오프).
        """
        payload = {
            "task_id": task_id,
            "agent": agent,
            "status": status,
            "result_data": result_data,
            "reference_id": reference_id,
            "payload_summary": payload_summary,
            "error": error,
            "usage_stats": {},
        }
        url = f"{self._cassiopeia_url}/results"
        
        # 환경변수에서 인증 키 로드 (따옴표 제거)
        api_key = (os.environ.get("ADMIN_API_KEY") or os.environ.get("CLIENT_API_KEY", "")).strip("\"'")

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=_HTTP_REPORT_TIMEOUT) as client:
                    resp = await client.post(
                        url, 
                        json=payload,
                        headers={"X-API-Key": api_key}
                    )
                    resp.raise_for_status()
                logger.info(
                    "[ArchiveCassiopeiaListener] 결과 보고 완료: task_id=%s status=%s",
                    task_id,
                    status,
                )
                return
            except Exception as exc:
                wait = 2**attempt
                logger.warning(
                    "[ArchiveCassiopeiaListener] 결과 보고 실패 (attempt %d/3): %s — %ds 후 재시도",
                    attempt + 1,
                    exc,
                    wait,
                )
                if attempt < 2:
                    await asyncio.sleep(wait)

        logger.error("[ArchiveCassiopeiaListener] 결과 보고 최종 실패: task_id=%s", task_id)
        try:
            redis = await self._ensure_redis()
            dlq_entry = {
                **payload,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "reason": "http_report_failed",
            }
            await redis.rpush(_DLQ_KEY, json.dumps(dlq_entry, ensure_ascii=False))
            logger.warning("[ArchiveCassiopeiaListener] 결과 DLQ 저장: task_id=%s", task_id)
        except Exception as dlq_exc:
            logger.error("[ArchiveCassiopeiaListener] DLQ 저장 실패: %s", dlq_exc)

    async def _heartbeat_loop(self) -> None:
        """
        15초 주기로 agent:archive_agent:health Redis Hash를 갱신합니다.
        CassiopeiaManager HealthMonitor가 이 키를 읽어 가용 여부를 판단합니다.
        CancelledError를 수신하면 정상 종료합니다.
        """
        logger.info("[ArchiveCassiopeiaListener] heartbeat 시작")
        try:
            while True:
                await self._update_health(
                    "BUSY" if self._current_task_count > 0 else "IDLE"
                )
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            logger.info("[ArchiveCassiopeiaListener] heartbeat 정상 종료")

    async def _update_health(self, status: str) -> None:
        """agent:archive_agent:health Hash 필드를 업데이트하고 중앙 레지스트리에 능력치를 동적으로 등록합니다."""
        try:
            redis = await self._ensure_redis()
            
            # 1. 헬스 체크 업데이트 (하트비트)
            await redis.hset(
                _HEALTH_KEY,
                mapping={
                    "agent_id": _AGENT_NAME,
                    "status": status,
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "version": "2.0.0",
                    "capabilities": "archive_notion,archive_obsidian,analyze_content",
                    "current_tasks": str(self._current_task_count),
                    "max_concurrency": "3",
                },
            )
            await redis.expire(_HEALTH_KEY, _HEALTH_TTL)

            # 2. 중앙 레지스트리에 NLU 설명 동적 등록
            await redis.hset("agents:registry", _AGENT_NAME, json.dumps({
                "name": _AGENT_NAME,
                "lifecycle_type": "long_running",
                "nlu_description": _NLU_DESCRIPTION,
                "capabilities": ["notion", "obsidian", "archive"],
                "registered_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False))

        except Exception as exc:
            logger.warning("[ArchiveCassiopeiaListener] 헬스/레지스트리 업데이트 실패: %s", exc)
