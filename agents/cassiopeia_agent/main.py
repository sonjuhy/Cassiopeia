"""
Cassiopeia Agent FastAPI 서버

엔드포인트 목록:
  [시스템]
  GET  /health                        시스템 전체 헬스 조회
  GET  /queue/status                  Redis 에이전트 큐 대기 수 조회

  [에이전트 결과·로그 수신 - 하위 에이전트용]
  POST /results                       하위 에이전트 실행 결과 수신
  POST /logs                          에이전트 활동 로그 수신

  [태스크]
  POST /tasks                         사용자 텍스트 → NLU → 에이전트 디스패치
  GET  /tasks/{task_id}               태스크 상태 조회

  [NLU]
  POST /nlu/analyze                   디스패치 없이 의도 분석만 수행

  [직접 디스패치]
  POST /dispatch                      NLU 없이 특정 에이전트로 직접 태스크 전달

  [에이전트 관리 (하위 에이전트 자기등록·헬스비트용)]
  GET  /agents                        등록된 에이전트 전체 목록 + 가용 목록
  POST /agents                        새 에이전트 레지스트리 자동 등록
  DELETE /agents/{agent_name}         에이전트 레지스트리 해제
  GET  /agents/{agent_name}/health    특정 에이전트 헬스 조회
  PUT  /agents/{agent_name}/heartbeat 에이전트 하트비트 갱신
  GET  /agents/{agent_name}/circuit   Circuit Breaker 상태 조회
  POST /agents/{agent_name}/reset     Circuit Breaker 수동 초기화

  [세션]
  GET  /sessions/{session_id}         세션 상태 조회
  GET  /sessions/{session_id}/history 세션 대화 이력 조회
  DELETE /sessions/{session_id}       세션 초기화

  [사용자 프로필]
  GET  /users/{user_id}/profile       사용자 프로필 조회
  PUT  /users/{user_id}/profile       사용자 프로필 수정

  [관리자 GUI - /admin 접두사]
  → admin_router.py 참조 (대시보드·에이전트 생명주기·권한·큐·태스크·로그·세션·사용자·시스템 메트릭)
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import redis.asyncio as aioredis
import uvicorn
from cassiopeia_sdk.client import CassiopeiaClient
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

load_dotenv(encoding="utf-8", override=True)


def _check_env_or_setup() -> None:
    """필수 환경변수가 없으면 설정 방법을 선택하게 한다."""
    required = ["ADMIN_API_KEY", "CLIENT_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if not missing:
        return

    print("=" * 60)
    print(" Cassiopeia — 필수 환경변수가 설정되지 않았습니다.")
    print(f" 누락된 항목: {', '.join(missing)}")
    print("=" * 60)
    print("\n설정 방법을 선택하세요:")
    print("  1) 설정 마법사 실행 (자동으로 .env 생성)")
    print("  2) 직접 .env 수정 후 재실행")
    print()

    while True:
        choice = input("선택 [1/2]: ").strip()
        if choice == "1":
            import sys
            from pathlib import Path
            wizard_path = Path(__file__).resolve().parents[2] / "tools" / "setup_wizard.py"
            import importlib.util
            spec = importlib.util.spec_from_file_location("setup_wizard", wizard_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.SetupWizard().run()
            load_dotenv(encoding="utf-8", override=True)
            still_missing = [k for k in required if not os.environ.get(k)]
            if still_missing:
                print(f"\n[오류] 아직 누락된 항목이 있습니다: {', '.join(still_missing)}")
                sys.exit(1)
            print("\n환경변수가 로드되었습니다. 서버를 시작합니다...\n")
            return
        elif choice == "2":
            print("\n.env 파일을 수정한 뒤 다시 실행하세요.")
            print("  참고: .env.example 을 .env 로 복사한 뒤 편집하세요.")
            import sys
            sys.exit(0)
        else:
            print("  1 또는 2를 입력하세요.")


_check_env_or_setup()


def _validate_callback_url(url: str) -> None:
    """SSRF 방어: callback_url 에 내부망/루프백/링크로컬 주소가 오지 못하도록 차단한다."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"callback_url 허용되지 않은 스킴: '{parsed.scheme}'. http 또는 https 만 허용됩니다.")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("callback_url 에 호스트가 없습니다.")
    try:
        resolved_ip = socket.gethostbyname(hostname)
    except socket.gaierror as exc:
        raise ValueError(f"callback_url 호스트를 해석할 수 없습니다: {hostname} — {exc}")
    addr = ipaddress.ip_address(resolved_ip)
    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        raise ValueError(
            f"callback_url 내부 또는 예약된 IP 주소로의 요청은 허용되지 않습니다: {resolved_ip}"
        )


from shared_core.llm import OllamaManager
from shared_core.agent_logger import setup_logging
from shared_core.dispatch_auth import sign_task, verify_task, DispatchAuthError

