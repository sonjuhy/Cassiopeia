"""
auth.py 테스트 스위트

커버리지 목표:
  - verify_admin_key: 유효 / 무효 / None → HTTPException 403
  - verify_client_key: 클라이언트 키 / 관리자 키(동등 허용) / 무효 / None
  - 환경변수 미설정 시 RuntimeError 발생
"""
from __future__ import annotations

import importlib
import os

import pytest
from fastapi import HTTPException

# conftest.py 에서 ADMIN_API_KEY / CLIENT_API_KEY 가 테스트 값으로 패치됩니다.
_ADMIN_KEY  = "test-admin-key"
_CLIENT_KEY = "test-client-key"
_BAD_KEY    = "totally-wrong-key"


# ── verify_admin_key ──────────────────────────────────────────────────────────

class TestVerifyAdminKey:
    async def test_valid_admin_key_passes(self, monkeypatch):
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        # 예외가 발생하지 않아야 함
        await auth.verify_admin_key(api_key=_ADMIN_KEY)

    async def test_invalid_key_raises_403(self, monkeypatch):
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        with pytest.raises(HTTPException) as exc_info:
            await auth.verify_admin_key(api_key=_BAD_KEY)
        assert exc_info.value.status_code == 403

    async def test_none_key_raises_403(self, monkeypatch):
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        with pytest.raises(HTTPException) as exc_info:
            await auth.verify_admin_key(api_key=None)
        assert exc_info.value.status_code == 403

    async def test_empty_string_raises_403(self, monkeypatch):
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        with pytest.raises(HTTPException) as exc_info:
            await auth.verify_admin_key(api_key="")
        assert exc_info.value.status_code == 403

    async def test_client_key_rejected_for_admin_endpoint(self, monkeypatch):
        """클라이언트 키로는 관리자 전용 엔드포인트에 접근할 수 없어야 합니다."""
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        monkeypatch.setattr(auth, "CLIENT_API_KEY", _CLIENT_KEY)
        with pytest.raises(HTTPException) as exc_info:
            await auth.verify_admin_key(api_key=_CLIENT_KEY)
        assert exc_info.value.status_code == 403

    async def test_error_detail_contains_message(self, monkeypatch):
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        with pytest.raises(HTTPException) as exc_info:
            await auth.verify_admin_key(api_key=_BAD_KEY)
        assert exc_info.value.detail  # detail 이 비어 있지 않아야 함


# ── verify_client_key ─────────────────────────────────────────────────────────

class TestVerifyClientKey:
    async def test_valid_client_key_passes(self, monkeypatch):
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        monkeypatch.setattr(auth, "CLIENT_API_KEY", _CLIENT_KEY)
        await auth.verify_client_key(api_key=_CLIENT_KEY)

    async def test_admin_key_also_passes_client_endpoint(self, monkeypatch):
        """관리자 키는 클라이언트 엔드포인트에도 접근 가능해야 합니다."""
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        monkeypatch.setattr(auth, "CLIENT_API_KEY", _CLIENT_KEY)
        await auth.verify_client_key(api_key=_ADMIN_KEY)

    async def test_invalid_key_raises_403(self, monkeypatch):
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        monkeypatch.setattr(auth, "CLIENT_API_KEY", _CLIENT_KEY)
        with pytest.raises(HTTPException) as exc_info:
            await auth.verify_client_key(api_key=_BAD_KEY)
        assert exc_info.value.status_code == 403

    async def test_none_raises_403(self, monkeypatch):
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        monkeypatch.setattr(auth, "CLIENT_API_KEY", _CLIENT_KEY)
        with pytest.raises(HTTPException) as exc_info:
            await auth.verify_client_key(api_key=None)
        assert exc_info.value.status_code == 403

    async def test_empty_string_raises_403(self, monkeypatch):
        import agents.cassiopeia_agent.auth as auth
        monkeypatch.setattr(auth, "ADMIN_API_KEY", _ADMIN_KEY)
        monkeypatch.setattr(auth, "CLIENT_API_KEY", _CLIENT_KEY)
        with pytest.raises(HTTPException) as exc_info:
            await auth.verify_client_key(api_key="")
        assert exc_info.value.status_code == 403


# ── 환경변수 미설정 RuntimeError ──────────────────────────────────────────────

class TestMissingEnvVarRuntimeError:
    def test_missing_admin_key_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "")
        monkeypatch.setenv("CLIENT_API_KEY", "some-client-key")
        with pytest.raises(RuntimeError, match="ADMIN_API_KEY"):
            import agents.cassiopeia_agent.auth as auth_module
            importlib.reload(auth_module)

    def test_missing_client_key_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "some-admin-key")
        monkeypatch.setenv("CLIENT_API_KEY", "")
        with pytest.raises(RuntimeError, match="CLIENT_API_KEY"):
            import agents.cassiopeia_agent.auth as auth_module
            importlib.reload(auth_module)

    def test_quoted_keys_are_stripped(self, monkeypatch):
        """따옴표로 감싼 환경변수 값은 정상적으로 처리되어야 합니다."""
        monkeypatch.setenv("ADMIN_API_KEY", '"quoted-admin-key"')
        monkeypatch.setenv("CLIENT_API_KEY", "'quoted-client-key'")
        import agents.cassiopeia_agent.auth as auth_module
        importlib.reload(auth_module)
        # 따옴표가 제거된 후 런타임 오류 없이 로드되어야 함
        assert auth_module.ADMIN_API_KEY == "quoted-admin-key"
        assert auth_module.CLIENT_API_KEY == "quoted-client-key"
