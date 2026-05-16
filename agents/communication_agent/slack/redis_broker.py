"""
Redis 메시지 브로커 클라이언트
- 세션 상태·승인 피드백·헬스체크 전용
- 태스크 통신(inbound/outbound)은 모든 플랫폼(Slack/Discord/Telegram)이
  cassiopeia-sdk (Redis Pub/Sub)를 통해 직접 처리합니다.
"""

import json
import logging
import os
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("slack_agent.redis_broker")

# 승인 피드백은 태스크별 큐를 사용 (카시오페아와 일치)
_APPROVAL_KEY_PREFIX = "cassiopeia:approval:"

# 세션 TTL: 2시간
_SESSION_TTL = 7200


class RedisBroker:
    """
    소통 에이전트의 Redis 클라이언트 (세션 상태·승인 피드백·헬스체크 전용).

    환경 변수:
        REDIS_URL: Redis 접속 URL (기본값: redis://localhost:6379)
    """

    def __init__(self, url: str | None = None) -> None:
        redis_url = url or os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
        if "localhost" in redis_url:
            redis_url = redis_url.replace("localhost", "127.0.0.1")

        self._client: aioredis.Redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=60.0,
            socket_connect_timeout=5.0,
        )

    async def push_approval(self, feedback: dict[str, Any]) -> None:
        """
        사용자 승인/반려 피드백을 cassiopeia:approval:{task_id} 큐에 삽입합니다.

        카시오페아는 태스크별 큐(cassiopeia:approval:{approval_task_id})에서
        BLPOP으로 승인 응답을 대기합니다. 단일 큐(cassiopeia:results)에 push하면
        카시오페아가 응답을 수신하지 못하므로 반드시 task_id별 큐를 사용합니다.

        Args:
            feedback (dict): ApprovalFeedback 스키마 딕셔너리.
                             반드시 "task_id" 필드를 포함해야 합니다.
        """
        task_id = feedback.get("task_id", "")
        if not task_id:
            logger.error("[RedisBroker] push_approval: task_id 없음 — 피드백 무시")
            return
        key = f"{_APPROVAL_KEY_PREFIX}{task_id}"
        await self._client.rpush(key, json.dumps(feedback, ensure_ascii=False))
        logger.debug("[RedisBroker] push_approval key=%s action=%s", key, feedback.get("action"))

    # ── 세션 스레드 관리 ───────────────────────────────────────────────────────

    async def get_thread_ts(self, session_id: str) -> str | None:
        """세션에 연결된 Slack 스레드 루트 ts를 조회합니다."""
        return await self._client.get(f"slack:session:{session_id}:thread_ts")

    async def save_thread_ts(self, session_id: str, thread_ts: str) -> None:
        """세션의 스레드 루트 ts를 저장합니다 (TTL: 2시간)."""
        await self._client.setex(f"slack:session:{session_id}:thread_ts", _SESSION_TTL, thread_ts)

    async def get_progress_msg_ts(self, session_id: str) -> str | None:
        """진행 상태 메시지의 ts를 조회합니다 (chat_update 용)."""
        return await self._client.get(f"slack:session:{session_id}:progress_msg_ts")

    async def save_progress_msg_ts(self, session_id: str, ts: str) -> None:
        """진행 상태 메시지의 ts를 저장합니다 (TTL: 2시간)."""
        await self._client.setex(f"slack:session:{session_id}:progress_msg_ts", _SESSION_TTL, ts)

    # ── 태스크 컨텍스트 ────────────────────────────────────────────────────────

    async def save_task_context(self, task_id: str, context: dict[str, Any]) -> None:
        """태스크 컨텍스트(채널 ID, 스레드 ts 등)를 저장합니다."""
        await self._client.setex(
            f"slack:task:{task_id}:context",
            _SESSION_TTL,
            json.dumps(context, ensure_ascii=False),
        )

    async def get_task_context(self, task_id: str) -> dict[str, Any] | None:
        """저장된 태스크 컨텍스트를 조회합니다."""
        data = await self._client.get(f"slack:task:{task_id}:context")
        return json.loads(data) if data else None

    # ── 연결 관리 ──────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Redis 연결 상태를 확인합니다."""
        try:
            await self._client.ping()
            return True
        except Exception:
            return False

    async def update_agent_health(self, agent_name: str, fields: dict[str, str]) -> None:
        """agent:{agent_name}:health Hash를 갱신합니다 (하트비트 전송용)."""
        key = f"agent:{agent_name}:health"
        await self._client.hset(key, mapping=fields)
        await self._client.expire(key, 60)

    async def update_agent_registry(self, agent_name: str, registry_data: dict[str, Any]) -> None:
        """agents:registry Hash에 에이전트 정보를 등록합니다 (동적 라우팅용)."""
        await self._client.hset("agents:registry", agent_name, json.dumps(registry_data, ensure_ascii=False))

    async def close(self) -> None:
        """Redis 연결을 종료합니다."""
        await self._client.aclose()
