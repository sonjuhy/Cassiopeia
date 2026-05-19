"""
공유 pytest 픽스처
- fake_redis: in-memory Redis (fakeredis)
- mock_llm_provider: AsyncMock LLM 공급자
- health_monitor / state_manager / nlu_engine: 의존성 주입된 인스턴스
- async_client: FastAPI TestClient (lifespan 교체)
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
import pytest_asyncio

os.environ.setdefault("LLM_BACKEND", "gemini")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("CLIENT_API_KEY", "test-client-key")
# Fernet 키: 테스트 전용 고정값 (URL-safe base64, cryptography.fernet.Fernet 규격)
os.environ.setdefault("ENCRYPTION_KEY", "sjbWLtj1X4WskngsFoQj-21Bx37TgszKXX0b2vlQhHY=")

_TEST_ADMIN_KEY = "test-admin-key"
_TEST_CLIENT_KEY = "test-client-key"


# main.py에서 load_dotenv(override=True)가 .env 값으로 덮어쓰는 것을 방지하기 위해
# auth 모듈의 키 변수를 테스트 키로 고정합니다.
@pytest.fixture(autouse=True)
def _patch_auth_keys(monkeypatch):
    try:
        import agents.cassiopeia_agent.auth as auth_module
        monkeypatch.setattr(auth_module, "ADMIN_API_KEY", _TEST_ADMIN_KEY)
        monkeypatch.setattr(auth_module, "CLIENT_API_KEY", _TEST_CLIENT_KEY)
    except Exception:
        pass


# ── Redis ────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def fake_redis():
    server = fakeredis.FakeServer()
    r = fakeredis.FakeAsyncRedis(decode_responses=True, server=server)
    yield r
    await r.aclose()


# ── LLM Provider mock ────────────────────────────────────────────────────────

def _make_single_nlu_json(
    intent: str = "파일 조회",
    agent: str = "file_agent",
    action: str = "read_file",
    confidence: float = 0.9,
) -> str:
    return json.dumps({
        "type": "single",
        "intent": intent,
        "selected_agent": agent,
        "action": action,
        "params": {"path": "/tmp/test.txt"},
        "metadata": {
            "reason": "테스트",
            "confidence_score": confidence,
            "requires_user_approval": False,
        },
    }, ensure_ascii=False)


@pytest.fixture
def mock_llm_provider():
    provider = AsyncMock()
    provider.validate = AsyncMock(return_value=True)
    from shared_core.llm.interfaces import LLMUsage
    provider.generate_response = AsyncMock(
        return_value=(_make_single_nlu_json(), LLMUsage(prompt_tokens=10, completion_tokens=50, total_tokens=60))
    )
    return provider


@pytest.fixture
def single_nlu_json():
    return _make_single_nlu_json


# ── HealthMonitor ────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def health_monitor(fake_redis):
    from agents.cassiopeia_agent.health_monitor import HealthMonitor
    return HealthMonitor(redis_client=fake_redis)


# ── StateManager ─────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def state_manager(fake_redis, tmp_path):
    db_path = str(tmp_path / "test.db")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_PATH", db_path)
        from agents.cassiopeia_agent.state_manager import StateManager
        sm = StateManager(redis_client=fake_redis)
        yield sm
        await sm.close()


# ── CassiopeiaManager ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_cassiopeia():
    from unittest.mock import AsyncMock, MagicMock
    c = MagicMock()
    c.connect = AsyncMock()
    c.disconnect = AsyncMock()
    c.send_message = AsyncMock(return_value=True)
    c.listen = MagicMock()
    return c


@pytest_asyncio.fixture
async def manager(fake_redis, state_manager, health_monitor, mock_cassiopeia):
    from agents.cassiopeia_agent.manager import CassiopeiaManager
    return CassiopeiaManager(
        redis_client=fake_redis,
        state_manager=state_manager,
        health_monitor=health_monitor,
        cassiopeia=mock_cassiopeia,
    )


# ── FastAPI TestClient ────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def async_client(fake_redis, tmp_path):
    import httpx
    from agents.cassiopeia_agent.main import app
    from agents.cassiopeia_agent import app_context
    from agents.cassiopeia_agent.health_monitor import HealthMonitor
    from agents.cassiopeia_agent.manager import CassiopeiaManager
    from agents.cassiopeia_agent.state_manager import StateManager
    from agents.cassiopeia_agent.agent_builder_handler import AgentBuilderHandler
    from agents.cassiopeia_agent.registry import AgentRegistry
    from agents.cassiopeia_agent.marketplace_handler import MarketplaceHandler

    db_path = str(tmp_path / "test_app.db")

    from unittest.mock import AsyncMock, MagicMock
    mock_cass = MagicMock()
    mock_cass.connect = AsyncMock()
    mock_cass.disconnect = AsyncMock()
    mock_cass.send_message = AsyncMock(return_value=True)
    mock_cass.listen = MagicMock()

    sm = StateManager(redis_client=fake_redis)
    sm._db_path = db_path
    hm = HealthMonitor(redis_client=fake_redis)
    mgr = CassiopeiaManager(
        redis_client=fake_redis,
        state_manager=sm,
        health_monitor=hm,
        cassiopeia=mock_cass,
    )

    # ctx를 직접 설정 (lifespan 우회)
    app_context.ctx.redis_client = fake_redis
    app_context.ctx.state_manager = sm
    app_context.ctx.health_monitor = hm
    app_context.ctx.manager = mgr
    app_context.ctx.builder_handler = AgentBuilderHandler()
    app_context.ctx.registry = AgentRegistry()
    app_context.ctx.marketplace = MarketplaceHandler(
        app_context.ctx.builder_handler,
        app_context.ctx.registry,
        app_context.ctx.health_monitor,
    )
    app_context.ctx.cassiopeia_client = mock_cass
    app_context.ctx.listen_task = None
    app_context.ctx.monitor_task = None

    # 원본 lifespan이 ctx를 덮어쓰지 않도록 no-op으로 교체
    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"X-API-Key": "test-admin-key"}) as client:
            # state_manager를 엔드포인트 테스트에서 접근할 수 있도록 노출
            client.state_manager = sm  # type: ignore[attr-defined]
            yield client
    finally:
        app.router.lifespan_context = original_lifespan
        await sm.close()
