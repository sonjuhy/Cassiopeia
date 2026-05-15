import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.cassiopeia_agent.manager import CassiopeiaManager

@pytest.fixture
def mock_state():
    sm = AsyncMock()
    sm.get_agent_secrets = AsyncMock(return_value={})
    return sm

@pytest.fixture
def mock_health():
    return AsyncMock()

@pytest.fixture
def manager(mock_state, mock_health):
    m = CassiopeiaManager(
        redis_client=AsyncMock(),
        nlu_engine=AsyncMock(),
        state_manager=mock_state,
        health_monitor=mock_health
    )
    return m

@pytest.mark.asyncio
class TestManagerSecretInjection:
    async def test_inject_secrets_into_dispatch(self, manager, mock_state):
        """에이전트 태스크 디스패치 시 저장된 시크릿이 payload에 포함되는지 검증합니다."""
        agent_name = "schedule-agent"
        stored_secrets = {"GOOGLE_SERVICE_ACCOUNT_JSON": "fake-json"}
        mock_state.get_agent_secrets.return_value = stored_secrets
        
        # 원본 디스패치 메시지
        dispatch = {
            "action": "list_schedules",
            "params": {"date": "today"}
        }
        
        # 1. 시크릿 주입 로직 실행 (Manager 내부 메서드라 가정)
        enriched_dispatch = await manager._enrich_dispatch_with_secrets(agent_name, dispatch)
        
        # 2. 검증: params 내부에 credentials 필드가 생겼는지 확인
        assert "credentials" in enriched_dispatch["params"]
        assert enriched_dispatch["params"]["credentials"] == stored_secrets
        
    async def test_no_secrets_if_not_stored(self, manager, mock_state):
        """저장된 시크릿이 없을 경우 credentials 필드가 추가되지 않아야 합니다."""
        agent_name = "test-agent"
        mock_state.get_agent_secrets.return_value = {}
        
        dispatch = {"params": {}}
        enriched = await manager._enrich_dispatch_with_secrets(agent_name, dispatch)
        
        assert "credentials" not in enriched["params"]
