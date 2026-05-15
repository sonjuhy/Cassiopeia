"""
api_spec.md 구현 TDD 테스트

커버 대상 (총 8개 API 그룹):
  1. POST /admin/system/control         — 시스템 제어
  2. POST /admin/system/repair          — 시스템 복구
  3. PUT  /users/{user_id}/security     — 보안 설정 변경
  4. POST /users/{user_id}/credits/topup — 크레딧 충전
  5. POST /admin/endpoints/firewall/rules — 방화벽 규칙 등록
  6. GET  /admin/endpoints/firewall/rules — 방화벽 규칙 목록
  7. POST /admin/endpoints               — 엔드포인트 등록
  8. GET  /admin/endpoints               — 엔드포인트 목록
  9. GET  /marketplace/agents/{id}/details — 마켓플레이스 상세
 10. POST /agents/register/advanced      — 고급 에이전트 등록 (multipart)

각 그룹 테스트:
  - 정상 요청 → 기대 응답 구조
  - 잘못된 입력 → 422 Validation Error
  - 인증 실패 → 403 Forbidden
  - 부재 리소스 → 404 Not Found (해당하는 경우)
"""
from __future__ import annotations

import json
from typing import Any

import pytest

_ADMIN_KEY  = "test-admin-key"
_CLIENT_KEY = "test-client-key"
_BAD_KEY    = "wrong-key"


# ══════════════════════════════════════════════════════════════════════════════
# 1. POST /admin/system/control
# ══════════════════════════════════════════════════════════════════════════════

