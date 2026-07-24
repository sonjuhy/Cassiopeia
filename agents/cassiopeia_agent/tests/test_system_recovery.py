"""
System Recovery & Diagnostic API TDD 테스트 (GET /system/recovery/metrics, POST /admin/system/recovery/repair)
"""

import pytest
from httpx import ASGITransport, AsyncClient
from agents.cassiopeia_agent.main import app
import agents.cassiopeia_agent.auth as auth


@pytest.mark.asyncio
async def test_get_recovery_metrics_success():
    """GET /system/recovery/metrics 호출 시 복구 메트릭 정보가 올바르게 반환되는지 테스트"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/system/recovery/metrics")

    assert response.status_code == 200
    data = response.json()
    assert "active_threats" in data or "activeThreats" in data
    assert "latency_delta" in data or "latencyDelta" in data
    assert "integrity_score" in data or "integrityScore" in data


@pytest.mark.asyncio
async def test_system_recovery_repair_unauthorized():
    """관리자 키 없이 POST /admin/system/recovery/repair 호출 시 403 Forbidden 반환"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/admin/system/recovery/repair",
            json={"module_id": "core_engine", "repair_type": "hotfix"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_system_recovery_repair_success():
    """올바른 ADMIN_API_KEY와 함께 POST /admin/system/recovery/repair 호출 시 성공"""
    headers = {"X-API-Key": auth.ADMIN_API_KEY}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/admin/system/recovery/repair",
            json={"module_id": "core_engine", "repair_type": "hotfix"},
            headers=headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert "repair_id" in data
    assert data["status"] == "in_progress"
    assert "estimated_time" in data


@pytest.mark.asyncio
async def test_system_recovery_repair_invalid_payload():
    """유효하지 않은 module_id 전달 시 400 또는 422 반환"""
    headers = {"X-API-Key": auth.ADMIN_API_KEY}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/admin/system/recovery/repair",
            json={"module_id": "invalid_module", "repair_type": "invalid_type"},
            headers=headers,
        )

    assert response.status_code in [400, 422]
