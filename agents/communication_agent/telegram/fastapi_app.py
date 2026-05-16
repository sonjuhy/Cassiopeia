"""
FastAPI 서버 + Telegram 봇 실시간 리스너
- TelegramCommAgent 기반 양방향 게이트웨이 (Redis 메시지 브로커 연동)
- python-telegram-bot Application을 FastAPI lifespan 백그라운드 태스크로 실행
- Redis agent:communication:telegram:tasks 큐 리스너를 별도 백그라운드 태스크로 실행

엔드포인트:
    POST /send      : Telegram 채팅에 메시지 전송
    GET  /health    : 헬스체크
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

load_dotenv(encoding="utf-8", override=True)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .agent import TelegramCommAgent
from ..models import TelegramEvent
from ..slack.redis_broker import RedisBroker

logger = logging.getLogger("telegram_agent.fastapi_app")

if not logging.root.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


# ─── 싱글톤 컨텍스트 ────────────────────────────────────────────────────────────


class _AppContext:
    def __init__(self) -> None:
        self.ptb_app: Application | None = None
        self.comm_agent: TelegramCommAgent | None = None
        self.redis: RedisBroker | None = None
        self._polling_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None


_ctx = _AppContext()


# ─── Telegram 핸들러 ────────────────────────────────────────────────────────────

async def _handle_setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _ctx.comm_agent is not None and update.message:
        await _ctx.comm_agent.handle_setup_command(update.message)

async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram 텍스트 메시지를 수신하여 TelegramCommAgent로 전달합니다."""
    msg = update.message
    if msg is None or msg.text is None:
        return

    user = msg.from_user
    if user is None or user.is_bot:
        return

    text = msg.text.strip()
    if not text:
        return

    # 설정 JSON 입력 응답 처리
    if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.is_bot:
        if "키 설정을 위한 JSON 데이터를 입력" in (msg.reply_to_message.text or ""):
            if _ctx.comm_agent is not None:
                await _ctx.comm_agent.handle_setup_reply(msg)
            return

    event = TelegramEvent(
        user_id=str(user.id),
        chat_id=str(msg.chat_id),
        text=text,
        message_id=str(msg.message_id),
    )

    logger.info(
        "[TelegramBot] 메시지 수신 — user=%s chat=%s: %.80s",
        event["user_id"],
        event["chat_id"],
        event["text"],
    )

    if _ctx.comm_agent is not None:
        await _ctx.comm_agent.on_user_message(event, msg)


