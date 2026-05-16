"""
Telegram 소통 에이전트 (TelegramCommAgent)
- Telegram Bot API와 cassiopeia Pub/Sub 사이의 양방향 게이트웨이
- Inbound:  Telegram 메시지 → on_user_message → cassiopeia Pub/Sub agent:cassiopeia
- Outbound: cassiopeia Pub/Sub agent:telegram_communication_agent → listen_system_results → Telegram
- Feedback: [승인/수정 요청/취소] 인라인 버튼 클릭 → Redis cassiopeia:approval:{task_id}
"""

from __future__ import annotations

import asyncio
import json
import httpx
import logging
import os
import uuid
from typing import Any

from cassiopeia_sdk.client import CassiopeiaClient
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

from ..models import TelegramEvent
from ..slack.redis_broker import RedisBroker
from .formatter import TelegramFormatter
from shared_core.dispatch_auth import sign_task

logger = logging.getLogger("telegram_agent.agent")

_MAX_RETRIES = 3

# 권한 관리: 허용된 채팅/사용자 (비어있으면 전체 허용)
_ALLOWED_CHATS: list[str] = [
    c for c in os.environ.get("TELEGRAM_ALLOWED_CHATS", "").split(",") if c
]
_ALLOWED_USER_IDS: list[str] = [
    u for u in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",") if u
]


def _is_authorized(user_id: str, chat_id: str) -> bool:
    chat_ok = not _ALLOWED_CHATS or chat_id in _ALLOWED_CHATS
    user_ok = not _ALLOWED_USER_IDS or user_id in _ALLOWED_USER_IDS
    return chat_ok and user_ok


