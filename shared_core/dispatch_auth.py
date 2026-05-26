"""
dispatch_auth — HMAC-SHA256 dispatch 메시지 서명/검증

서명 대상 필드: task_id, user_id, content, session_id, source
비밀키: DISPATCH_HMAC_SECRET 환경변수

DISPATCH_HMAC_SECRET 미설정 시:
- sign_task: 서명 없이 원본 반환 (하위 호환)
- verify_task: 검증 생략 (하위 호환)

설정 시:
- sign_task: _hmac 필드 추가
- verify_task: _hmac 불일치 또는 누락 → DispatchAuthError
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

_HMAC_FIELD = "_hmac"
_SIGNED_FIELDS = ("task_id", "user_id", "content", "session_id", "source")


class DispatchAuthError(ValueError):
    """HMAC 서명 검증 실패"""


def _secret() -> bytes | None:
    val = os.environ.get("DISPATCH_HMAC_SECRET", "")
    return val.encode() if val else None


def _canonical(task: dict[str, Any]) -> bytes:
    """서명 대상 정규화 — 고정 필드 순서, requester에서 user_id 추출"""
    user_id = (task.get("requester") or {}).get("user_id") or task.get("user_id", "")
    payload = {
        "task_id": task.get("task_id", ""),
        "user_id": user_id,
        "content": task.get("content", ""),
        "session_id": task.get("session_id", ""),
        "source": task.get("source", ""),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()


def sign_task(task: dict[str, Any]) -> dict[str, Any]:
    """
    DISPATCH_HMAC_SECRET가 설정된 경우 _hmac 필드를 추가한 새 dict을 반환합니다.
    미설정 시 원본을 그대로 반환합니다 (원본 변경 없음).
    """
    secret = _secret()
    if not secret:
        return task
    sig = hmac.new(secret, _canonical(task), hashlib.sha256).hexdigest()
    return {**task, _HMAC_FIELD: sig}


def verify_task(task: dict[str, Any]) -> None:
    """
    DISPATCH_HMAC_SECRET가 설정된 경우 서명을 검증합니다.

    Args:
        task: dispatch 메시지 딕셔너리 (_hmac 포함 가능).

    Raises:
        DispatchAuthError: 서명이 없거나 불일치할 때.
    """
    secret = _secret()
    if not secret:
        return

    sig = task.get(_HMAC_FIELD)
    if not sig:
        raise DispatchAuthError(
            "dispatch 메시지에 서명이 없습니다. (_hmac 필드 누락)"
        )

    task_body = {k: v for k, v in task.items() if k != _HMAC_FIELD}
    expected = hmac.new(secret, _canonical(task_body), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(sig, expected):
        raise DispatchAuthError(
            f"dispatch 메시지 서명 검증 실패. task_id={task.get('task_id')}"
        )


def sign_dispatch(dispatch: dict[str, Any]) -> dict[str, Any]:
    """SDK 호환용 전체 페이로드 서명 함수"""
    secret = _secret()
    if not secret:
        return dispatch
    body = {k: v for k, v in dispatch.items() if k != _HMAC_FIELD}
    sig = hmac.new(
        secret,
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode(),
        hashlib.sha256
    ).hexdigest()
    return {**dispatch, _HMAC_FIELD: sig}