from .app_context import ctx
from .auth import CLIENT_API_KEY, verify_admin_key, verify_client_key

# 보안 마스킹 필터가 적용된 로깅 설정 활성화
setup_logging()

logger = logging.getLogger("cassiopeia_agent.main")
from .admin_router import router as admin_router
from .error_messages import build_error_response, get_user_message
from .rate_limiter import RateLimiter
from .health_monitor import HealthMonitor
from .manager import CassiopeiaManager
from .nlu_engine import build_nlu_engine
from .sandbox_tool import SandboxTool
from .state_manager import StateManager
from .agent_builder_handler import AgentBuilderHandler
from .registry import AgentRegistry
from .marketplace_handler import MarketplaceHandler
from .llm_gateway import LLMGatewayHandler
from .llm_gateway.rate_limiter import TokenRateLimiter

_OLLAMA_READY_TIMEOUT: int = int(os.environ.get("OLLAMA_READY_TIMEOUT", "120"))
_LOCAL_LLM_MODEL: str = os.environ.get("LOCAL_LLM_MODEL", "llama3.2")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cassiopeia_agent.main")

_KNOWN_AGENTS = [
    "archive_agent",
    "research_agent",
    "calendar_agent",
    "file_agent",
    "communication_agent",
]

# ── Lifespan ───────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 수명 주기 관리: 초기화 → 백그라운드 실행 → 종료."""
    logger.info("[Lifespan] Cassiopeia Agent 시작")

    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379").replace(
        "localhost", "127.0.0.1"
    )
    logger.info("[Lifespan] Redis 연결 시도: %s", redis_url)

    ctx.redis_client = aioredis.from_url(
        redis_url, decode_responses=True, socket_timeout=60.0
    )
    try:
        await ctx.redis_client.ping()
        logger.info("[Lifespan] Redis 연결 성공")
    except Exception as exc:
        logger.error("[Lifespan] Redis 연결 실패: %s", exc)
        raise RuntimeError(f"Redis 연결 실패: {exc}")

    ctx.state_manager = StateManager(redis_client=ctx.redis_client)
    ctx.health_monitor = HealthMonitor(redis_client=ctx.redis_client)
    ctx.builder_handler = AgentBuilderHandler()
    ctx.registry = AgentRegistry()
    ctx.marketplace = MarketplaceHandler(
        ctx.builder_handler, ctx.registry, ctx.health_monitor
    )

    ctx.sandbox_tool = SandboxTool()
    try:
        await ctx.sandbox_tool.start()
        logger.info("[Lifespan] SandboxTool 시작 완료 (runtime=%s)", ctx.sandbox_tool.runtime)
    except Exception as exc:
        logger.warning("[Lifespan] SandboxTool 시작 실패 (코드 실행 기능 비활성화): %s", exc)
        ctx.sandbox_tool = None

    llm_backend = os.environ.get("LLM_BACKEND", "gemini").lower()
    
    if llm_backend == "local":
        ollama_mgr = OllamaManager()
        logger.info(
            "[Lifespan] Ollama 준비 대기 중 (timeout=%ds, model=%s)",
            _OLLAMA_READY_TIMEOUT, _LOCAL_LLM_MODEL,
        )
        if not await ollama_mgr.wait_until_ready(timeout=_OLLAMA_READY_TIMEOUT):
            raise RuntimeError(
                f"Ollama 서버 응답 없음 (timeout={_OLLAMA_READY_TIMEOUT}s) — "
                "OLLAMA_BASE_URL 또는 OLLAMA_READY_TIMEOUT을 확인하세요."
            )
        try:
            await ollama_mgr.ensure_model(_LOCAL_LLM_MODEL)
        except RuntimeError as exc:
            raise RuntimeError(f"Ollama 모델 준비 실패 ({_LOCAL_LLM_MODEL}): {exc}") from exc
        logger.info("[Lifespan] Ollama 준비 완료: %s", _LOCAL_LLM_MODEL)

    try:
        nlu_engine = build_nlu_engine()
        logger.info("[Lifespan] NLU 엔진 생성 완료 (%s)", nlu_engine.__class__.__name__)
        if not await nlu_engine.validate():
            raise RuntimeError("LLM API 연결 검증 실패")
    except Exception as exc:
        logger.error("[Lifespan] NLU 초기화 실패: %s", exc)
        raise RuntimeError(f"NLU 초기화 실패: {exc}")

    ctx.manager = CassiopeiaManager(
        redis_client=ctx.redis_client,
        nlu_engine=nlu_engine,
        state_manager=ctx.state_manager,
        health_monitor=ctx.health_monitor,
        sandbox_tool=ctx.sandbox_tool,
    )

    # 기본 코어 에이전트 레지스트리 등록 (비즈니스 로직 에이전트 하드코딩 제거)
    # (caps, lifecycle_type, nlu_description, permission_preset) — nlu_description 생략 시 ""
    _AGENT_CONFIGS: dict[str, tuple] = {
        "communication_agent": (
            ["send_message", "ask_clarification"], 
            "long_running", 
            "- communication_agent: 사용자 질문 및 응답 (actions: ask_clarification)", 
            "standard"
        ),
        # sandbox_agent: 카시오페아 내부 도구로 편입 — ephemeral 등록으로 헬스체크 없이 항상 가용
        "sandbox_agent": (
            ["execute_code", "run_code"],
            "ephemeral",
            (
                "- sandbox_agent: Python/JavaScript/Bash 코드를 격리된 VM(Docker/Firecracker)에서 실행합니다. "
                "params: {language: str, code: str, stdin?: str, timeout?: int, memory_mb?: int}"
            ),
            "minimal"
        ),
    }
    for agent_name, config in _AGENT_CONFIGS.items():
        caps, ltype = config[0], config[1]
        nlu_desc = config[2] if len(config) > 2 else ""
        preset = config[3] if len(config) > 3 else "standard"
        await ctx.health_monitor.register_agent(
            agent_name, caps, lifecycle_type=ltype, nlu_description=nlu_desc, permission_preset=preset
        )

    ctx.cassiopeia_client = CassiopeiaClient(agent_id="cassiopeia-api", redis_url=redis_url)
    await ctx.cassiopeia_client.connect()
    logger.info("[Lifespan] Cassiopeia 클라이언트 연결 완료")

    ctx.llm_gateway = LLMGatewayHandler(
        redis_client=ctx.redis_client,
        llm_provider=nlu_engine._provider,
        cassiopeia=ctx.cassiopeia_client,
        rate_limiter=TokenRateLimiter(redis_client=ctx.redis_client),
    )
    ctx.manager._llm_gateway = ctx.llm_gateway
    logger.info("[Lifespan] LLM Gateway 초기화 완료")

    ctx.listen_task = asyncio.create_task(
        ctx.manager.listen_tasks(), name="cassiopeia_listen_tasks"
    )
    ctx.monitor_task = asyncio.create_task(
        ctx.health_monitor.monitor_loop(interval=30), name="cassiopeia_health_monitor"
    )

    yield

    for t in [ctx.listen_task, ctx.monitor_task]:
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    await ctx.cassiopeia_client.disconnect()

    if ctx.sandbox_tool is not None:
        await ctx.sandbox_tool.shutdown()

    await ctx.state_manager.close()
    await ctx.redis_client.aclose()


