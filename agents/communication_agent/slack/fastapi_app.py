"""
FastAPI 서버 + Slack SDK Socket Mode 실시간 리스너 (v4)
- SlackCommAgent 기반 양방향 게이트웨이 (Redis 메시지 브로커 연동)
- slack_bolt AsyncApp + AsyncSocketModeHandler를 FastAPI lifespan 백그라운드 태스크로 실행
- Redis agent:communication:tasks 큐 리스너를 별도 백그라운드 태스크로 실행
- [승인] [수정 요청] [취소] 버튼 인터랙션 핸들러 추가
- Server-Sent Events(SSE)를 통한 실시간 메시지 스트리밍 지원

엔드포인트:
    GET  /messages/history  : Slack conversations.history API 채널 메시지 조회
    GET  /messages/recent   : 서버가 수신한 인메모리 최근 메시지 조회
    GET  /messages/live     : SSE 실시간 수신 메시지 스트리밍
    POST /send              : 채널 메시지 전송
    POST /notify            : Notion 승인 대기 태스크 알림 발송
    GET  /health            : 헬스체크
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from dotenv import load_dotenv

load_dotenv(encoding="utf-8", override=True)

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from ..models import SlackEvent
from .agent import SlackCommAgent
from .dispatcher import DockerDispatcher
from .llm_classifier import ClassifierProtocol
from .redis_broker import RedisBroker

from shared_core.agent_logger import setup_logging

logger = logging.getLogger("slack_agent.fastapi_app")

# 보안 마스킹 필터가 적용된 로깅 설정 활성화
setup_logging()

_MAX_RECENT_MESSAGES = 100


# ─── 싱글톤 컨텍스트 ────────────────────────────────────────────────────────────


class _AppContext:
    """FastAPI 앱 생애 동안 단일 인스턴스로 유지되는 공유 상태."""

    def __init__(self) -> None:
        self.web_client: AsyncWebClient | None = None
        self.socket_handler: AsyncSocketModeHandler | None = None
        self.classifier: ClassifierProtocol | None = None
        self.dispatcher: DockerDispatcher | None = None
        self.comm_agent: SlackCommAgent | None = None
        self.redis: RedisBroker | None = None
        self._socket_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None

        self.recent_messages: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT_MESSAGES)
        self.sse_queues: list[asyncio.Queue[dict[str, Any]]] = []


_ctx = _AppContext()


# ─── 수신 메시지 내부 저장 ──────────────────────────────────────────────────────


def _store_received_message(event: dict[str, Any]) -> dict[str, Any]:
    """수신된 Slack 이벤트를 인메모리에 저장하고 SSE 구독자에 브로드캐스트합니다."""
    record: dict[str, Any] = {
        "user": event.get("user", ""),
        "channel": event.get("channel", ""),
        "text": event.get("text", ""),
        "ts": event.get("ts", ""),
        "thread_ts": event.get("thread_ts"),
        "received_at": time.time(),
    }
    _ctx.recent_messages.append(record)

    for q in list(_ctx.sse_queues):
        try:
            q.put_nowait(record)
        except asyncio.QueueFull:
            pass

    return record


# ─── Slack 이벤트 파싱 ──────────────────────────────────────────────────────────


def _parse_slack_event(event: dict[str, Any]) -> SlackEvent | None:
    """slack_bolt 이벤트 핸들러의 event 객체를 SlackEvent TypedDict로 변환합니다."""
    if event.get("subtype") or event.get("bot_id"):
        return None

    text: str = event.get("text", "").strip()
    if not text:
        return None

    return SlackEvent(
        user=event.get("user", ""),
        channel=event.get("channel", ""),
        text=text,
        ts=event.get("ts", ""),
        thread_ts=event.get("thread_ts"),
    )


# ─── LLM 분류기 팩토리 ──────────────────────────────────────────────────────────


def _build_classifier(backend: str) -> ClassifierProtocol:
    """환경변수 CLASSIFIER_BACKEND 값에 따라 적합한 LLM 분류기를 반환합니다."""
    b = backend.lower()
    if b == "gemini_api":
        from .llm_classifier import GeminiAPIClassifier
        return GeminiAPIClassifier()
    if b == "local":
        from .llm_classifier import LLMClassifier
        from shared_core.llm import build_llm_provider
        return LLMClassifier(provider=build_llm_provider(backend="local"))
    if b == "claude_cli":
        from .llm_classifier import ClaudeCLIClassifier
        return ClaudeCLIClassifier()
    if b == "gemini_cli":
        from .llm_classifier import GeminiCLIClassifier
        return GeminiCLIClassifier()
    from .llm_classifier import ClaudeAPIClassifier
    return ClaudeAPIClassifier()


# ─── FastAPI lifespan ────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 앱의 시작/종료 시 Slack Socket Mode 핸들러와
    Redis 결과 리스너를 백그라운드 태스크로 관리합니다.
    """
    bot_token: str = os.environ["SLACK_BOT_TOKEN"]
    app_token: str = os.environ["SLACK_APP_TOKEN"]
    backend: str = os.environ.get("CLASSIFIER_BACKEND", "gemini_api")

    # ── 컨텍스트 초기화 ──
    _ctx.web_client = AsyncWebClient(token=bot_token)
    _ctx.classifier = _build_classifier(backend)
    _ctx.dispatcher = DockerDispatcher()

    # ── Redis 브로커 초기화 (옵션) ──
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

    # ── SlackCommAgent 초기화 ──
    _ctx.comm_agent = SlackCommAgent(
        web_client=_ctx.web_client,
        redis=_ctx.redis,
    )

    # ── slack_bolt AsyncApp 생성 및 이벤트 핸들러 등록 ──
    bolt_app = AsyncApp(token=bot_token)

    @bolt_app.event("message")
    async def handle_message(event: dict, say: Any) -> None:
        """
        Slack 채널 메시지 이벤트를 수신합니다.
        - 인메모리 저장 및 SSE 브로드캐스트
        - Redis 활성화 시: SlackCommAgent.on_user_request → 카시오페아 큐 전달
        - Redis 비활성화 시: LLM 분류 → Docker 컨테이너 디스패치 (폴백)
        """
        if not event.get("subtype") and not event.get("bot_id"):
            _store_received_message(event)

        slack_event = _parse_slack_event(event)
        if slack_event is None:
            return

        logger.info(
            "[bolt] 메시지 수신 — user=%s channel=%s: %.80s",
            slack_event["user"],
            slack_event["channel"],
            slack_event["text"],
        )

        # ── Redis 활성화 경로: 소통 에이전트 → 카시오페아 큐 ──
        if _ctx.redis is not None and _ctx.comm_agent is not None:
            await _ctx.comm_agent.on_user_request(slack_event, say)
            return

        # ── 폴백 경로: LLM 분류 → Docker 디스패치 ──
        try:
            agent_name = await _ctx.classifier.classify(slack_event)  # type: ignore[union-attr]
            logger.info("[bolt] 분류 결과 (폴백): %s", agent_name)
        except Exception as exc:
            logger.exception("[bolt] 분류 실패: %s", exc)
            await say(
                text=f"에이전트 분류 중 오류가 발생했습니다: {exc}",
                thread_ts=slack_event["ts"],
            )
            return

        success, message = await _ctx.dispatcher.dispatch(agent_name, slack_event)  # type: ignore[union-attr]
        status_emoji = "✅" if success else "❌"
        logger.info("[bolt] 디스패치 %s: %s", "완료" if success else "실패", message)
        await say(
            text=f"{status_emoji} *{agent_name}* 에이전트에 전달했습니다.\n`{message}`",
            thread_ts=slack_event["ts"],
        )

    # ── 승인 버튼 인터랙션 핸들러 ──────────────────────────────────────────────

    @bolt_app.action("approve_task")
    async def handle_approve(ack: Any, body: dict, say: Any) -> None:
        """[승인] 버튼 클릭 처리: 카시오페아에 승인 신호를 전달합니다."""
        await ack()
        await _handle_approval_action(body, action="approve", say=say)

    @bolt_app.action("request_revision")
    async def handle_revision(ack: Any, body: dict, say: Any) -> None:
        """[수정 요청] 버튼 클릭 처리: 피드백을 카시오페아에 전달합니다."""
        await ack()
        await _handle_approval_action(body, action="request_revision", say=say)

    @bolt_app.action("cancel_task")
    async def handle_cancel(ack: Any, body: dict, say: Any) -> None:
        """[취소] 버튼 클릭 처리: 작업 취소 신호를 카시오페아에 전달합니다."""
        await ack()
        await _handle_approval_action(body, action="cancel", say=say)

    # ── 설정 위저드 핸들러 ────────────────────────────────────────────────────

    @bolt_app.command("/설정")
    async def handle_setup_command(ack: Any, body: dict) -> None:
        if _ctx.comm_agent:
            await _ctx.comm_agent.handle_setup_command(ack, body)

    @bolt_app.action(re.compile("setup_agent_.*"))
    async def handle_setup_agent_click(ack: Any, body: dict) -> None:
        if _ctx.comm_agent:
            await _ctx.comm_agent.handle_setup_agent_click(ack, body)

    @bolt_app.view("setup_secrets_modal")
    async def handle_setup_modal_submission(ack: Any, body: dict, view: dict) -> None:
        if _ctx.comm_agent:
            await _ctx.comm_agent.handle_modal_submission(ack, body, view)

    # ─── Socket Mode 핸들러 시작 ───
    # Notion 링크 버튼은 URL 전용이므로 ack만 처리
    @bolt_app.action("open_notion")
    async def handle_open_notion(ack: Any) -> None:
        await ack()

    @bolt_app.action("open_github_pr")
    async def handle_open_github_pr(ack: Any) -> None:
        await ack()

    @bolt_app.error
    async def handle_error(error: Exception) -> None:
        logger.error("[bolt] 앱 에러: %s", error)

    # ── AsyncSocketModeHandler 백그라운드 태스크 실행 ──
    handler = AsyncSocketModeHandler(bolt_app, app_token=app_token)
    _ctx.socket_handler = handler

    async def _run_socket() -> None:
        logger.info("[lifespan] Slack Socket Mode 연결 시작 (backend=%s)", backend)
        await handler.start_async()

    _ctx._socket_task = asyncio.create_task(_run_socket())

    # ── Redis 결과 리스너 백그라운드 태스크 실행 ──
    if redis_enabled and _ctx.comm_agent is not None:

        async def _run_listen() -> None:
            try:
                await _ctx.comm_agent.listen_system_results()  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("[lifespan] Redis 결과 리스너 오류: %s", exc)

        _ctx._listen_task = asyncio.create_task(_run_listen())
        logger.info("[lifespan] Redis 결과 리스너 시작")

    yield  # 서버가 요청을 처리하는 동안 대기

    # ── 종료 처리 ──
    logger.info("[lifespan] 종료 중...")

    for q in list(_ctx.sse_queues):
        try:
            q.put_nowait({"event": "close"})
        except asyncio.QueueFull:
            pass

    for task in [_ctx._socket_task, _ctx._listen_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    await handler.close_async()

    if _ctx.redis is not None:
        await _ctx.redis.close()

    logger.info("[lifespan] 종료 완료")


# ─── 승인 액션 공통 처리 ─────────────────────────────────────────────────────────


async def _handle_approval_action(
    body: dict[str, Any],
    action: str,
    say: Any,
) -> None:
    """
    승인/수정 요청/취소 버튼 클릭에 대한 공통 처리 로직.
    task_id로 컨텍스트를 복원하고 카시오페아에 피드백을 전달합니다.
    """
    if _ctx.redis is None or _ctx.comm_agent is None:
        logger.warning("[action] Redis 미설정 — 승인 액션 처리 불가")
        return

    action_element = body.get("actions", [{}])[0]
    task_id: str = action_element.get("value", "")
    user_id: str = body.get("user", {}).get("id", "")
    channel_id: str = body.get("channel", {}).get("id", "")

    if not task_id:
        logger.warning("[action] task_id 없음 — 승인 액션 무시")
        return

    feedback = {
        "task_id": task_id,
        "action": action,
        "user_id": user_id,
        "channel_id": channel_id,
        "comment": None,
    }

    await _ctx.redis.push_approval(feedback)
    logger.info(
        "[action] 피드백 전달 — task_id=%s action=%s user=%s", task_id, action, user_id
    )

    # 메시지 상태 업데이트
    action_labels = {
        "approve": "✅ 승인됨",
        "request_revision": "✏️ 수정 요청됨",
        "cancel": "🚫 취소됨",
    }
    label = action_labels.get(action, action)

    # 원본 메시지의 버튼 블록을 제거하고 결과 텍스트로 업데이트합니다.
    message_ts = body.get("message", {}).get("ts")
    if message_ts and _ctx.web_client:
        try:
            original_blocks = body.get("message", {}).get("blocks", [])
            # 텍스트 섹션(content)만 남기고 actions(버튼) 블록 제거
            new_blocks = [b for b in original_blocks if b.get("type") != "actions"]
            new_blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"*{label}* — <@{user_id}>님이 처리했습니다."}]
            })
            
            await _ctx.web_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=new_blocks,
                text=f"{label} 처리됨"
            )
        except Exception as e:
            logger.error("[action] 원본 메시지 업데이트 실패: %s", e)
            # 폴백: 업데이트 실패 시 스레드에 메시지 남김
            thread_ts: str | None = None
            ctx = await _ctx.redis.get_task_context(task_id)
            if ctx:
                thread_ts = ctx.get("thread_ts")
            await say(text=f"{label} — <@{user_id}>님이 처리했습니다.", thread_ts=thread_ts)


