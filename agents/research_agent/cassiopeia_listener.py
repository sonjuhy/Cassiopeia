"""
Research Agent Cassiopeia 리스너
- cassiopeia-sdk CassiopeiaClient.listen()으로 cassiopeia 디스패치 수신 (Redis Pub/Sub)
- ResearchAgent._handle_task()에 위임 후 cassiopeia /results로 결과 보고
- agent:research-agent:health Redis Hash를 15초 주기로 갱신 (CassiopeiaManager HealthMonitor 연동)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import redis.asyncio as aioredis
from cassiopeia_sdk.client import AgentMessage, CassiopeiaClient

from .agent import ResearchAgent

logger = logging.getLogger("research_agent.cassiopeia_listener")

_AGENT_NAME = "research-agent"
_HEALTH_KEY = f"agent:{_AGENT_NAME}:health"
_HEARTBEAT_INTERVAL: int = int(os.environ.get("HEARTBEAT_INTERVAL", "15"))
_HTTP_REPORT_TIMEOUT: float = float(os.environ.get("HTTP_REPORT_TIMEOUT", "10.0"))
_DISPATCH_TIMEOUT: float = float(os.environ.get("DISPATCH_TIMEOUT", "180.0"))
_HEALTH_TTL: int = _HEARTBEAT_INTERVAL * 4

_NLU_DESCRIPTION = (
    "- research-agent: 웹 검색, 정보 수집, 조사를 수행할 때 사용합니다. "
    "사용자의 질문에 대해 최신 정보를 검색하여 보고서를 작성합니다. "
    "(actions: search, research, investigate)"
)


class ResearchCassiopeiaListener:
    """
    CassiopeiaManager ↔ ResearchAgent 연결 브리지 (Pub/Sub 기반).

    - cassiopeia-sdk listen()으로 agent:research-agent 채널 구독
    - ResearchAgent._handle_task()에 위임
    - HTTP POST {cassiopeia_url}/results 결과 보고 (agent 내부에서 처리)
    - 15초 주기 heartbeat (agent:research-agent:health)
    """

    def __init__(
        self,
        agent: ResearchAgent | None = None,
        redis_url: str | None = None,
        cassiopeia_url: str | None = None,
    ) -> None:
        self._agent = agent or ResearchAgent()

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
        agent:research-agent Pub/Sub 채널을 구독하는 메인 루프.
        CancelledError를 수신하면 정상 종료합니다.
        """
        cassiopeia = await self._ensure_cassiopeia()
        logger.info("[ResearchCassiopeiaListener] listen_tasks 시작 (channel: agent:%s)", _AGENT_NAME)

        try:
            async for msg in cassiopeia.listen():
                task = asyncio.create_task(self._handle_task(msg))
                task.add_done_callback(
                    lambda t: t.exception() if not t.cancelled() and t.exception() else None
                )
        except asyncio.CancelledError:
            logger.info("[ResearchCassiopeiaListener] listen_tasks 정상 종료")
        except Exception as exc:
            logger.error("[ResearchCassiopeiaListener] listen_tasks 오류: %s", exc)
            raise

    async def _handle_task(self, msg: AgentMessage) -> None:
        """
        수신한 AgentMessage를 ResearchAgent에 위임합니다.

        msg.payload는 cassiopeia가 전송한 dispatch dict입니다.
        ResearchAgent._handle_task()는 JSON 문자열을 기대하므로 직렬화 후 전달합니다.
        """
        task_id = msg.payload.get("task_id", "unknown") if isinstance(msg.payload, dict) else "unknown"
        self._current_task_count += 1
        await self._update_health("BUSY")

        try:
            logger.info("[ResearchCassiopeiaListener] 태스크 수신: task_id=%s action=%s", task_id, msg.action)
            raw = json.dumps(msg.payload, ensure_ascii=False)
            
            # 180초 타임아웃 적용
            await asyncio.wait_for(
                self._agent._handle_task(raw, self._cassiopeia_url),
                timeout=_DISPATCH_TIMEOUT
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.error("[ResearchCassiopeiaListener] 태스크 처리 시간 초과 task_id=%s", task_id)
        except asyncio.CancelledError:
            logger.warning("[ResearchCassiopeiaListener] 태스크 취소됨: task_id=%s", task_id)
            raise
        except Exception as exc:
            logger.error("[ResearchCassiopeiaListener] 태스크 처리 실패 task_id=%s: %s", task_id, exc)
        finally:
            self._current_task_count = max(0, self._current_task_count - 1)
            if self._current_task_count == 0:
                await self._update_health("IDLE")

    async def _heartbeat_loop(self) -> None:
        """
        15초 주기로 agent:research-agent:health Redis Hash를 갱신합니다.
        CassiopeiaManager HealthMonitor가 이 키를 읽어 가용 여부를 판단합니다.
        CancelledError를 수신하면 정상 종료합니다.
        """
        logger.info("[ResearchCassiopeiaListener] heartbeat 시작")
        try:
            while True:
                await self._update_health(
                    "BUSY" if self._current_task_count > 0 else "IDLE"
                )
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            logger.info("[ResearchCassiopeiaListener] heartbeat 정상 종료")

    async def _update_health(self, status: str) -> None:
        """agent:research-agent:health Hash 필드를 업데이트하고 중앙 레지스트리에 능력치를 동적으로 등록합니다."""
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
                    "capabilities": "investigate,web_search",
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
                "capabilities": ["search", "research", "investigate"],
                "registered_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False))

        except Exception as exc:
            logger.warning("[ResearchCassiopeiaListener] 헬스/레지스트리 업데이트 실패: %s", exc)

    async def run(self) -> None:
        """listen_tasks와 _heartbeat_loop를 동시에 실행합니다."""
        hb_task = asyncio.create_task(self._heartbeat_loop())
        try:
            await self.listen_tasks()
        finally:
            hb_task.cancel()
            await self.close()
            logger.info("[ResearchCassiopeiaListener] 실행 종료")