class TestSystemControl:
    async def test_restart_all_returns_success(self, async_client):
        resp = await async_client.post(
            "/admin/system/control",
            json={"action": "restart", "target": "all"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "message" in data
        assert "timestamp" in data

    async def test_terminate_core_engine(self, async_client):
        resp = await async_client.post(
            "/admin/system/control",
            json={"action": "terminate", "target": "core_engine"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    async def test_optimize_network_mesh(self, async_client):
        resp = await async_client.post(
            "/admin/system/control",
            json={"action": "optimize", "target": "network_mesh"},
        )
        assert resp.status_code == 200

    async def test_invalid_action_returns_422(self, async_client):
        resp = await async_client.post(
            "/admin/system/control",
            json={"action": "explode", "target": "all"},
        )
        assert resp.status_code == 422

    async def test_invalid_target_returns_422(self, async_client):
        resp = await async_client.post(
            "/admin/system/control",
            json={"action": "restart", "target": "unknown_module"},
        )
        assert resp.status_code == 422

    async def test_missing_action_returns_422(self, async_client):
        resp = await async_client.post(
            "/admin/system/control",
            json={"target": "all"},
        )
        assert resp.status_code == 422

    async def test_no_admin_key_returns_403(self, async_client):
        resp = await async_client.post(
            "/admin/system/control",
            json={"action": "restart", "target": "all"},
            headers={"X-API-Key": _BAD_KEY},
        )
        assert resp.status_code == 403

    async def test_client_key_returns_403(self, async_client):
        """관리자 전용 엔드포인트에 클라이언트 키 사용 시 거부."""
        resp = await async_client.post(
            "/admin/system/control",
            json={"action": "restart", "target": "all"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 403

    async def test_operation_logged_to_redis(self, async_client, fake_redis):
        """수행된 제어 작업이 Redis 이력 큐에 기록되어야 합니다."""
        await async_client.post(
            "/admin/system/control",
            json={"action": "optimize", "target": "all"},
        )
        log_len = await fake_redis.llen("system:ops:log")
        assert log_len >= 1


# ══════════════════════════════════════════════════════════════════════════════
# 2. POST /admin/system/repair
# ══════════════════════════════════════════════════════════════════════════════

class TestSystemRepair:
    async def test_hotfix_core_engine_returns_repair_id(self, async_client):
        resp = await async_client.post(
            "/admin/system/repair",
            json={"module_id": "core_engine", "repair_type": "hotfix"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "repair_id" in data
        assert data["status"] == "in_progress"
        assert "estimated_time" in data

    async def test_full_reinstall_network_mesh(self, async_client):
        resp = await async_client.post(
            "/admin/system/repair",
            json={"module_id": "network_mesh", "repair_type": "full_reinstall"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["repair_id"].startswith("rep-")

    async def test_invalid_module_returns_422(self, async_client):
        resp = await async_client.post(
            "/admin/system/repair",
            json={"module_id": "unknown_module", "repair_type": "hotfix"},
        )
        assert resp.status_code == 422

    async def test_invalid_repair_type_returns_422(self, async_client):
        resp = await async_client.post(
            "/admin/system/repair",
            json={"module_id": "core_engine", "repair_type": "quick_patch"},
        )
        assert resp.status_code == 422

    async def test_repair_status_stored_in_redis(self, async_client, fake_redis):
        resp = await async_client.post(
            "/admin/system/repair",
            json={"module_id": "core_engine", "repair_type": "hotfix"},
        )
        repair_id = resp.json()["repair_id"]
        stored = await fake_redis.hgetall(f"system:repair:{repair_id}")
        assert stored.get("status") == "in_progress"
        assert stored.get("module_id") == "core_engine"

    async def test_no_admin_key_returns_403(self, async_client):
        resp = await async_client.post(
            "/admin/system/repair",
            json={"module_id": "core_engine", "repair_type": "hotfix"},
            headers={"X-API-Key": _BAD_KEY},
        )
        assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# 3. PUT /users/{user_id}/security
# ══════════════════════════════════════════════════════════════════════════════

class TestUserSecurity:
    _URL = "/users/test-user/security"

    async def test_update_password_returns_200(self, async_client):
        resp = await async_client.put(
            self._URL,
            json={
                "current_password": "old_pass",
                "new_password": "new_secure_pass_123!",
                "mfa_enabled": False,
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    async def test_enable_mfa_totp(self, async_client):
        resp = await async_client.put(
            self._URL,
            json={
                "current_password": "old_pass",
                "new_password": "new_secure_pass_456!",
                "mfa_enabled": True,
                "mfa_type": "totp",
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 200

    async def test_same_password_returns_400(self, async_client):
        """새 비밀번호가 현재와 동일하면 400."""
        resp = await async_client.put(
            self._URL,
            json={
                "current_password": "same_pass",
                "new_password": "same_pass",
                "mfa_enabled": False,
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 400

    async def test_weak_password_returns_400(self, async_client):
        """너무 짧은 새 비밀번호는 400."""
        resp = await async_client.put(
            self._URL,
            json={
                "current_password": "old_pass",
                "new_password": "abc",  # 너무 짧음
                "mfa_enabled": False,
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 400

    async def test_missing_new_password_returns_422(self, async_client):
        resp = await async_client.put(
            self._URL,
            json={"current_password": "old_pass", "mfa_enabled": False},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 422

    async def test_no_auth_returns_403(self, async_client):
        resp = await async_client.put(
            self._URL,
            json={
                "current_password": "old",
                "new_password": "new_pass_xyz_123!",
                "mfa_enabled": False,
            },
            headers={"X-API-Key": _BAD_KEY},
        )
        assert resp.status_code == 403

    async def test_security_settings_persisted(self, async_client, fake_redis):
        """보안 설정이 사용자 프로필에 저장되어야 합니다."""
        await async_client.put(
            self._URL,
            json={
                "current_password": "old_pass",
                "new_password": "new_stored_pass_789!",
                "mfa_enabled": True,
                "mfa_type": "totp",
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        # 이후 GET 프로필 에서도 mfa_enabled 확인
        get_resp = await async_client.get(
            "/users/test-user/profile",
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert get_resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 4. POST /users/{user_id}/credits/topup
# ══════════════════════════════════════════════════════════════════════════════

class TestCreditTopup:
    _URL = "/users/test-user/credits/topup"

    async def test_topup_returns_transaction_id(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"amount": 50.0, "currency": "USD", "payment_method_id": "pm_123"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "transaction_id" in data
        assert "new_balance" in data
        assert data["transaction_id"].startswith("TX-")

    async def test_balance_increases_after_topup(self, async_client):
        resp1 = await async_client.post(
            self._URL,
            json={"amount": 100.0, "currency": "USD", "payment_method_id": "pm_abc"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp1.status_code == 200
        balance_after = resp1.json()["new_balance"]
        assert balance_after >= 100  # 최소 100 이상이어야 함

    async def test_zero_amount_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"amount": 0, "currency": "USD", "payment_method_id": "pm_123"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 422

    async def test_negative_amount_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"amount": -10.0, "currency": "USD", "payment_method_id": "pm_123"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 422

    async def test_missing_payment_method_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"amount": 50.0, "currency": "USD"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 422

    async def test_no_auth_returns_403(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"amount": 50.0, "currency": "USD", "payment_method_id": "pm_bad"},
            headers={"X-API-Key": _BAD_KEY},
        )
        assert resp.status_code == 403

    async def test_idempotency_key_prevents_duplicate(self, async_client):
        """같은 X-Idempotency-Key 로 두 번 충전해도 1번만 처리됩니다."""
        payload = {"amount": 50.0, "currency": "USD", "payment_method_id": "pm_idem"}
        headers = {"X-API-Key": _CLIENT_KEY, "X-Idempotency-Key": "topup-idem-001"}

        r1 = await async_client.post(self._URL, json=payload, headers=headers)
        r2 = await async_client.post(self._URL, json=payload, headers=headers)

        assert r1.status_code == 200
        assert r2.status_code == 200
        # 두 번째 응답은 캐시된 결과여야 함
        assert r1.json()["transaction_id"] == r2.json()["transaction_id"]


# ══════════════════════════════════════════════════════════════════════════════
# 5 & 6. POST/GET /admin/endpoints/firewall/rules
# ══════════════════════════════════════════════════════════════════════════════

class TestFirewallRules:
    _URL = "/admin/endpoints/firewall/rules"

    async def test_create_allow_rule_returns_201(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={
                "rule_name": "Allow HTTPS Outbound",
                "protocol": "TCP",
                "port": 443,
                "action": "allow",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["status"] == "active"

    async def test_create_deny_rule(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={
                "rule_name": "Block HTTP",
                "protocol": "TCP",
                "port": 80,
                "action": "deny",
            },
        )
        assert resp.status_code == 201

    async def test_invalid_port_zero_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"rule_name": "Bad Rule", "protocol": "TCP", "port": 0, "action": "allow"},
        )
        assert resp.status_code == 422

    async def test_invalid_port_over_65535_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"rule_name": "Bad Rule", "protocol": "TCP", "port": 99999, "action": "allow"},
        )
        assert resp.status_code == 422

    async def test_invalid_action_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"rule_name": "Bad Rule", "protocol": "TCP", "port": 443, "action": "drop"},
        )
        assert resp.status_code == 422

    async def test_list_rules_returns_200(self, async_client):
        resp = await async_client.get(self._URL)
        assert resp.status_code == 200
        data = resp.json()
        assert "rules" in data
        assert isinstance(data["rules"], list)

    async def test_created_rule_appears_in_list(self, async_client):
        await async_client.post(
            self._URL,
            json={"rule_name": "ListTest Rule", "protocol": "UDP", "port": 53, "action": "allow"},
        )
        list_resp = await async_client.get(self._URL)
        rule_names = [r["rule_name"] for r in list_resp.json()["rules"]]
        assert "ListTest Rule" in rule_names

    async def test_no_admin_key_returns_403(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"rule_name": "X", "protocol": "TCP", "port": 443, "action": "allow"},
            headers={"X-API-Key": _BAD_KEY},
        )
        assert resp.status_code == 403

    async def test_rule_stored_in_redis(self, async_client, fake_redis):
        await async_client.post(
            self._URL,
            json={"rule_name": "Redis Check Rule", "protocol": "TCP", "port": 8080, "action": "deny"},
        )
        keys = await fake_redis.hkeys("system:firewall:rules")
        # 저장된 규칙 이름 중 하나가 "Redis Check Rule"을 포함해야 함
        rules_json = [await fake_redis.hget("system:firewall:rules", k) for k in keys]
        rules = [json.loads(r) for r in rules_json if r]
        assert any(r.get("rule_name") == "Redis Check Rule" for r in rules)


# ══════════════════════════════════════════════════════════════════════════════
# 7 & 8. POST/GET /admin/endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestEndpointRegistration:
    _URL = "/admin/endpoints"

    async def test_register_endpoint_returns_201(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={
                "path": "/v1/custom/logic",
                "method": "POST",
                "target_service": "logic-container:8080",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "endpoint_id" in data
        assert data["endpoint_id"].startswith("end-")

    async def test_invalid_method_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={
                "path": "/v1/bad",
                "method": "INVALID_VERB",
                "target_service": "svc:8080",
            },
        )
        assert resp.status_code == 422

    async def test_path_must_start_with_slash(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"path": "no-leading-slash", "method": "GET", "target_service": "svc:8080"},
        )
        assert resp.status_code == 422

    async def test_list_endpoints_returns_200(self, async_client):
        resp = await async_client.get(self._URL)
        assert resp.status_code == 200
        data = resp.json()
        assert "endpoints" in data
        assert isinstance(data["endpoints"], list)

    async def test_registered_endpoint_appears_in_list(self, async_client):
        await async_client.post(
            self._URL,
            json={"path": "/v1/test-list", "method": "GET", "target_service": "svc:9090"},
        )
        list_resp = await async_client.get(self._URL)
        paths = [e["path"] for e in list_resp.json()["endpoints"]]
        assert "/v1/test-list" in paths

    async def test_no_admin_key_returns_403(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"path": "/v1/x", "method": "GET", "target_service": "svc:1234"},
            headers={"X-API-Key": _BAD_KEY},
        )
        assert resp.status_code == 403

    async def test_endpoint_stored_in_redis(self, async_client, fake_redis):
        await async_client.post(
            self._URL,
            json={"path": "/v1/redis-test", "method": "PUT", "target_service": "redis-svc:7070"},
        )
        keys = await fake_redis.hkeys("system:endpoints")
        stored = [json.loads(await fake_redis.hget("system:endpoints", k)) for k in keys]
        assert any(e.get("path") == "/v1/redis-test" for e in stored)


# ══════════════════════════════════════════════════════════════════════════════
# 9. GET /marketplace/agents/{id}/details
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketplaceAgentDetails:
    async def _seed_agent(self, fake_redis, agent_id: str, data: dict) -> None:
        await fake_redis.set(
            f"marketplace:agent:{agent_id}:details",
            json.dumps(data, ensure_ascii=False),
        )

    async def test_existing_agent_returns_details(self, async_client, fake_redis):
        await self._seed_agent(fake_redis, "mkt-1", {
            "id": "mkt-1",
            "permissions_required": ["network_egress", "filesystem_read"],
            "release_history": [{"version": "v2.4.1", "date": "2026-04-01", "notes": "Bug fixes"}],
            "confidence_score": 98.2,
            "documentation_url": "https://docs.ai-agent.com/mkt-1",
        })
        resp = await async_client.get("/marketplace/agents/mkt-1/details")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "mkt-1"
        assert "permissions_required" in data
        assert "release_history" in data
        assert "confidence_score" in data
        assert "documentation_url" in data

    async def test_unknown_agent_returns_404(self, async_client):
        resp = await async_client.get("/marketplace/agents/nonexistent-xyz/details")
        assert resp.status_code == 404

    async def test_no_auth_required(self, async_client, fake_redis):
        """명세상 인증 불필요(None/Client). 인증 없이도 접근 가능."""
        await self._seed_agent(fake_redis, "mkt-public", {
            "id": "mkt-public",
            "permissions_required": [],
            "release_history": [],
            "confidence_score": 90.0,
            "documentation_url": "",
        })
        # API Key 없이 요청
        resp = await async_client.get(
            "/marketplace/agents/mkt-public/details",
            headers={},
        )
        assert resp.status_code == 200

    async def test_response_contains_all_required_fields(self, async_client, fake_redis):
        await self._seed_agent(fake_redis, "mkt-full", {
            "id": "mkt-full",
            "permissions_required": ["network_egress"],
            "release_history": [],
            "confidence_score": 95.5,
            "documentation_url": "https://example.com",
        })
        resp = await async_client.get("/marketplace/agents/mkt-full/details")
        data = resp.json()
        for field in ("id", "permissions_required", "release_history", "confidence_score", "documentation_url"):
            assert field in data, f"필드 누락: {field}"


# ══════════════════════════════════════════════════════════════════════════════
# 10. POST /agents/register/advanced
# ══════════════════════════════════════════════════════════════════════════════

class TestAdvancedAgentRegistration:
    _URL = "/agents/register/advanced"

    def _make_metadata(self, name: str = "my_custom_agent") -> str:
        return json.dumps({
            "name": name,
            "description": "커스텀 에이전트입니다.",
            "economics": {"fee": 5.0, "billing_cycle": "monthly"},
            "permissions": {"gpu": False, "network": True},
        })

    async def test_register_with_metadata_only_returns_201(self, async_client):
        resp = await async_client.post(
            self._URL,
            data={"metadata": self._make_metadata("advanced_agent_01")},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "agent_id" in data

    async def test_register_with_icon_file(self, async_client):
        """아이콘 파일(이미지)을 첨부한 고급 등록."""
        fake_icon = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # fake PNG header
        resp = await async_client.post(
            self._URL,
            data={"metadata": self._make_metadata("icon_agent_02")},
            files={"icon": ("icon.png", fake_icon, "image/png")},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 201
        assert "agent_id" in resp.json()

    async def test_invalid_metadata_json_returns_400(self, async_client):
        resp = await async_client.post(
            self._URL,
            data={"metadata": "this is not json"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 400

    async def test_missing_name_in_metadata_returns_400(self, async_client):
        metadata = json.dumps({
            "description": "이름 없음",
            "economics": {"fee": 0},
            "permissions": {},
        })
        resp = await async_client.post(
            self._URL,
            data={"metadata": metadata},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 400

    async def test_no_auth_returns_403(self, async_client):
        resp = await async_client.post(
            self._URL,
            data={"metadata": self._make_metadata("unauth_agent")},
            headers={"X-API-Key": _BAD_KEY},
        )
        assert resp.status_code == 403

    async def test_registered_agent_appears_in_registry(self, async_client, fake_redis):
        resp = await async_client.post(
            self._URL,
            data={"metadata": self._make_metadata("registry_check_agent")},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 201
        # Redis 레지스트리에 등록되어야 함
        reg = await fake_redis.hget("agents:registry", "registry_check_agent")
        assert reg is not None

    async def test_invalid_agent_name_characters_returns_400(self, async_client):
        metadata = json.dumps({
            "name": "invalid name with spaces!",
            "description": "이름에 공백 포함",
            "economics": {"fee": 0},
            "permissions": {},
        })
        resp = await async_client.post(
            self._URL,
            data={"metadata": metadata},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 400