# ─── FastAPI 앱 정의 ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Communication Agent API",
    description="SlackCommAgent 기반 실시간 Slack ↔ Redis 양방향 게이트웨이",
    version="4.0.0",
    lifespan=lifespan,
)


# ──────────────────────────────────────────────────────────────────────────────────
# 메시지 수신 엔드포인트
# ──────────────────────────────────────────────────────────────────────────────────


@app.get("/messages/history", summary="Slack 채널 메시지 히스토리 조회")
async def get_channel_history(
    channel: str = Query(..., description="Slack 채널 ID (예: C06XXXXXXX)"),
    limit: int = Query(20, ge=1, le=200, description="조회할 메시지 수 (최대 200)"),
    oldest: str | None = Query(None, description="이 ts 이후 메시지만 조회"),
    latest: str | None = Query(None, description="이 ts 이전 메시지만 조회"),
) -> JSONResponse:
    """Slack conversations.history API를 통해 채널의 메시지 목록을 조회합니다."""
    if _ctx.web_client is None:
        raise HTTPException(
            status_code=503, detail="WebClient가 초기화되지 않았습니다."
        )

    try:
        kwargs: dict[str, Any] = {"channel": channel, "limit": limit}
        if oldest:
            kwargs["oldest"] = oldest
        if latest:
            kwargs["latest"] = latest

        response = await _ctx.web_client.conversations_history(**kwargs)
        messages: list[dict[str, Any]] = response.get("messages", [])
        parsed: list[dict[str, Any]] = [
            {
                "user": msg.get("user", msg.get("bot_id", "")),
                "text": msg.get("text", ""),
                "ts": msg.get("ts", ""),
                "thread_ts": msg.get("thread_ts"),
                "reply_count": msg.get("reply_count", 0),
                "is_bot": "bot_id" in msg,
            }
            for msg in messages
        ]

        return JSONResponse(
            {
                "ok": True,
                "channel": channel,
                "messages": parsed,
                "has_more": response.get("has_more", False),
            }
        )

    except Exception as exc:
        err_str = str(exc)
        if "missing_scope" in err_str:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "missing_scope",
                    "message": "Slack 앱에 channels:history 스코프가 없습니다.",
                    "needed_scopes": [
                        "channels:history",
                        "groups:history",
                        "mpim:history",
                        "im:history",
                    ],
                },
            ) from exc
        logger.exception("[/messages/history] 조회 실패: %s", exc)
        raise HTTPException(status_code=500, detail=err_str) from exc