# ── FastAPI 앱 ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Cassiopeia Agent API",
    version="2.0.0",
    description=(
        "AI 에이전트 카시오페아 지휘자 — 외부 제어 및 관리자 API\n\n"
        "관리자 GUI 전용 엔드포인트는 `/admin` 접두사를 사용합니다."
    ),
    lifespan=lifespan,
)

# CORS — GUI 도구(Electron, 웹 대시보드 등)에서 접근 가능하도록 설정
_CORS_ORIGINS = [
    o.strip() for o in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",
    ).split(",") if o.strip() and o.strip() != "*"
]

if not _CORS_ORIGINS:
    logger.warning("CORS_ORIGINS가 비어있거나 '*'입니다. 기본적으로 localhost 접근만 허용됩니다.")
    _CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 관리자 라우터 포함
app.include_router(admin_router)

# ── Request / Response 모델 ────────────────────────────────────────────────────


class AgentLogBody(BaseModel):
    agent_name: str = Field(..., max_length=100)
    action: str = Field(..., max_length=200)
    message: str = Field(..., max_length=5000)
    task_id: str | None = Field(None, max_length=100)
    session_id: str | None = Field(None, max_length=100)
    payload: dict[str, Any] | None = None


class AgentResultErrorBody(BaseModel):
    code: str = Field(..., max_length=100)
    message: str = Field(..., max_length=5000)
    traceback: str | None = Field(None, max_length=20000)


class AgentResultBody(BaseModel):
    task_id: str = Field(..., max_length=100)
    agent: str = Field(default="", max_length=100)
    status: str = Field(..., max_length=50)
    result_data: dict[str, Any] = {}
    error: AgentResultErrorBody | None = None
    usage_stats: dict[str, Any] = {}


class SubmitTaskBody(BaseModel):
    content: str = Field(..., description="사용자 자연어 입력", max_length=10000)
    user_id: str = Field(default="api-user", max_length=100)
    channel_id: str = Field(default="api", max_length=100)
    session_id: str | None = Field(None, max_length=100)
    callback_url: str | None = Field(None, description="작업 완료 시 결과를 POST할 웹훅 URL", max_length=1000)


