"""
System Telemetry & Control API 엔드포인트 TDD 테스트
"""

import pytest
from httpx import ASGITransport, AsyncClient
from agents.cassiopeia_agent.main import app
import agents.cassiopeia_agent.auth as auth


@pytest.mark.asyncio
async def test_get_system_usage_success():
    """GET /system/usage 호출 시 시스템 텔레메트리 정보가 올바르게 반환되는지 테스트"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/system/usage")

    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] in ["ONLINE", "DEGRADED", "OFFLINE"]
    assert "cpu" in data
    assert "percent" in data["cpu"]
    assert "cores" in data["cpu"]
    assert "memory" in data
    assert "percent" in data["memory"]
    assert "used_mb" in data["memory"]
    assert "total_mb" in data["memory"]
    assert "disk" in data
    assert "percent" in data["disk"]
    assert "agents" in data
    assert "running" in data["agents"]
    assert "total" in data["agents"]
    assert "uptime_seconds" in data


@pytest.mark.asyncio
async def test_control_system_unauthorized():
    """관리자 키 없이 POST /admin/system/control 호출 시 403 Forbidden 반환"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/admin/system/control",
            json={"action": "restart", "target": "core_engine"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_control_system_success():
    """올바른 ADMIN_API_KEY와 함께 POST /admin/system/control 호출 시 성공"""
    headers = {"X-API-Key": auth.ADMIN_API_KEY}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/admin/system/control",
            json={"action": "restart", "target": "core_engine"},
            headers=headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "restart" in data["message"]
    assert "core_engine" in data["message"]


@pytest.mark.asyncio
async def test_control_system_invalid_action():
    """허용되지 않은 action 전달 시 400 Bad Request 반환"""
    headers = {"X-API-Key": auth.ADMIN_API_KEY}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/admin/system/control",
            json={"action": "invalid_action", "target": "core_engine"},
            headers=headers,
        )

    assert response.status_code in [400, 422]