@app.get("/messages/recent", summary="서버 수신 메시지 인메모리 조회")
async def get_recent_messages(
    limit: int = Query(
        20, ge=1, le=_MAX_RECENT_MESSAGES, description="반환할 최근 메시지 수"
    ),
    channel: str | None = Query(None, description="특정 채널 ID로 필터링"),
) -> JSONResponse:
    """Socket Mode를 통해 수신하여 인메모리에 저장한 최근 메시지를 반환합니다."""
    all_messages = list(_ctx.recent_messages)

    if channel:
        all_messages = [m for m in all_messages if m.get("channel") == channel]

    sorted_messages = sorted(all_messages, key=lambda m: m.get("ts", ""), reverse=True)
    result = sorted_messages[:limit]

    return JSONResponse(
        {
            "ok": True,
            "messages": result,
            "total_stored": len(all_messages),
        }
    )


@app.get("/messages/live", summary="실시간 메시지 스트리밍 (Server-Sent Events)")
async def stream_live_messages(
    channel: str | None = Query(None, description="특정 채널 ID로 필터링"),
) -> StreamingResponse:
    """Server-Sent Events(SSE)를 통해 수신된 메시지를 실시간으로 스트리밍합니다."""
    client_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=50)
    _ctx.sse_queues.append(client_queue)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            connect_data = json.dumps(
                {
                    "event": "connected",
                    "message": "Slack 실시간 메시지 스트림 연결됨",
                    "filter_channel": channel,
                    "timestamp": time.time(),
                },
                ensure_ascii=False,
            )
            yield f"data: {connect_data}\n\n"

            while True:
                try:
                    record = await asyncio.wait_for(client_queue.get(), timeout=30.0)

                    if record.get("event") == "close":
                        yield 'data: {"event": "server_closing"}\n\n'
                        break

                    if channel and record.get("channel") != channel:
                        continue

                    payload = json.dumps(record, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

        except asyncio.CancelledError:
            logger.info("[SSE] 클라이언트 연결 종료")
        finally:
            if client_queue in _ctx.sse_queues:
                _ctx.sse_queues.remove(client_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ──────────────────────────────────────────────────────────────────────────────────
# 메시지 전송 엔드포인트
# ──────────────────────────────────────────────────────────────────────────────────


@app.post("/send", summary="Slack 채널에 메시지 전송")
async def send_message(request: Request) -> JSONResponse:
    """
    REST API를 통해 특정 Slack 채널에 메시지를 직접 전송합니다.

    Request Body (JSON):
        channel (str): Slack 채널 ID
        text (str): 전송할 메시지 텍스트
        thread_ts (str | None): 스레드 답글로 보낼 경우 원본 메시지의 ts
    """
    if _ctx.web_client is None:
        raise HTTPException(
            status_code=503, detail="WebClient가 초기화되지 않았습니다."
        )

    body: dict[str, Any] = await request.json()
    channel: str = body.get("channel", "")
    text: str = body.get("text", "")
    thread_ts: str | None = body.get("thread_ts")

    if not channel or not text:
        raise HTTPException(status_code=400, detail="channel과 text는 필수입니다.")

    try:
        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        response = await _ctx.web_client.chat_postMessage(**kwargs)
        return JSONResponse({"ok": True, "ts": response.get("ts")})
    except Exception as exc:
        logger.exception("[/send] Slack 메시지 전송 실패: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/notify", summary="Notion 승인 대기 태스크 Slack 알림 발송")
async def notify_pending_tasks() -> JSONResponse:
    """Notion 데이터베이스에서 '승인 대기중' 태스크를 조회하여 Slack으로 알림을 발송합니다."""
    from .notion_parser import parse_notion_task

    if _ctx.comm_agent is None:
        raise HTTPException(
            status_code=503, detail="CommAgent가 초기화되지 않았습니다."
        )

    sent = 0
    failed = 0

    raw_payloads = await _ctx.comm_agent.fetch_notifications()
    for raw in raw_payloads:
        task = parse_notion_task(raw)
        if task is None:
            failed += 1
            continue
        message = await _ctx.comm_agent.format_slack_message(task)
        success, msg = await _ctx.comm_agent.push_to_slack(message)
        if success:
            sent += 1
        else:
            failed += 1
            logger.warning("[/notify] 전송 실패 [%s]: %s", task["title"], msg)

    return JSONResponse({"ok": True, "sent": sent, "failed": failed})


# ──────────────────────────────────────────────────────────────────────────────────
# 유틸리티 엔드포인트
# ──────────────────────────────────────────────────────────────────────────────────


@app.get("/health", summary="헬스체크")
async def health_check() -> JSONResponse:
    """서비스 상태, Slack Socket Mode 연결 여부, Redis 연결 여부를 반환합니다."""
    socket_running: bool = (
        _ctx._socket_task is not None and not _ctx._socket_task.done()
    )
    listen_running: bool = (
        _ctx._listen_task is not None and not _ctx._listen_task.done()
    )
    redis_connected: bool = False
    if _ctx.redis is not None:
        try:
            redis_connected = await _ctx.redis.ping()
        except Exception:
            pass

    return JSONResponse(
        {
            "status": "ok",
            "socket_running": socket_running,
            "redis_listener_running": listen_running,
            "redis_connected": redis_connected,
            "sse_clients": len(_ctx.sse_queues),
            "recent_messages_buffered": len(_ctx.recent_messages),
        }
    )
