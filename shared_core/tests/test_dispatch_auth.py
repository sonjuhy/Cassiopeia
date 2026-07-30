"""
[TDD] shared_core.dispatch_auth 단위 테스트

- sign_task: _hmac 필드 추가, 미설정 시 원본 반환
- verify_task: 유효/누락/불일치 서명 처리, 미설정 시 통과
- 정규화: requester.user_id 추출, 내용 변조 감지
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from shared_core.dispatch_auth import DispatchAuthError, sign_task, verify_task, sign_dispatch

_SECRET = "test-hmac-secret-32bytes-padding!!"

_TASK = {
    "task_id": "task-001",
    "session_id": "U1:C1",
    "requester": {"user_id": "U1", "channel_id": "C1"},
    "content": "파일 읽어줘",
    "source": "slack",
}


@pytest.fixture
def with_secret(monkeypatch):
    monkeypatch.setenv("DISPATCH_HMAC_SECRET", _SECRET)


@pytest.fixture
def without_secret(monkeypatch):
    monkeypatch.delenv("DISPATCH_HMAC_SECRET", raising=False)


class TestSignTask:
    def test_sign_adds_hmac_field(self, with_secret):
        signed = sign_task(_TASK)
        assert "_hmac" in signed

    def test_sign_does_not_mutate_original(self, with_secret):
        original = dict(_TASK)
        sign_task(_TASK)
        assert "_hmac" not in _TASK
        assert _TASK == original

    def test_sign_hmac_is_hex_string(self, with_secret):
        signed = sign_task(_TASK)
        assert isinstance(signed["_hmac"], str)
        int(signed["_hmac"], 16)  # 유효한 hex 여부

    def test_sign_without_secret_returns_unchanged(self, without_secret):
        signed = sign_task(_TASK)
        assert "_hmac" not in signed
        assert signed == _TASK

    def test_same_task_same_signature(self, with_secret):
        sig1 = sign_task(_TASK)["_hmac"]
        sig2 = sign_task(_TASK)["_hmac"]
        assert sig1 == sig2

    def test_different_content_different_signature(self, with_secret):
        t1 = {**_TASK, "content": "hello"}
        t2 = {**_TASK, "content": "world"}
        assert sign_task(t1)["_hmac"] != sign_task(t2)["_hmac"]

    def test_different_user_different_signature(self, with_secret):
        t1 = {**_TASK, "requester": {"user_id": "U1", "channel_id": "C1"}}
        t2 = {**_TASK, "requester": {"user_id": "U2", "channel_id": "C1"}}
        assert sign_task(t1)["_hmac"] != sign_task(t2)["_hmac"]


class TestVerifyTask:
    def test_valid_signature_passes(self, with_secret):
        signed = sign_task(_TASK)
        verify_task(signed)  # 예외 없어야 함

    def test_missing_signature_raises(self, with_secret):
        with pytest.raises(DispatchAuthError, match="누락"):
            verify_task(_TASK)

    def test_wrong_signature_raises(self, with_secret):
        signed = {**_TASK, "_hmac": "deadbeef" * 8}
        with pytest.raises(DispatchAuthError, match="검증 실패"):
            verify_task(signed)

    def test_tampered_content_raises(self, with_secret):
        signed = sign_task(_TASK)
        tampered = {**signed, "content": "악성 내용"}
        with pytest.raises(DispatchAuthError):
            verify_task(tampered)

    def test_tampered_user_id_raises(self, with_secret):
        signed = sign_task(_TASK)
        tampered = {**signed, "requester": {"user_id": "attacker", "channel_id": "C1"}}
        with pytest.raises(DispatchAuthError):
            verify_task(tampered)

    def test_verify_without_secret_always_passes(self, without_secret):
        verify_task(_TASK)  # 서명 없어도 통과
        verify_task({**_TASK, "_hmac": "garbage"})  # 잘못된 서명도 통과

    def test_extra_fields_do_not_affect_verification(self, with_secret):
        signed = sign_task(_TASK)
        # 서명 후 추가 필드 삽입 — 서명 대상이 아니므로 통과해야 함
        signed["callback_url"] = "https://example.com/hook"
        signed["thread_ts"] = "12345.6789"
        verify_task(signed)


class TestCanonical:
    def test_extracts_user_id_from_requester(self, with_secret):
        """requester 구조에서 user_id를 올바르게 추출해 서명해야 함."""
        task_with_requester = {
            "task_id": "t1",
            "session_id": "U99:C1",
            "requester": {"user_id": "U99", "channel_id": "C1"},
            "content": "test",
            "source": "slack",
        }
        signed = sign_task(task_with_requester)
        verify_task(signed)  # 예외 없어야 함

    def test_sign_and_verify_roundtrip_api_source(self, with_secret):
        """API 경로 태스크 (source=api) 도 서명/검증 가능해야 함."""
        api_task = {
            "task_id": "api-task-1",
            "session_id": "user123:None",
            "requester": {"user_id": "user123", "channel_id": "None"},
            "content": "조사해줘",
            "source": "api",
        }
        signed = sign_task(api_task)
        verify_task(signed)


class TestSignDispatch:
    def test_sign_dispatch_covers_all_fields(self, with_secret):
        dispatch = {
            "task_id": "dispatch-001",
            "version": "1.1",
            "params": {"code": "print(1)"},
            "metadata": {"requires_user_approval": False}
        }
        signed = sign_dispatch(dispatch)
        assert "_hmac" in signed
        assert signed["_hmac"] is not None

        # Verify using raw verification logic matching SDK
        body = {k: v for k, v in signed.items() if k != "_hmac"}
        import hmac
        import hashlib
        expected = hmac.new(
            _SECRET.encode(),
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode(),
            hashlib.sha256
        ).hexdigest()
        assert signed["_hmac"] == expected

    def test_sign_dispatch_without_secret_returns_unchanged(self, without_secret):
        dispatch = {"task_id": "dispatch-002", "version": "1.1"}
        signed = sign_dispatch(dispatch)
        assert "_hmac" not in signed
        assert signed == dispatch