def _build_approval_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """승인/수정 요청/취소 인라인 키보드를 생성합니다."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 승인", callback_data=f"approve:{task_id}"),
            InlineKeyboardButton("✏️ 수정 요청", callback_data=f"request_revision:{task_id}"),
            InlineKeyboardButton("🚫 취소", callback_data=f"cancel:{task_id}"),
        ]
    ])


class TelegramCommAgent:
    """
    Telegram ↔ Redis 양방향 소통 에이전트.

    환경 변수:
        TELEGRAM_BOT_TOKEN        : Telegram 봇 토큰 (@BotFather에서 발급)
        TELEGRAM_ALLOWED_CHATS    : 허용된 채팅 ID 목록 (쉼표 구분, 비어있으면 전체 허용)
        TELEGRAM_ALLOWED_USERS    : 허용된 사용자 ID 목록 (쉼표 구분, 비어있으면 전체 허용)
        REDIS_URL                 : Redis 접속 URL (기본값: redis://localhost:6379)
    """

    agent_name: str = "telegram_communication_agent"

    def __init__(
        self,
        bot: Bot | None = None,
        redis: RedisBroker | None = None,
        cassiopeia: CassiopeiaClient | None = None,
    ) -> None:
        self._bot = bot
        self._redis = redis
        self._cassiopeia: CassiopeiaClient | None = cassiopeia
        self._cassiopeia_connected: bool = cassiopeia is not None
        self._heartbeat_task: asyncio.Task[None] | None = None

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot

    async def handle_setup_command(self, message: Message) -> None:
        """'/setup' 명령어 처리: 에이전트 선택 인라인 키보드를 보냅니다."""
        agents = ["schedule-agent", "research-agent", "archive_agent"]
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(agent, callback_data=f"setup_agent:{agent}")] for agent in agents
        ])
        await message.reply_text("⚙️ <b>에이전트 설정 위저드</b>\n설정(API 키 등록 등)이 필요한 에이전트를 선택하세요.", reply_markup=keyboard, parse_mode=ParseMode.HTML)

    async def handle_setup_callback(self, query: Any, agent_name: str) -> None:
        """에이전트 선택 시 JSON 입력을 요청하는 메시지(ForceReply 트리거)를 보냅니다."""
        agents_guide = {
            "schedule-agent": "GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_CALENDAR_ID",
            "research-agent": "GEMINI_API_KEY, PERPLEXITY_API_KEY",
            "archive_agent": "NOTION_TOKEN, NOTION_DB_ID"
        }
        guide = agents_guide.get(agent_name, "필요한 설정을 입력하세요.")
        from telegram import ForceReply
        
        await query.message.reply_text(
            f"*{agent_name}* 키 설정을 위한 JSON 데이터를 입력해주세요.\n"
            f"가이드: `{guide}`\n\n"
            f"이 메시지에 '답장(Reply)' 형태로 JSON을 전송해주세요.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ForceReply(selective=True)
        )

    async def handle_setup_reply(self, message: Message) -> None:
        """사용자가 보낸 JSON 데이터를 파싱하여 Admin API로 전송합니다."""
        import httpx
        raw_text = message.text or ""
        
        # 'archive_agent 키 설정을 위한 JSON 데이터를 입력해주세요.' 에서 추출
        reply_text = message.reply_to_message.text or ""
        agent_name = ""
        import re
        match = re.search(r"\*(.*?)\* 키 설정", reply_text)
        if match:
            agent_name = match.group(1)
        elif "키 설정을 위한" in reply_text:
            agent_name = reply_text.split("키 설정을")[0].strip("* ")
        
        if not agent_name:
            await message.reply_text("❌ 에이전트 이름을 파악할 수 없습니다.")
            return

        try:
            secrets = json.loads(raw_text)
            if not isinstance(secrets, dict):
                raise ValueError("JSON 객체(dict) 형식이어야 합니다.")
        except Exception as e:
            await message.reply_text(f"❌ 유효한 JSON이 아닙니다: {e}")
            return

        cassiopeia_url = os.environ.get("CASSIOPEIA_URL", "http://cassiopeia-agent:8001").strip("/")
        admin_key = os.environ.get("ADMIN_API_KEY", "").strip('"\'')
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{cassiopeia_url}/admin/secrets/{agent_name}",
                    json=secrets,
                    headers={"X-API-Key": admin_key}
                )
                resp.raise_for_status()
            
            await message.reply_text(f"✅ <b>{agent_name}</b> 설정이 성공적으로 저장되었습니다.", parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error("[setup] 시크릿 저장 실패: %s", e)
            await message.reply_text(f"❌ <b>{agent_name}</b> 설정 저장 중 오류가 발생했습니다: {e}", parse_mode=ParseMode.HTML)

    async def _ensure_cassiopeia(self) -> CassiopeiaClient:
        if self._cassiopeia is None:
            redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379").replace("localhost", "127.0.0.1")
            self._cassiopeia = CassiopeiaClient(agent_id=self.agent_name, redis_url=redis_url)
        if not self._cassiopeia_connected:
            await self._cassiopeia.connect()
            self._cassiopeia_connected = True
        return self._cassiopeia

    # ── 권한 확인 ──────────────────────────────────────────────────────────────

    def is_authorized(self, user_id: str, chat_id: str) -> bool:
        return _is_authorized(user_id, chat_id)

    # ── Inbound: 사용자 메시지 처리 ────────────────────────────────────────────

    async def on_user_message(self, event: TelegramEvent, message: Message) -> None:
        """
        Telegram 메시지를 수신하여 카시오페아 Redis 큐로 전달합니다.

        Args:
            event (TelegramEvent): 파싱된 Telegram 이벤트.
            message (Message): 원본 Telegram 메시지 객체 (접수 확인 전송용).
        """
        user_id = event["user_id"]
        chat_id = event["chat_id"]
        message_id = event["message_id"]

        if not self.is_authorized(user_id, chat_id):
            logger.warning("[TelegramAgent] 미허가 접근 user=%s chat=%s", user_id, chat_id)
            return

        if self._redis is None:
            logger.warning("[TelegramAgent] Redis 미설정 — on_user_message 건너뜀")
            return

        # 접수 확인 메시지 전송
        await message.reply_text("⏳ 요청을 접수했습니다. 처리 중입니다...")

        task_id = str(uuid.uuid4())
        session_id = f"{user_id}:{chat_id}"
        task = {
            "task_id": task_id,
            "session_id": session_id,
            "requester": {"user_id": user_id, "channel_id": chat_id},
            "content": event["text"],
            "source": "telegram",
            "thread_ts": message_id,
        }
        cassiopeia = await self._ensure_cassiopeia()
        await cassiopeia.send_message(
            action="user_request",
            payload=sign_task(task),
            receiver="cassiopeia",
        )

        await self._redis.save_task_context(task_id, {
            "channel_id": chat_id,
            "thread_ts": message_id,    # Telegram에서는 원본 메시지 ID
            "user_id": user_id,
            "session_id": f"{user_id}:{chat_id}",
            "platform": "telegram",
        })

        logger.info(
            "[TelegramAgent] 카시오페아 전달 — task_id=%s user=%s message_id=%s",
            task_id, user_id, message_id,
        )

    async def on_approval_callback(
        self,
        action: str,
        task_id: str,
        user_id: str,
        chat_id: str,
    ) -> None:
        """
        인라인 버튼 클릭(CallbackQuery)을 처리하여 카시오페아에 피드백을 전달합니다.

        Args:
            action (str): "approve" | "request_revision" | "cancel"
            task_id (str): 버튼 callback_data에서 추출한 태스크 ID.
            user_id (str): 버튼을 클릭한 Telegram 사용자 ID.
            chat_id (str): 버튼이 있는 채팅 ID.
        """
        if self._redis is None:
            return

        feedback: dict[str, Any] = {
            "task_id": task_id,
            "action": action,
            "user_id": user_id,
            "channel_id": chat_id,
            "comment": None,
        }
        await self._redis.push_approval(feedback)
        logger.info("[TelegramAgent] 피드백 전달 — task_id=%s action=%s", task_id, action)

    # ── Outbound: 시스템 결과 수신 루프 ───────────────────────────────────────

    async def listen_system_results(self) -> None:
        """cassiopeia Pub/Sub agent:telegram_communication_agent 채널을 모니터링합니다."""
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="telegram_agent_heartbeat"
        )

        cassiopeia = await self._ensure_cassiopeia()
        logger.info("[TelegramAgent] cassiopeia 결과 리스너 시작 (channel: agent:%s)", self.agent_name)
        try:
            async for msg in cassiopeia.listen():
                try:
                    await self._handle_system_result(dict(msg.payload))
                except Exception as exc:
                    logger.error("[TelegramAgent] 결과 처리 오류: %s", exc)
        except asyncio.CancelledError:
            logger.info("[TelegramAgent] listen_system_results 정상 종료")

    async def _heartbeat_loop(self) -> None:
        from datetime import datetime, timezone
        
        nlu_desc = (
            "- telegram_communication_agent: 텔레그램 사용자와의 대화, 추가 질문(ask_clarification), "
            "또는 명확하지 않은 요청에 대해 답변할 때 사용합니다. (actions: ask_clarification, send_message)"
        )

        while True:
            try:
                if self._redis:
                    # 1. 헬스 상태 업데이트
                    await self._redis.update_agent_health(
                        self.agent_name,
                        {
                            "status": "IDLE",
                            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                            "version": "1.0.0",
                        },
                    )
                    
                    # 2. 중앙 레지스트리에 능력치 등록 (동적 라우팅용)
                    await self._redis.update_agent_registry(
                        self.agent_name,
                        {
                            "name": self.agent_name,
                            "lifecycle_type": "long_running",
                            "nlu_description": nlu_desc,
                            "capabilities": ["message", "telegram"],
                            "registered_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    
                    logger.debug("[TelegramAgent] 하트비트/레지스트리 갱신 완료 (agent=%s)", self.agent_name)
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[TelegramAgent] 하트비트 갱신 실패: %s", e)
                await asyncio.sleep(5)

    async def _handle_system_result(self, result: dict[str, Any]) -> None:
        """수신된 결과를 Telegram으로 전송합니다."""
        task_id: str = result.get("task_id", "")
        content: str = result.get("content", "")
        requires_approval: bool = result.get("requires_user_approval", False)
        agent_name: str = result.get("agent_name", "에이전트")
        progress_percent: int | None = result.get("progress_percent")

        ctx = await self._redis.get_task_context(task_id) if self._redis else None
        if ctx is None:
            logger.warning("[TelegramAgent] 태스크 컨텍스트 없음: task_id=%s", task_id)
            return

        chat_id = ctx["channel_id"]
        ref_message_id = ctx.get("thread_ts")

        # 진행 상태 업데이트
        if progress_percent is not None:
            await self._post_progress_update(
                task_id=task_id,
                chat_id=chat_id,
                percent=progress_percent,
                message=content,
            )
            return

        # 최종 결과 전송
        if requires_approval:
            text = f"⚠️ <b>실행 승인 요청</b>\n\n{TelegramFormatter.format(content)}"
            keyboard = _build_approval_keyboard(task_id)
            await self._send_with_retry(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                reply_to_message_id=int(ref_message_id) if ref_message_id else None,
            )
        else:
            text = f"✅ <b>작업이 완료되었습니다.</b>\n\n{TelegramFormatter.format(content)}\n\n<i>처리 에이전트: {agent_name}</i>"
            await self._send_with_retry(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=int(ref_message_id) if ref_message_id else None,
            )

    async def _post_progress_update(
        self,
        task_id: str,
        chat_id: str,
        percent: int,
        message: str,
    ) -> None:
        """진행 상태 메시지를 편집하거나 새로 전송합니다."""
        if self._redis is None or self._bot is None:
            return

        progress_text = f"🔄 {message} ({percent}%)"
        existing_ref = await self._redis.get_progress_msg_ts(task_id)

        if existing_ref:
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=int(existing_ref),
                    text=progress_text,
                )
                return
            except TelegramError:
                pass

        sent = await self._bot.send_message(chat_id=chat_id, text=progress_text)
        await self._redis.save_progress_msg_ts(task_id, str(sent.message_id))

    # ── 재시도 전송 ──────────────────────────────────────────────────────────

    async def _send_with_retry(
        self,
        chat_id: str,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        if self._bot is None:
            logger.warning("[TelegramAgent] Bot 미초기화 — 메시지 전송 건너뜀")
            return

        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": ParseMode.HTML,
        }
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id

        for attempt in range(max_retries):
            try:
                await self._bot.send_message(**kwargs)
                return
            except RetryAfter as e:
                logger.warning("[TelegramAgent] Rate Limit — %.1f초 후 재시도 (%d/%d)", e.retry_after, attempt + 1, max_retries)
                await asyncio.sleep(e.retry_after)
            except TelegramError as exc:
                logger.error("[TelegramAgent] 메시지 전송 실패: %s", exc)
                raise

    # ── 직접 메시지 전송 (REST API 용) ────────────────────────────────────────

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> str:
        """채팅에 메시지를 전송하고 message_id를 반환합니다."""
        if self._bot is None:
            raise RuntimeError("Telegram Bot이 초기화되지 않았습니다.")

        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": ParseMode.HTML,
        }
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = int(reply_to_message_id)

        sent = await self._bot.send_message(**kwargs)
        return str(sent.message_id)
