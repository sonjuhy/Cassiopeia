"""
Network Endpoints Management API TDD 테스트 (GET /admin/endpoints, GET /endpoints, POST /admin/endpoints)
"""

import pytest
from httpx import ASGITransport, AsyncClient
from agents.cassiopeia_agent.main import app
import agents.cassiopeia_agent.auth as auth


@pytest.mark.asyncio
async def test_get_admin_endpoints_unauthorized():
    """관리자 키 없이 GET /admin/endpoints 호출 시 403 Forbidden 반환"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/admin/endpoints")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_admin_endpoints_success():
    """올바른 ADMIN_API_KEY와 함께 GET /admin/endpoints 호출 시 성공"""
    headers = {"X-API-Key": auth.ADMIN_API_KEY}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/admin/endpoints", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "endpoints" in data
    assert isinstance(data["endpoints"], list)
    assert len(data["endpoints"]) > 0

    first_ep = data["endpoints"][0]
    assert "path" in first_ep
    assert "method" in first_ep


@pytest.mark.asyncio
async def test_get_public_endpoints_alias():
    """GET /endpoints (공개 별칭) 호출 시 200 OK 및 엔드포인트 목록 반환"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/endpoints")

    assert response.status_code == 200
    data = response.json()
    assert "endpoints" in data


@pytest.mark.asyncio
async def test_create_admin_endpoint():
    """POST /admin/endpoints 신규 엔드포인트 등록"""
    headers = {"X-API-Key": auth.ADMIN_API_KEY}
    new_ep = {
        "path": "/api/v1/custom",
        "method": "POST",
        "target_service": "custom_service",
        "description": "Custom API Endpoint",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/admin/endpoints", json=new_ep, headers=headers)

    assert response.status_code == 201
    data = response.json()
    assert data["path"] == "/api/v1/custom"
    assert data["method"] == "POST"
    assert data["target_service"] == "custom_service"