class SubmitMarketplaceInstallBody(BaseModel):
    item_url: str = Field(..., description="마켓플레이스 에이전트 매니페스트 JSON URL", max_length=1000)
    user_id: str = Field(default="admin", max_length=100)


class NLUAnalyzeBody(BaseModel):
    text: str = Field(..., max_length=10000)
    session_id: str = Field(default="nlu-session", max_length=100)
    user_id: str = Field(default="api-user", max_length=100)
    include_context: bool = False


class DirectDispatchBody(BaseModel):
    agent_name: str = Field(..., max_length=100)
    action: str = Field(..., max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)
    content: str = Field(default="", max_length=10000)
    user_id: str = Field(default="api-user", max_length=100)
    channel_id: str = Field(default="api", max_length=100)
    priority: str = Field(default="MEDIUM", max_length=50)
    timeout: int = 300


class RegisterAgentBody(BaseModel):
    agent_name: str = Field(..., max_length=100)
    capabilities: list[str] = Field(default_factory=list)
    lifecycle_type: str = Field(default="long_running", max_length=50)
    nlu_description: str = Field(default="", max_length=2000)


class HeartbeatBody(BaseModel):
    status: str = Field(default="IDLE", max_length=50)
    current_tasks: int = 0
    version: str = Field(default="1.0.0", max_length=50)
    capabilities: list[str] = Field(default_factory=list)
    max_concurrency: int = 1
    nlu_description: str = Field(default="", max_length=2000)


class UpdateUserProfileBody(BaseModel):
    name: str | None = Field(None, max_length=100)
    style_pref: dict[str, str] | None = None


class ApprovalRespondBody(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$", description="approve 또는 reject", max_length=20)


class LLMKeyUpdateBody(BaseModel):
    api_key: str = Field(..., description="LLM 제공업체의 API 키.", max_length=300)


SUPPORTED_LLM_PROVIDERS = ["gemini", "claude", "openai", "local"]


# ── 시스템 엔드포인트 ──────────────────────────────────────────────────────────


@app.get("/health", tags=["시스템"])
async def health_check() -> dict[str, Any]:
    try:
        redis_ok = await ctx.redis_client.ping()
    except Exception:
        redis_ok = False
    system_health = await ctx.health_monitor.get_system_health()
    listen_running = ctx.listen_task is not None and not ctx.listen_task.done()
    sandbox_stats = ctx.sandbox_tool.pool_stats() if ctx.sandbox_tool is not None else None
    return {
        "status": "ok" if redis_ok and listen_running else "degraded",
        "redis_connected": bool(redis_ok),
        "listen_task_running": listen_running,
        "agents": system_health,
        "sandbox": sandbox_stats,
    }


@app.get("/queue/status", tags=["시스템"])
async def queue_status() -> dict[str, Any]:
    """모든 에이전트 큐의 대기 메시지 수를 반환합니다."""
    return await ctx.health_monitor.get_all_queues_status()


# ── 하위 에이전트 수신 엔드포인트 ────────────────────────────────────────────


@app.post("/results", tags=["에이전트"], dependencies=[Depends(verify_client_key)])
async def receive_result(result: AgentResultBody) -> dict[str, Any]:
    result_dict = result.model_dump()
    await ctx.manager.receive_agent_result(result_dict)

    # 작업 히스토리 상태 업데이트
    await ctx.state_manager.update_task_history_status(result.task_id, result.status)

    # 웹훅 콜백 — 비동기 발송 (실패해도 응답에 영향 없음)
    callback_url = await ctx.redis_client.get(f"task:{result.task_id}:callback_url")
    if callback_url and result.status in ("COMPLETED", "FAILED"):
        asyncio.create_task(_fire_webhook(callback_url, result_dict))

    return {"status": "accepted", "task_id": result.task_id}


async def _fire_webhook(url: str, payload: dict[str, Any]) -> None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload)
        logger.info("[Webhook] 콜백 발송 완료: %s", url)
    except Exception as exc:
        logger.warning("[Webhook] 콜백 발송 실패: %s — %s", url, exc)


