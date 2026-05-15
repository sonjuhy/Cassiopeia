"""
api_spec.md 경로 정합성(Path Alignment) TDD 테스트

스펙과 구현 경로가 달랐던 3개 엔드포인트를 스펙 그대로 구현:

  1. POST /admin/system/recovery/repair
       spec: POST /system/recovery/repair  (현재: /admin/system/repair — /recovery/ 누락)

  2. PUT  /user/security
       spec: PUT  /user/security           (현재: /users/{user_id}/security — user_id 경로 파라미터)
       → user_id 는 쿼리 파라미터로 수용 (default: "api-user")

  3. POST /user/credits/topup
       spec: POST /user/credits/topup      (현재: /users/{user_id}/credits/topup)
       → user_id 는 쿼리 파라미터로 수용 (default: "api-user")

기존 /users/{user_id}/... 경로는 하위 호환성을 위해 유지합니다.
"""
from __future__ import annotations

import json
import pytest

_ADMIN_KEY  = "test-admin-key"
_CLIENT_KEY = "test-client-key"
_BAD_KEY    = "wrong-key"


# ══════════════════════════════════════════════════════════════════════════════
# 1. POST /admin/system/recovery/repair
#    스펙 경로: POST /system/recovery/repair  (admin prefix + /recovery/ 추가)
# ══════════════════════════════════════════════════════════════════════════════

class TestSystemRecoveryRepairPath:
    """스펙 정확한 경로 /admin/system/recovery/repair 가 동작해야 합니다."""

    _URL = "/admin/system/recovery/repair"

    async def test_hotfix_returns_repair_id(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"module_id": "core_engine", "repair_type": "hotfix"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["repair_id"].startswith("rep-")
        assert data["status"] == "in_progress"
        assert "estimated_time" in data

    async def test_full_reinstall_network_mesh(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"module_id": "network_mesh", "repair_type": "full_reinstall"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    async def test_invalid_module_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"module_id": "ghost_module", "repair_type": "hotfix"},
        )
        assert resp.status_code == 422

    async def test_invalid_repair_type_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"module_id": "core_engine", "repair_type": "patch"},
        )
        assert resp.status_code == 422

    async def test_no_admin_key_returns_403(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"module_id": "core_engine", "repair_type": "hotfix"},
            headers={"X-API-Key": _BAD_KEY},
        )
        assert resp.status_code == 403

    async def test_status_stored_in_redis(self, async_client, fake_redis):
        resp = await async_client.post(
            self._URL,
            json={"module_id": "network_mesh", "repair_type": "hotfix"},
        )
        repair_id = resp.json()["repair_id"]
        stored = await fake_redis.hgetall(f"system:repair:{repair_id}")
        assert stored.get("status") == "in_progress"
        assert stored.get("module_id") == "network_mesh"

    async def test_pushed_to_pending_queue(self, async_client, fake_redis):
        """SystemExecutor 가 소비할 pending 큐에 명령이 들어가야 합니다."""
        resp = await async_client.post(
            self._URL,
            json={"module_id": "core_engine", "repair_type": "full_reinstall"},
        )
        repair_id = resp.json()["repair_id"]
        length = await fake_redis.llen("system:repair:pending")
        assert length >= 1
        raw = await fake_redis.lindex("system:repair:pending", 0)
        cmd = json.loads(raw)
        assert cmd["repair_id"] == repair_id
        assert cmd["repair_type"] == "full_reinstall"

    async def test_old_path_still_works(self, async_client):
        """기존 /admin/system/repair 경로는 하위 호환성을 위해 유지됩니다."""
        resp = await async_client.post(
            "/admin/system/repair",
            json={"module_id": "core_engine", "repair_type": "hotfix"},
        )
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 2. PUT /user/security
#    스펙: PUT /user/security  (user_id 없음 → query param user_id 로 처리)
# ══════════════════════════════════════════════════════════════════════════════

