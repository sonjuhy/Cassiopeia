"""
인증 및 권한 관리 (AuthN/AuthZ) 모듈
FastAPI Security 기능을 활용해 API 키 검증을 수행합니다.
"""

import os
import secrets
from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

# 클라이언트와 관리자가 사용할 API 키 헤더 이름
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# 환경변수에서 키 로드 — 미설정 시 서비스 시작 불가
ADMIN_API_KEY: str = os.environ.get("ADMIN_API_KEY", "")
CLIENT_API_KEY: str = os.environ.get("CLIENT_API_KEY", "")

if not ADMIN_API_KEY:
    raise RuntimeError(
        "ADMIN_API_KEY 환경변수가 설정되지 않았습니다. "
        "python -m agents.cassiopeia_agent.main 을 실행하면 설정 마법사를 사용할 수 있습니다."
    )

if not CLIENT_API_KEY:
    raise RuntimeError(
        "CLIENT_API_KEY 환경변수가 설정되지 않았습니다. "
        "python -m agents.cassiopeia_agent.main 을 실행하면 설정 마법사를 사용할 수 있습니다."
    )


async def verify_admin_key(api_key: str | None = Security(API_KEY_HEADER)) -> None:
    """관리자 전용 API 접근 검증. (예: /admin/*)"""
    if not api_key or not secrets.compare_digest(api_key, ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한 인증에 실패했습니다. (유효하지 않은 X-API-Key)",
        )


async def verify_client_key(api_key: str | None = Security(API_KEY_HEADER)) -> None:
    """
    클라이언트 접근 검증. (예: /tasks, /dispatch 등 상태 변경 API)
    관리자 키를 가진 경우에도 통과를 허용합니다.
    """
    if api_key and secrets.compare_digest(api_key, ADMIN_API_KEY):
        return

    if not api_key or not secrets.compare_digest(api_key, CLIENT_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="클라이언트 권한 인증에 실패했습니다. (유효하지 않은 X-API-Key)",
        )


def is_admin(api_key: str | None) -> bool:
    """X-API-Key가 관리자 키인지 확인합니다."""
    return bool(api_key and secrets.compare_digest(api_key, ADMIN_API_KEY))