@app.post("/logs", tags=["에이전트"], dependencies=[Depends(verify_client_key)])
async def receive_log(body: AgentLogBody) -> dict[str, Any]:
    # 대용량 로그로 인한 카시오페아 DB 부하 방지 (Hybrid Architecture)
    message = body.message if len(body.message) <= 1000 else body.message[:1000] + "...(truncated)"
    
    def mask_secrets(text: str) -> str:
        text = re.sub(r"sk-[a-zA-Z0-9\-]{20,}", "***MASKED***", text) # OpenAI, Anthropic
        text = re.sub(r"AIza[a-zA-Z0-9\-_]{30,}", "***MASKED***", text) # Google Gemini
        text = re.sub(r"gh[pous]_[a-zA-Z0-9]{30,}", "***MASKED***", text) # GitHub
        text = re.sub(r"Bearer\s+[a-zA-Z0-9\-\._~+/]+", "Bearer ***MASKED***", text)
        return text

    # 민감 정보 마스킹 (API keys, Bearer tokens)
    message = mask_secrets(message)

    payload = body.payload
    
    if payload:
        payload_str = json.dumps(payload, ensure_ascii=False)
        payload_str = mask_secrets(payload_str)
        
        if len(payload_str) > 2000:
            payload = {"_truncated_": True, "note": "Payload too large to be stored in central DB."}
        else:
            payload = json.loads(payload_str)
            
    await ctx.state_manager.add_agent_log(
        body.agent_name,
        body.action,
        message,
        body.task_id,
        body.session_id,
        payload,
    )
    return {"status": "logged"}


# ── 태스크 엔드포인트 ──────────────────────────────────────────────────────────


@app.get("/prompts/suggestions", tags=["UX 온보딩"])
async def get_prompt_suggestions() -> dict[str, Any]:
    """
    현재 사용 가능한 에이전트 기능을 기반으로 UI에 표시할 추천 프롬프트를 반환합니다.
    """
    available_agents = await ctx.health_monitor.get_available_agents()
    
    # 에이전트별 추천 프롬프트 템플릿
    suggestion_map = {
        "archive_agent": ["최근 회의록 찾아줘", "데이터베이스 목록 보여줘", "새로운 페이지 작성해줘"],
        "research_agent": ["최신 AI 트렌드 요약해줘", "파이썬 비동기 처리 방법 조사해줘"],
        "calendar_agent": ["내일 오후 3시에 회의 일정 추가해줘", "이번 주 내 일정 알려줘"],
        "file_agent": ["현재 디렉토리 파일 목록 보여줘", "README.md 파일 읽어줘"],
        "communication_agent": ["슬랙으로 메시지 보내줘", "팀원에게 진행 상황 공유해줘"]
    }
    
    suggestions = [
        "오늘 날씨 어때?", # 기본 챗봇 프롬프트
        "간단한 인사말 작성해줘"
    ]
    
    for agent in available_agents:
        if agent in suggestion_map:
            # 사용 가능한 에이전트의 추천 프롬프트 중 1개씩 추가
            suggestions.append(suggestion_map[agent][0])
            
    # 최대 5개까지만 반환하도록 제한
    return {"suggestions": suggestions[:5]}


@app.post("/tasks", tags=["태스크"], dependencies=[Depends(verify_client_key)])
async def submit_task(body: SubmitTaskBody, request: Request) -> dict[str, Any]:
    """사용자 자연어 입력을 NLU로 분석하여 적절한 에이전트에 디스패치합니다."""
    # ── Rate Limiting ──────────────────────────────────────────────────────────
    limiter = RateLimiter(ctx.redis_client)
    allowed, retry_after = await limiter.check(body.user_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={**build_error_response("RATE_LIMIT", retry_after=retry_after), "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )

    # ── Idempotency ────────────────────────────────────────────────────────────
    idem_key = request.headers.get("X-Idempotency-Key")
    if idem_key:
        cached = await ctx.state_manager.get_idempotency_result(idem_key)
        if cached:
            return {**cached, "idempotent": True}

    if body.callback_url:
        try:
            _validate_callback_url(body.callback_url)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"callback_url 검증 실패: {exc}",
            )

    task_id = str(uuid.uuid4())
    session_id = body.session_id or f"{body.user_id}:{body.channel_id}"
    task: dict[str, Any] = {
        "task_id": task_id,
        "session_id": session_id,
        "requester": {"user_id": body.user_id, "channel_id": body.channel_id},
        "content": body.content,
        "source": "api",
    }
    if body.callback_url:
        task["callback_url"] = body.callback_url
        # 웹훅 URL을 Redis에 별도 저장 (결과 수신 시 발송용)
        await ctx.redis_client.setex(
            f"task:{task_id}:callback_url", 86400, body.callback_url
        )

    await ctx.cassiopeia_client.send_message(
        action="user_request",
        payload=sign_task(task),
        receiver="cassiopeia",
    )

    # ── 작업 히스토리 저장 ─────────────────────────────────────────────────────
    await ctx.state_manager.save_task_history(task_id, body.user_id, body.content)

    response = {"status": "accepted", "task_id": task_id, "session_id": session_id}

    if idem_key:
        await ctx.state_manager.save_idempotency_result(idem_key, response)

    return response


@app.get("/tasks/{task_id}/stream", tags=["태스크"], dependencies=[Depends(verify_client_key)])
async def stream_task(task_id: str) -> StreamingResponse:
    """태스크 상태 변경을 SSE(Server-Sent Events)로 실시간 스트리밍합니다.

    UI는 EventSource API로 이 엔드포인트를 구독하면 됩니다.
    COMPLETED 또는 FAILED 이벤트 수신 후 스트림이 자동 종료됩니다.
    """
    async def _event_generator():
        poll_interval = 1.0
        max_wait = int(os.environ.get("RESPONSE_TIMEOUT_SEC", "300"))
        elapsed = 0.0
        last_status: str | None = None

        while elapsed < max_wait:
            state = await ctx.state_manager.get_task_state(task_id)
            current_status = state.get("status", "PROCESSING") if state else "PROCESSING"

            if current_status != last_status:
                payload = json.dumps(
                    {"task_id": task_id, "status": current_status, **state},
                    ensure_ascii=False,
                )
                yield f"data: {payload}\n\n"
                last_status = current_status

            if current_status in ("COMPLETED", "FAILED"):
                break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if last_status not in ("COMPLETED", "FAILED"):
            timeout_payload = json.dumps({
                "task_id": task_id,
                "status": "FAILED",
                "error": get_user_message("TIMEOUT"),
            }, ensure_ascii=False)
            yield f"data: {timeout_payload}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/tasks/{task_id}", tags=["태스크"], dependencies=[Depends(verify_client_key)])
