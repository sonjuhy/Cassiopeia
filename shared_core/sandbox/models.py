"""
Sandbox 공유 모델 (shared_core)

agents/sandbox_agent에 의존하지 않는 독립 스키마.
SandboxClient에서 사용합니다.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field

type SandboxRuntime = Literal["firecracker", "docker"]
type SandboxLanguage = Literal["python", "javascript", "bash", "typescript", "ruby", "go"]


class SandboxRequest(BaseModel):
    """격리 코드 실행 요청."""

    language: str
    code: str
    stdin: str = ""
    timeout: int = Field(default=30, ge=1, le=300)
    memory_mb: int = Field(default=256, ge=64, le=4096)
    env: dict[str, str] = Field(default_factory=dict)


class SandboxResult(TypedDict):
    """격리 코드 실행 결과."""

    stdout: str
    stderr: str
    exit_code: int
    runtime_used: SandboxRuntime
    execution_time_ms: int