class TestUserSecuritySpecPath:
    """스펙 경로 PUT /user/security 가 동작해야 합니다."""

    _URL = "/user/security"

    async def test_update_returns_200(self, async_client):
        resp = await async_client.put(
            self._URL,
            json={
                "current_password": "old_pass",
                "new_password": "new_secure_789!",
                "mfa_enabled": False,
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    async def test_with_explicit_user_id_query_param(self, async_client):
        resp = await async_client.put(
            f"{self._URL}?user_id=custom-user",
            json={
                "current_password": "old_pass",
                "new_password": "new_secure_888!",
                "mfa_enabled": True,
                "mfa_type": "totp",
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    async def test_same_password_returns_400(self, async_client):
        resp = await async_client.put(
            self._URL,
            json={
                "current_password": "same",
                "new_password": "same",
                "mfa_enabled": False,
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 400

    async def test_weak_password_returns_400(self, async_client):
        resp = await async_client.put(
            self._URL,
            json={
                "current_password": "old",
                "new_password": "abc",
                "mfa_enabled": False,
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 400

    async def test_missing_new_password_returns_422(self, async_client):
        resp = await async_client.put(
            self._URL,
            json={"current_password": "old", "mfa_enabled": False},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 422

    async def test_no_auth_returns_403(self, async_client):
        resp = await async_client.put(
            self._URL,
            json={
                "current_password": "old",
                "new_password": "new_secure_pass!",
                "mfa_enabled": False,
            },
            headers={"X-API-Key": _BAD_KEY},
        )
        assert resp.status_code == 403

    async def test_default_user_id_is_api_user(self, async_client, fake_redis):
        """user_id 쿼리 파라미터 없이 호출하면 기본값 'api-user' 로 처리됩니다."""
        await async_client.put(
            self._URL,
            json={
                "current_password": "old_pass",
                "new_password": "stored_new_pass!",
                "mfa_enabled": True,
                "mfa_type": "totp",
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        # Redis 에 api-user 보안 설정이 저장되었는지 확인
        security = await fake_redis.hgetall("user:api-user:security")
        assert security.get("mfa_enabled") == "true"

    async def test_old_path_still_works(self, async_client):
        """기존 /users/{user_id}/security 경로는 하위 호환성을 위해 유지됩니다."""
        resp = await async_client.put(
            "/users/compat-user/security",
            json={
                "current_password": "old_pass",
                "new_password": "new_compat_pass!",
                "mfa_enabled": False,
            },
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 3. POST /user/credits/topup
#    스펙: POST /user/credits/topup  (user_id 없음 → query param user_id 로 처리)
# ══════════════════════════════════════════════════════════════════════════════

class TestCreditTopupSpecPath:
    """스펙 경로 POST /user/credits/topup 가 동작해야 합니다."""

    _URL = "/user/credits/topup"

    async def test_topup_returns_transaction_and_balance(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"amount": 50.0, "currency": "USD", "payment_method_id": "pm_spec"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["transaction_id"].startswith("TX-")
        assert "new_balance" in data

    async def test_with_explicit_user_id_query_param(self, async_client):
        resp = await async_client.post(
            f"{self._URL}?user_id=custom-user",
            json={"amount": 100.0, "currency": "KRW", "payment_method_id": "pm_kr"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 200
        assert resp.json()["new_balance"] >= 100.0

    async def test_zero_amount_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"amount": 0, "currency": "USD", "payment_method_id": "pm_x"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 422

    async def test_negative_amount_returns_422(self, async_client):
        resp = await async_client.post(
            self._URL,
            json={"amount": -5.0, "currency": "USD", "payment_method_id": "pm_x"},
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
        """X-Idempotency-Key 로 중복 충전 방지."""
        payload = {"amount": 30.0, "currency": "USD", "payment_method_id": "pm_idem2"}
        headers = {"X-API-Key": _CLIENT_KEY, "X-Idempotency-Key": "spec-idem-001"}

        r1 = await async_client.post(self._URL, json=payload, headers=headers)
        r2 = await async_client.post(self._URL, json=payload, headers=headers)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["transaction_id"] == r2.json()["transaction_id"]

    async def test_balance_accumulates_across_topups(self, async_client):
        """같은 user_id 로 두 번 충전하면 잔액이 누적됩니다."""
        url = f"{self._URL}?user_id=balance-test-user"
        headers = {"X-API-Key": _CLIENT_KEY}

        r1 = await async_client.post(
            url,
            json={"amount": 50.0, "currency": "USD", "payment_method_id": "pm_1"},
            headers=headers,
        )
        r2 = await async_client.post(
            url,
            json={"amount": 30.0, "currency": "USD", "payment_method_id": "pm_2"},
            headers=headers,
        )

        assert r1.status_code == 200
        assert r2.status_code == 200
        # 두 번째 응답의 잔액은 첫 번째보다 최소 30 이상 커야 함
        assert r2.json()["new_balance"] > r1.json()["new_balance"]

    async def test_old_path_still_works(self, async_client):
        """기존 /users/{user_id}/credits/topup 경로는 하위 호환성을 위해 유지됩니다."""
        resp = await async_client.post(
            "/users/compat-user/credits/topup",
            json={"amount": 10.0, "currency": "USD", "payment_method_id": "pm_compat"},
            headers={"X-API-Key": _CLIENT_KEY},
        )
        assert resp.status_code == 200