async def get_task(task_id: str, include_logs: bool = Query(False, description="최근 진행 로그 포함 여부")) -> dict[str, Any]:
    state = await ctx.state_manager.get_task_state(task_id)
    if not state:
        return {"task_id": task_id, "status": "NOT_FOUND"}
        
    response = {"task_id": task_id, **state}
    if include_logs:
        logs = await ctx.state_manager.get_agent_logs(task_id=task_id, limit=5)
        response["recent_logs"] = logs
        
    return response


@app.post("/tasks/{task_id}/cancel", tags=["태스크"], dependencies=[Depends(verify_client_key)])
async def cancel_task(
    task_id: str,
    user_id: str = Query(default="api-user", description="취소를 요청하는 사용자 ID"),
) -> dict[str, Any]:
    """진행 중인 태스크를 강제로 취소합니다."""
    try:
        success = await ctx.manager.cancel_task(task_id, user_id)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="태스크를 취소할 수 없습니다. 이미 완료/실패했거나 존재하지 않습니다."
        )
    return {"status": "CANCELLED", "task_id": task_id}


# ── NLU 분석 ──────────────────────────────────────────────────────────────────


@app.post("/nlu/analyze", tags=["NLU"], dependencies=[Depends(verify_client_key)])
async def nlu_analyze(body: NLUAnalyzeBody) -> dict[str, Any]:
    """디스패치 없이 NLU 의도 분석 결과만 반환합니다 (개발·테스트용)."""
    context: list[dict[str, Any]] = []
    if body.include_context:
        context = await ctx.state_manager.build_context_for_llm(
            body.session_id, body.user_id
        )

    agent_capabilities = await ctx.health_monitor.get_nlu_capabilities() or None
    result = await ctx.manager._nlu.analyze(
        body.text,
        body.session_id,
        context,
        agent_capabilities=agent_capabilities,
    )
    return result.model_dump()


# ── 직접 디스패치 ──────────────────────────────────────────────────────────────


@app.post("/dispatch", tags=["태스크"], dependencies=[Depends(verify_client_key)])
async def direct_dispatch(body: DirectDispatchBody) -> dict[str, Any]:
    """NLU를 거치지 않고 특정 에이전트에 직접 태스크를 전달합니다."""
    ready, reason = await ctx.health_monitor.is_agent_ready(body.agent_name)
    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"에이전트 '{body.agent_name}' 사용 불가: {reason}",
        )

    task_id = str(uuid.uuid4())
    dispatch_msg = {
        "version": "1.1",
        "task_id": task_id,
        "session_id": f"{body.user_id}:{body.channel_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "requester": {"user_id": body.user_id, "channel_id": body.channel_id},
        "content": body.content,
        "agent": body.agent_name,
        "action": body.action,
        "params": body.params,
        "priority": body.priority,
        "timeout": body.timeout,
        "retry_info": {"count": 0, "max_retries": 3, "reason": None},
        "metadata": {},
    }
    await ctx.cassiopeia_client.send_message(
        action=body.action,
        payload=dispatch_msg,
        receiver=body.agent_name,
    )

    # 결과 대기 (timeout 초)
    result = await ctx.manager.wait_for_result(task_id, timeout=body.timeout)
    return {"task_id": task_id, "agent": body.agent_name, **result}


# ── 에이전트 관리 (자기 등록·하트비트용) ──────────────────────────────────────


@app.get("/agents", tags=["에이전트 관리"])
async def list_agents() -> dict[str, Any]:
    return {
        "available": await ctx.health_monitor.get_available_agents(),
        "all": await ctx.health_monitor.get_system_health(),
    }


