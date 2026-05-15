import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from agents.cassiopeia_agent.main import app, ctx

@pytest.fixture
def api_client():
    # Verify client key bypass (since we use internal mock for ctx)
    with TestClient(app) as client:
        yield client

@pytest.mark.asyncio
class TestSecretAdminAPI:
    async def test_save_secrets_api(self, api_client):
        """Admin API를 통한 시크릿 저장 기능을 검증합니다."""
        # 1. Mocks
        ctx.state_manager = AsyncMock()
        
        from agents.cassiopeia_agent.auth import ADMIN_API_KEY

        # 2. API Call
        resp = api_client.post(
            "/admin/secrets/test-agent",
            json={"API_KEY": "secret-value"},
            headers={"X-API-Key": ADMIN_API_KEY}
        )
        
        # 3. Verify
        assert resp.status_code == 200
        ctx.state_manager.save_agent_secrets.assert_called_once_with(
            "test-agent", {"API_KEY": "secret-value"}
        )

    async def test_get_secrets_api(self, api_client):
        """Admin API를 통한 시크릿 조회 기능을 검증합니다."""
        ctx.state_manager = AsyncMock()
        ctx.state_manager.get_agent_secrets.return_value = {"KEY": "VAL"}
        
        from agents.cassiopeia_agent.auth import ADMIN_API_KEY
        resp = api_client.get(
            "/admin/secrets/test-agent",
            headers={"X-API-Key": ADMIN_API_KEY}
        )
        
        assert resp.status_code == 200
        assert resp.json() == {"KEY": "VAL"}
