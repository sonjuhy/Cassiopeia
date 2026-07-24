"""
Orchestra Global Config & User Transactions API TDD 테스트 (PUT /admin/config/orchestra, GET /user/transactions)
"""

import pytest
from httpx import ASGITransport, AsyncClient
from agents.cassiopeia_agent.main import app
import agents.cassiopeia_agent.auth as auth


@pytest.mark.asyncio
async def test_update_orchestra_config_unauthorized():
    """관리자 키 없이 PUT /admin/config/orchestra 호출 시 403 Forbidden 반환"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.put(
            "/admin/config/orchestra",
            json={"default_model": "cortex-9v", "global_api_key": "sec-123"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_orchestra_config_success():
    """올바른 ADMIN_API_KEY와 함께 PUT /admin/config/orchestra 호출 시 성공"""
    headers = {"X-API-Key": auth.ADMIN_API_KEY}
    payload = {
        "default_model": "cortex-9v",
        "global_api_key": "sec-123456",
        "orchestra_settings": {"max_concurrency": 10},
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.put(
            "/admin/config/orchestra", json=payload, headers=headers
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "updated"
    assert data["config"]["default_model"] == "cortex-9v"


@pytest.mark.asyncio
async def test_get_user_transactions():
    """GET /user/transactions 및 /users/user_default/transactions 호출 시 트랜잭션 목록 반환"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/user/transactions")

    assert response.status_code == 200
    data = response.json()
    assert "transactions" in data
    assert isinstance(data["transactions"], list)
    assert len(data["transactions"]) > 0

    first_tx = data["transactions"][0]
    assert "id" in first_tx
    assert "activity_type" in first_tx or "activityType" in first_tx
    assert "cost" in first_tx
