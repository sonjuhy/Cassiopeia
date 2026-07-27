"""
shared_core.sandbox — 격리 코드 실행 유틸리티

모든 에이전트에서 사용 가능한 sandbox_agent HTTP 클라이언트.

주요 컴포넌트:
    SandboxClient  — sandbox_agent REST API 직접 호출 클라이언트
    SandboxError   — 실행 실패 예외
    SandboxRequest — 실행 요청 모델
    SandboxResult  — 실행 결과 TypedDict
"""

from .client import SandboxClient, SandboxError
from .models import SandboxRequest, SandboxResult

__all__ = [
    "SandboxClient",
    "SandboxError",
    "SandboxRequest",
    "SandboxResult",
]