@app.post("/agents", tags=["에이전트 관리"], status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(verify_client_key)])
async def register_agent(body: RegisterAgentBody) -> dict[str, Any]:
    """에이전트 시작 시 자기 등록 엔드포인트."""
    await ctx.health_monitor.register_agent(
        body.agent_name,
        body.capabilities,
        lifecycle_type=body.lifecycle_type,
        nlu_description=body.nlu_description,
    )
    return {"status": "registered", "agent_name": body.agent_name}


@app.delete("/agents/{agent_name}", tags=["에이전트 관리"],
            dependencies=[Depends(verify_client_key)])
async def deregister_agent(agent_name: str) -> dict[str, Any]:
    """에이전트 종료 시 자기 해제 엔드포인트."""
    await ctx.redis_client.hdel("agents:registry", agent_name)
    return {"status": "deregistered", "agent_name": agent_name}


@app.get("/agents/{agent_name}/health", tags=["에이전트 관리"])
async def get_agent_health(agent_name: str) -> dict[str, Any]:
    health = await ctx.redis_client.hgetall(f"agent:{agent_name}:health")
    if not health:
        raise HTTPException(
            status_code=404, detail=f"에이전트 '{agent_name}'의 헬스 데이터가 없습니다."
        )
    return {"agent_name": agent_name, "health": health}


@app.put("/agents/{agent_name}/heartbeat", tags=["에이전트 관리"],
         dependencies=[Depends(verify_client_key)])
async def update_heartbeat(agent_name: str, body: HeartbeatBody) -> dict[str, Any]:
    """에이전트 하트비트 갱신 — 에이전트가 주기적으로 호출합니다."""
    mapping: dict[str, str] = {
        "agent_id": agent_name,
        "status": body.status,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "version": body.version,
        "current_tasks": str(body.current_tasks),
        "max_concurrency": str(body.max_concurrency),
    }
    if body.nlu_description:
        mapping["nlu_description"] = body.nlu_description
    if body.capabilities:
        mapping["capabilities"] = ",".join(body.capabilities)

    await ctx.redis_client.hset(f"agent:{agent_name}:health", mapping=mapping)
    await ctx.redis_client.expire(f"agent:{agent_name}:health", 60)
    return {"status": "updated", "agent_name": agent_name}


@app.get("/agents/{agent_name}/circuit", tags=["에이전트 관리"])
async def get_circuit(agent_name: str) -> dict[str, Any]:
    failures = int(await ctx.redis_client.get(f"circuit:{agent_name}:failures") or 0)
    return {
        "agent_name": agent_name,
        "failures": failures,
        "threshold": 3,
        "is_open": failures >= 3,
    }


@app.post("/agents/{agent_name}/reset", tags=["에이전트 관리"],
          dependencies=[Depends(verify_client_key)])
async def reset_circuit(agent_name: str) -> dict[str, Any]:
    await ctx.health_monitor.reset_circuit_breaker(agent_name)
    return {"status": "reset", "agent_name": agent_name}


@app.post("/marketplace/install", tags=["에이전트 관리"], dependencies=[Depends(verify_admin_key)])
async def install_from_marketplace(body: SubmitMarketplaceInstallBody):
    """외부 마켓플레이스로부터 에이전트를 내려받아 빌드 및 등록합니다."""
    task_id = f"mkt-{str(uuid.uuid4())[:8]}"
    result = await ctx.marketplace.install_from_marketplace(body.item_url, task_id)
    if result.get("status") == "FAILED":
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ── 세션 엔드포인트 ────────────────────────────────────────────────────────────


@app.get("/sessions/{session_id}", tags=["세션"], dependencies=[Depends(verify_client_key)])
async def get_session(session_id: str) -> dict[str, Any]:
    state = await ctx.redis_client.hgetall(f"session:{session_id}:state")
    return {"session_id": session_id, "state": state}


@app.get("/sessions/{session_id}/history", tags=["세션"],
         dependencies=[Depends(verify_client_key)])
async def get_session_history(
    session_id: str,
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    history = await ctx.state_manager.get_session_history(session_id, limit=limit)
    return {"session_id": session_id, "count": len(history), "history": history}


@app.delete("/sessions/{session_id}", tags=["세션"], dependencies=[Depends(verify_client_key)])
async def delete_session(session_id: str) -> dict[str, Any]:
    await ctx.state_manager.delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}


# ── 사용자 프로필 ──────────────────────────────────────────────────────────────


@app.get("/users/{user_id}/tasks", tags=["사용자"],
         dependencies=[Depends(verify_client_key)])
async def get_user_tasks(
    user_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="PENDING|COMPLETED|FAILED 필터"),
) -> dict[str, Any]:
    """사용자의 작업 히스토리를 최신순으로 반환합니다."""
    tasks, total = await ctx.state_manager.get_user_task_history(
        user_id, limit=limit, offset=offset, status_filter=status
    )
    return {"total": total, "limit": limit, "offset": offset, "tasks": tasks}