async def _handle_callback_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    인라인 버튼 클릭(CallbackQuery)을 처리합니다.
    callback_data 형식: "{action}:{task_id}"
    """
    query = update.callback_query
    if query is None or query.data is None:
        return

    await query.answer()  # Telegram에 버튼 클릭 확인 (로딩 스피너 제거)

    data = query.data
    if ":" not in data:
        return

    action, task_id = data.split(":", 1)
    user_id = str(query.from_user.id) if query.from_user else ""
    chat_id = str(query.message.chat_id) if query.message else ""

    if action == "setup_agent" and _ctx.comm_agent is not None:
        await _ctx.comm_agent.handle_setup_callback(query, task_id)
        return

    if _ctx.comm_agent is not None:
        await _ctx.comm_agent.on_approval_callback(
            action=action,
            task_id=task_id,
            user_id=user_id,
            chat_id=chat_id,
        )

    action_labels = {
        "approve": "✅ 승인됨",
        "request_revision": "✏️ 수정 요청됨",
        "cancel": "🚫 취소됨",
    }
    label = action_labels.get(action, action)
    user_mention = (
        f"@{query.from_user.username}"
        if query.from_user and query.from_user.username
        else user_id
    )

    await query.edit_message_reply_markup(reply_markup=None)  # 버튼 제거
    if query.message:
        await query.message.reply_text(f"{label} — {user_mention}님이 처리했습니다.")


# ─── FastAPI lifespan ────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_token: str = os.environ["TELEGRAM_BOT_TOKEN"]

    # ── Redis 브로커 초기화 ──
    redis_enabled = False
    try:
        _ctx.redis = RedisBroker()
        redis_enabled = await _ctx.redis.ping()
        if redis_enabled:
            logger.info("[lifespan] Redis 연결 성공")
        else:
            logger.warning("[lifespan] Redis ping 실패 — Redis 기능 비활성화")
            _ctx.redis = None
    except Exception as exc:
        logger.warning("[lifespan] Redis 초기화 실패 (%s) — Redis 기능 비활성화", exc)
        _ctx.redis = None

    # ── python-telegram-bot Application 초기화 ──
    _ctx.ptb_app = Application.builder().token(bot_token).build()

    # ── TelegramCommAgent 초기화 ──
    _ctx.comm_agent = TelegramCommAgent(
        bot=_ctx.ptb_app.bot,
        redis=_ctx.redis,
    )

    # ── 핸들러 등록 ──
    _ctx.ptb_app.add_handler(CommandHandler("setup", _handle_setup_command))
    _ctx.ptb_app.add_handler(CommandHandler("설정", _handle_setup_command))
    _ctx.ptb_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message)
    )
    _ctx.ptb_app.add_handler(CallbackQueryHandler(_handle_callback_query))

    # ── PTB 앱 초기화 및 폴링 시작 ──
    await _ctx.ptb_app.initialize()
    await _ctx.ptb_app.start()

    async def _run_polling() -> None:
        logger.info("[lifespan] Telegram 폴링 시작")
        try:
            await _ctx.ptb_app.updater.start_polling(drop_pending_updates=True)
            # 폴링은 내부적으로 루프를 돌므로 태스크가 완료될 때까지 대기
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass

    _ctx._polling_task = asyncio.create_task(_run_polling())

    # ── Redis 결과 리스너 백그라운드 태스크 실행 ──
    if redis_enabled and _ctx.comm_agent is not None:

        async def _run_listen() -> None:
            try:
                await _ctx.comm_agent.listen_system_results()
            except Exception as exc:
                logger.error("[lifespan] Redis 결과 리스너 오류: %s", exc)

        _ctx._listen_task = asyncio.create_task(_run_listen())
        logger.info("[lifespan] Redis 결과 리스너 시작")

    yield  # 서버가 요청을 처리하는 동안 대기

    # ── 종료 처리 ──
    logger.info("[lifespan] 종료 중...")

    for task in [_ctx._polling_task, _ctx._listen_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    if _ctx.ptb_app is not None:
        await _ctx.ptb_app.updater.stop()
        await _ctx.ptb_app.stop()
        await _ctx.ptb_app.shutdown()

    if _ctx.redis is not None:
        await _ctx.redis.close()

    logger.info("[lifespan] 종료 완료")


# ─── FastAPI 앱 정의 ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Telegram Communication Agent API",
    description="TelegramCommAgent 기반 실시간 Telegram ↔ Redis 양방향 게이트웨이",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/send", summary="Telegram 채팅에 메시지 전송")
async def send_message(request: Request) -> JSONResponse:
    """
    REST API를 통해 특정 Telegram 채팅에 메시지를 직접 전송합니다.

    Request Body (JSON):
        chat_id (str): Telegram 채팅 ID
        text (str): 전송할 메시지 텍스트 (HTML 파싱 모드 지원)
        reply_to_message_id (str | None): 답글로 보낼 경우 원본 메시지 ID
    """
    if _ctx.comm_agent is None:
        raise HTTPException(
            status_code=503, detail="TelegramCommAgent가 초기화되지 않았습니다."
        )

    body: dict[str, Any] = await request.json()
    chat_id: str = body.get("chat_id", "")
    text: str = body.get("text", "")
    reply_to_message_id: str | None = body.get("reply_to_message_id")

    if not chat_id or not text:
        raise HTTPException(status_code=400, detail="chat_id와 text는 필수입니다.")

    try:
        message_id = await _ctx.comm_agent.send_message(
            chat_id, text, reply_to_message_id
        )
        return JSONResponse({"ok": True, "message_id": message_id})
    except Exception as exc:
        logger.exception("[/send] Telegram 메시지 전송 실패: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health", summary="헬스체크")
async def health_check() -> JSONResponse:
    """서비스 상태, Telegram 봇 연결 여부, Redis 연결 여부를 반환합니다."""
    polling_running = _ctx._polling_task is not None and not _ctx._polling_task.done()
    listen_running = _ctx._listen_task is not None and not _ctx._listen_task.done()

    bot_info: dict[str, Any] = {}
    if _ctx.ptb_app is not None:
        try:
            bot = await _ctx.ptb_app.bot.get_me()
            bot_info = {"username": bot.username, "id": bot.id}
        except Exception:
            pass

    redis_connected = False
    if _ctx.redis is not None:
        try:
            redis_connected = await _ctx.redis.ping()
        except Exception:
            pass

    return JSONResponse(
        {
            "status": "ok",
            "polling_running": polling_running,
            "redis_listener_running": listen_running,
            "redis_connected": redis_connected,
            "bot": bot_info,
        }
    )
