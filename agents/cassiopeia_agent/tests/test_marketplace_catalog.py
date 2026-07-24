"""
Marketplace Catalog API 엔드포인트 TDD 테스트 (GET /marketplace/agents, GET /marketplace/templates)
"""

import pytest
from httpx import ASGITransport, AsyncClient
from agents.cassiopeia_agent.main import app


@pytest.mark.asyncio
async def test_get_marketplace_agents():
    """GET /marketplace/agents 호출 시 마켓플레이스 에이전트 목록이 정상 반환되는지 테스트"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/marketplace/agents")

    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "agents" in data
    assert isinstance(data["agents"], list)
    assert len(data["agents"]) > 0

    first_agent = data["agents"][0]
    assert "id" in first_agent
    assert "name" in first_agent
    assert "description" in first_agent
    assert "pricing_type" in first_agent or "pricingType" in first_agent


@pytest.mark.asyncio
async def test_get_marketplace_templates():
    """GET /marketplace/templates 호출 시 마켓플레이스 템플릿 목록이 정상 반환되는지 테스트"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/marketplace/templates")

    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "templates" in data
    assert isinstance(data["templates"], list)
    assert len(data["templates"]) > 0

    first_template = data["templates"][0]
    assert "id" in first_template
    assert "title" in first_template
    assert "category" in first_template
    assert "description" in first_template
