"""
ScheduleAgent 구체 구현체
- Google Calendar API 연동
- cassiopeia-sdk CassiopeiaClient.listen()으로 카시오페아 디스패치 수신
- 처리 결과를 HTTP POST /results 로 카시오페아에 전송
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
import redis.asyncio as aioredis
from cassiopeia_sdk.client import AgentMessage as SdkAgentMessage, CassiopeiaClient

from shared_core.calendar.interfaces import CalendarEvent, CalendarEventId

from .config import ScheduleAgentConfig, load_config_from_env
from .providers import GoogleCalendarProvider

logger = logging.getLogger("schedule_agent.agent")

_HEARTBEAT_INTERVAL: int = int(os.environ.get("HEARTBEAT_INTERVAL", "15"))
_HTTP_REPORT_TIMEOUT: float = float(os.environ.get("HTTP_REPORT_TIMEOUT", "10.0"))
_DLQ_KEY = "cassiopeia:dlq"


class ScheduleAgent:
    """
    일정 관리 에이전트.
    cassiopeia-sdk를 사용해 카시오페아로부터 태스크 메시지를 수신합니다.
    """

    agent_name: str = "schedule-agent"

    def __init__(
        self,
        config: ScheduleAgentConfig | None = None,
        calendar_provider: GoogleCalendarProvider | None = None,
    ) -> None:
        self._config = config or load_config_from_env()
        self._provider = calendar_provider or GoogleCalendarProvider(
            calendar_id=self._config.calendar_id,
            service_account_key_file=self._config.service_account_key_file,
            service_account_key_json=self._config.service_account_key_json,
            scopes=self._config.scopes,
        )

    async def process_message(self, action: str, payload: dict) -> dict[str, Any]:
        match action:
            case "list_schedules": return await self._handle_list(payload)
            case "add_schedule": return await self._handle_add(payload)
            case "modify_schedule": return await self._handle_modify(payload)
            case "remove_schedule": return await self._handle_remove(payload)
            case _: return {"status": "error", "message": f"알 수 없는 action: {action}"}

    async def _handle_list(self, payload: dict) -> dict:
        try:
            # NLU가 시간을 파싱하지 못했을 경우 오늘을 기본값으로 사용
            if "start_time" not in payload or "end_time" not in payload:
                now = datetime.now(timezone.utc)
                start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end_time = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            else:
                start_time = datetime.fromisoformat(payload["start_time"])
                end_time = datetime.fromisoformat(payload["end_time"])
                
            events = await self._provider.get_events(start_time, end_time)
            return {"status": "success", "events": [e.__dict__ for e in events]}
        except Exception as e: return {"status": "error", "message": str(e)}

    async def _handle_add(self, payload: dict) -> dict:
        try:
            event = CalendarEvent(**payload["event"])
            event_id = await self._provider.create_event(event)
            return {"status": "success", "event_id": event_id}
        except Exception as e: return {"status": "error", "message": str(e)}

    async def _handle_modify(self, payload: dict) -> dict:
        try:
            event = CalendarEvent(**payload["event"])
            success = await self._provider.update_event(payload["event_id"], event)
            return {"status": "success" if success else "error"}
        except Exception as e: return {"status": "error", "message": str(e)}

    async def _handle_remove(self, payload: dict) -> dict:
        try:
            success = await self._provider.delete_event(payload["event_id"])
            return {"status": "success" if success else "error"}
        except Exception as e: return {"status": "error", "message": str(e)}

    async def _report_result(
        self,
        cassiopeia_url: str,
        task_id: str,
        status: str,
        result_data: dict[str, Any],
        error: dict[str, Any] | None,
        redis: aioredis.Redis | None = None,
    ) -> None:
        """처리 결과를 카시오페아 /results 엔드포인트로 전송합니다. 최대 3회 재시도 후 DLQ 저장."""
        payload = {
            "task_id": task_id,
            "agent": self.agent_name,
            "status": status,
            "result_data": result_data,
            "error": error,
            "usage_stats": {},
        }
        url = f"{cassiopeia_url}/results"
        headers = {}
        if self._config.cassiopeia_api_key:
            headers["X-API-Key"] = self._config.cassiopeia_api_key

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=_HTTP_REPORT_TIMEOUT) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                logger.info("[ScheduleAgent] 결과 보고 완료: task_id=%s status=%s", task_id, status)
                return
            except Exception as exc:
                wait = 2 ** attempt
                logger.warning("[ScheduleAgent] 결과 보고 실패 (attempt %d/3): %s — %ds 후 재시도", attempt + 1, exc, wait)
                if attempt < 2:
                    await asyncio.sleep(wait)
        logger.error("[ScheduleAgent] 결과 보고 최종 실패: task_id=%s", task_id)
        if redis:
            try:
                dlq_entry = {**payload, "failed_at": datetime.now(timezone.utc).isoformat(), "reason": "http_report_failed"}
                await redis.rpush(_DLQ_KEY, json.dumps(dlq_entry, ensure_ascii=False))
                logger.warning("[ScheduleAgent] 결과 DLQ 저장: task_id=%s", task_id)
            except Exception as dlq_exc:
                logger.error("[ScheduleAgent] DLQ 저장 실패: %s", dlq_exc)

    async def _handle_task(
        self,
        msg: SdkAgentMessage,
        cassiopeia_url: str,
        redis: aioredis.Redis | None = None,
    ) -> None:
        """cassiopeia AgentMessage를 처리하고 결과를 카시오페아로 전송합니다.

        payload 구조:
            {
                "task_id": "...",
                "params": { 
                    "credentials": { ... }, # 오케스트라가 주입한 시크릿
                    ... 
                }
            }
        """
        task_id = msg.payload.get("task_id", "unknown")
        agent_result: dict[str, Any] = {
            "status": "FAILED",
            "result_data": {},
            "error": {"code": "INTERNAL_ERROR", "message": "처리 중 알 수 없는 오류", "traceback": None},
        }
        try:
            action = msg.action
            params = msg.payload.get("params", {})
            logger.info("[ScheduleAgent] 태스크 수신: task_id=%s action=%s", task_id, action)

            # ── 동적 시크릿 주입 처리 ──
            # 오케스트라가 DB에서 꺼내 주입한 credentials 가 있으면 provider 를 새로 생성합니다.
            injected_creds = params.get("credentials", {})
            if injected_creds:
                logger.info("[ScheduleAgent] 동적 시크릿 주입됨: task_id=%s", task_id)
                self._provider = GoogleCalendarProvider(
                    calendar_id=injected_creds.get("GOOGLE_CALENDAR_ID") or self._config.calendar_id,
                    service_account_key_json=injected_creds.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "",
                    scopes=self._config.scopes,
                )

            result = await self.process_message(action, params)

            if result.get("status") == "error":
                msg_text = result.get("message", "실행 오류")
                # 자격 증명 관련 에러인 경우 /설정 명령어 안내 추가
                if "서비스 계정 키" in msg_text or "credentials" in msg_text.lower():
                    msg_text += "\n💡 슬랙에서 '/설정' 명령어를 입력하여 구글 캘린더 키를 등록해 주세요."

                agent_result = {
                    "status": "FAILED",
                    "result_data": {},
                    "error": {"code": "EXECUTION_ERROR", "message": msg_text, "traceback": None},
                }
            else:
                agent_result = {
                    "status": "COMPLETED",
                    "result_data": {
                        "summary": f"{action} 완료",
                        "content": json.dumps(result, ensure_ascii=False, indent=2),
                        "data": result,
                    },
                    "error": None,
                }

        except asyncio.CancelledError:
            logger.warning("[ScheduleAgent] 태스크 취소됨: task_id=%s", task_id)
            agent_result["error"] = {"code": "CANCELLED", "message": "태스크가 취소되었습니다.", "traceback": None}
            raise
        except Exception as exc:
            logger.error("[ScheduleAgent] 태스크 처리 실패 task_id=%s: %s", task_id, exc)
            agent_result["error"] = {"code": "INTERNAL_ERROR", "message": str(exc), "traceback": None}
        finally:
            try:
                await self._report_result(
                    cassiopeia_url=cassiopeia_url,
                    task_id=task_id,
                    status=agent_result.get("status", "FAILED"),
                    result_data=agent_result.get("result_data", {}),
                    error=agent_result.get("error"),
                    redis=redis,
                )
            except Exception as exc:
                logger.error("[ScheduleAgent] 결과 보고 실패 task_id=%s: %s", task_id, exc)

    async def run(self) -> None:
        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
        if "localhost" in redis_url:
            redis_url = redis_url.replace("localhost", "127.0.0.1")
        cassiopeia_url = os.environ.get("CASSIOPEIA_URL", "http://cassiopeia-agent:8001")
        health_key = f"agent:{self.agent_name}:health"

        import re
        safe_redis_url = re.sub(r":([^:@]+)@", ":***MASKED***@", redis_url)
        logger.info("[ScheduleAgent] 실행 시작 (Redis: %s, agent: %s)", safe_redis_url, self.agent_name)

        # 하트비트와 DLQ는 직접 Redis 클라이언트 사용
        redis = aioredis.from_url(redis_url, decode_responses=True)

        # 메시지 수신은 cassiopeia-sdk 사용
        cassiopeia = CassiopeiaClient(agent_id=self.agent_name, redis_url=redis_url)
        await cassiopeia.connect()

        async def heartbeat_loop():
            while True:
                try:
                    await redis.hset(health_key, mapping={
                        "status": "IDLE",
                        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                        "version": "1.0.0"
                    })
                    await redis.expire(health_key, 60)
                    await asyncio.sleep(_HEARTBEAT_INTERVAL)
                except asyncio.CancelledError:
                    break
                except Exception:
                    await asyncio.sleep(5)

        hb_task = asyncio.create_task(heartbeat_loop())

        try:
            async for msg in cassiopeia.listen():
                asyncio.create_task(self._handle_task(msg, cassiopeia_url, redis))
        except asyncio.CancelledError:
            logger.info("[ScheduleAgent] 종료")
        finally:
            hb_task.cancel()
            await cassiopeia.disconnect()
            await redis.aclose()
            logger.info("[ScheduleAgent] 실행 종료")
