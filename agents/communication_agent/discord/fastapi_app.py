"""
FastAPI 서버 + Discord 봇 실시간 리스너
- DiscordCommAgent 기반 양방향 게이트웨이 (Redis 메시지 브로커 연동)
- discord.py AsyncClient를 FastAPI lifespan 백그라운드 태스크로 실행
- Redis agent:communication:discord:tasks 큐 리스너를 별도 백그라운드 태스크로 실행

엔드포인트:
    POST /send      : Discord 채널에 메시지 전송
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

import discord
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .agent import DiscordCommAgent
from ..slack.redis_broker import RedisBroker

logger = logging.getLogger("discord_agent.fastapi_app")

if not logging.root.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


# ─── 싱글톤 컨텍스트 ────────────────────────────────────────────────────────────


class _AppContext:
    def __init__(self) -> None:
        self.bot: DiscordBot | None = None
        self.comm_agent: DiscordCommAgent | None = None
        self.redis: RedisBroker | None = None
        self._bot_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None


_ctx = _AppContext()


# ─── Discord 봇 클라이언트 ───────────────────────────────────────────────────────


class DiscordBot(discord.Client):
    """Discord 이벤트를 수신하여 DiscordCommAgent로 전달하는 봇 클라이언트."""

    def __init__(self, comm_agent: DiscordCommAgent, **kwargs: Any) -> None:
        intents = discord.Intents.default()
        intents.message_content = (
            True  # 메시지 내용 읽기 권한 (Discord 개발자 포털에서도 활성화 필요)
        )
        super().__init__(intents=intents, **kwargs)
        self._comm_agent = comm_agent

    async def on_ready(self) -> None:
        logger.info(
            "[DiscordBot] 봇 준비 완료: %s (id=%s)",
            self.user,
            self.user.id if self.user else "N/A",
        )

    async def on_message(self, message: discord.Message) -> None:
        """
        Discord 채널 메시지 이벤트를 수신합니다.
        - 봇 자신의 메시지는 무시합니다.
        - 봇이 @멘션된 경우 또는 DM인 경우 처리합니다.
        """
        if message.author.bot:
            return

        # DM이거나 봇이 @멘션된 경우에만 처리
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.user in message.mentions if self.user else False

        if not is_dm and not is_mentioned:
            return

        # @멘션 제거하여 순수 텍스트 추출
        text = message.content
        if self.user:
            text = text.replace(f"<@{self.user.id}>", "").replace(
                f"<@!{self.user.id}>", ""
            )
        text = text.strip()

        if not text:
            return

        if text.startswith("/설정") or text.startswith("/setup"):
            await self._comm_agent.handle_setup_command(message)
            return

        from ..models import DiscordEvent

        event = DiscordEvent(
            user_id=str(message.author.id),
            channel_id=str(message.channel.id),
            guild_id=str(message.guild.id) if message.guild else None,
            text=text,
            message_id=str(message.id),
        )

        logger.info(
            "[DiscordBot] 메시지 수신 — user=%s channel=%s: %.80s",
            event["user_id"],
            event["channel_id"],
            event["text"],
        )

        await self._comm_agent.on_user_message(event, message)

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        logger.exception("[DiscordBot] 이벤트 처리 오류 (%s)", event_method)


# ─── FastAPI lifespan ────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_token: str = os.environ["DISCORD_BOT_TOKEN"]

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

    # ── DiscordCommAgent 초기화 ──
    _ctx.comm_agent = DiscordCommAgent(redis=_ctx.redis)

    # ── Discord 봇 초기화 ──
    _ctx.bot = DiscordBot(comm_agent=_ctx.comm_agent)
    _ctx.comm_agent.set_client(_ctx.bot)

    # ── Discord 봇 백그라운드 태스크 실행 ──
    async def _run_bot() -> None:
        logger.info("[lifespan] Discord 봇 연결 시작")
        try:
            await _ctx.bot.start(bot_token)
        except Exception as exc:
            logger.error("[lifespan] Discord 봇 오류: %s", exc)

    _ctx._bot_task = asyncio.create_task(_run_bot())

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

    for task in [_ctx._bot_task, _ctx._listen_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    if _ctx.bot is not None:
        await _ctx.bot.close()

    if _ctx.redis is not None:
        await _ctx.redis.close()

    logger.info("[lifespan] 종료 완료")


# ─── FastAPI 앱 정의 ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Discord Communication Agent API",
    description="DiscordCommAgent 기반 실시간 Discord ↔ Redis 양방향 게이트웨이",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/send", summary="Discord 채널에 메시지 전송")
async def send_message(request: Request) -> JSONResponse:
    """
    REST API를 통해 특정 Discord 채널에 메시지를 직접 전송합니다.

    Request Body (JSON):
        channel_id (str): Discord 채널 ID
        text (str): 전송할 메시지 텍스트
        reference_message_id (str | None): 답글로 보낼 경우 원본 메시지 ID
    """
    if _ctx.comm_agent is None:
        raise HTTPException(
            status_code=503, detail="DiscordCommAgent가 초기화되지 않았습니다."
        )

    body: dict[str, Any] = await request.json()
    channel_id: str = body.get("channel_id", "")
    text: str = body.get("text", "")
    reference_message_id: str | None = body.get("reference_message_id")

    if not channel_id or not text:
        raise HTTPException(status_code=400, detail="channel_id와 text는 필수입니다.")

    try:
        message_id = await _ctx.comm_agent.send_message(
            channel_id, text, reference_message_id
        )
        return JSONResponse({"ok": True, "message_id": message_id})
    except Exception as exc:
        logger.exception("[/send] Discord 메시지 전송 실패: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health", summary="헬스체크")
async def health_check() -> JSONResponse:
    """서비스 상태, Discord 봇 연결 여부, Redis 연결 여부를 반환합니다."""
    bot_running = _ctx._bot_task is not None and not _ctx._bot_task.done()
    listen_running = _ctx._listen_task is not None and not _ctx._listen_task.done()
    bot_ready = _ctx.bot is not None and _ctx.bot.is_ready()

    redis_connected = False
    if _ctx.redis is not None:
        try:
            redis_connected = await _ctx.redis.ping()
        except Exception:
            pass

    return JSONResponse(
        {
            "status": "ok",
            "bot_running": bot_running,
            "bot_ready": bot_ready,
            "redis_listener_running": listen_running,
            "redis_connected": redis_connected,
        }
    )