@app.get("/users/{user_id}/profile", tags=["사용자"],
         dependencies=[Depends(verify_client_key)])
async def get_profile(user_id: str) -> dict[str, Any]:
    profile = await ctx.state_manager.get_user_profile(user_id)
    profile.pop("llm_keys", None)  # LLM API 키는 공개 프로필 응답에서 제외
    return profile


@app.put("/users/{user_id}/profile", tags=["사용자"],
         dependencies=[Depends(verify_client_key)])
async def update_profile(user_id: str, body: UpdateUserProfileBody) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.style_pref is not None:
        updates["style_pref"] = body.style_pref
    if not updates:
        raise HTTPException(status_code=400, detail="수정할 필드가 없습니다.")
    await ctx.state_manager.update_user_profile(user_id, updates)
    profile = await ctx.state_manager.get_user_profile(user_id)
    profile.pop("llm_keys", None)
    return profile


@app.put("/users/{user_id}/llm_keys/{provider_name}", tags=["사용자"],
         dependencies=[Depends(verify_client_key)])
async def update_llm_api_key(
    user_id: str,
    provider_name: str,
    body: LLMKeyUpdateBody
) -> dict[str, Any]:
    """
    사용자의 특정 LLM 제공업체 API 키를 설정하거나 업데이트합니다.
    """
    if provider_name not in SUPPORTED_LLM_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid LLM provider '{provider_name}'. Supported providers are: {', '.join(SUPPORTED_LLM_PROVIDERS)}"
        )

    if not body.api_key or not body.api_key.strip():
        raise HTTPException(status_code=400, detail="API key must be a non-empty string.")

    profile = await ctx.state_manager.get_user_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="User profile not found.")

    current_keys = profile.get("llm_keys") or {}
    current_keys[provider_name] = body.api_key

    await ctx.state_manager.update_user_profile(user_id, {"llm_keys": current_keys})

    return {"message": "LLM API key updated successfully."}


# ── 승인 API ──────────────────────────────────────────────────────────────────

_APPROVAL_META_PREFIX = "cassiopeia:approval_meta:"
_APPROVAL_QUEUE_PREFIX = "cassiopeia:approval:"


@app.get("/approval/{approval_id}", tags=["승인"],
         dependencies=[Depends(verify_client_key)])
async def get_approval(approval_id: str) -> dict[str, Any]:
    """대기 중인 승인 요청 상세 조회.

    UI는 이 엔드포인트를 폴링하거나 SSE로 notification을 받은 뒤 호출합니다.
    """
    meta = await ctx.redis_client.hgetall(f"{_APPROVAL_META_PREFIX}{approval_id}")
    if not meta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"승인 요청 '{approval_id}'을 찾을 수 없습니다.",
        )
    return {"approval_id": approval_id, **meta}


@app.post("/approval/{approval_id}/respond", tags=["승인"],
          dependencies=[Depends(verify_client_key)])
async def respond_approval(approval_id: str, body: ApprovalRespondBody) -> dict[str, Any]:
    """승인 요청에 응답합니다 (approve / reject).

    매니저의 request_user_approval()이 대기 중인 Redis 키에 결과를 push합니다.
    """
    meta = await ctx.redis_client.hgetall(f"{_APPROVAL_META_PREFIX}{approval_id}")
    if not meta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"승인 요청 '{approval_id}'을 찾을 수 없습니다.",
        )

    current_status = meta.get("status", "PENDING")
    if current_status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 처리된 승인 요청입니다 (상태: {current_status}).",
        )

    # 승인 결과를 매니저가 BLPOP 대기 중인 큐에 push
    decision = json.dumps({"action": body.action}, ensure_ascii=False)
    await ctx.redis_client.rpush(f"{_APPROVAL_QUEUE_PREFIX}{approval_id}", decision)

    # 메타 상태 업데이트
    new_status = "APPROVED" if body.action == "approve" else "REJECTED"
    await ctx.redis_client.hset(
        f"{_APPROVAL_META_PREFIX}{approval_id}", "status", new_status
    )

    return {
        "approval_id": approval_id,
        "action": body.action,
        "status": new_status,
    }


# ── 진입점 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Cassiopeia Agent Server")
    parser.add_argument(
        "--llm",
        type=str,
        choices=["gemini", "claude", "chatgpt", "local"],
        default="gemini",
        help="LLM Backend to use (default: gemini)"
    )
    args = parser.parse_args()
    
    os.environ["LLM_BACKEND"] = args.llm
    
    uvicorn.run("agents.cassiopeia_agent.main:app", host="0.0.0.0", port=49152, reload=False)
