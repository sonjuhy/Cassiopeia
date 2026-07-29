"""
Sandbox 프로토콜 인터페이스 (Python 3.12+)
- 모든 인터페이스는 typing.Protocol로 정의 (ABC 미사용)
- 구현체: FirecrackerSandbox, DockerSandbox
"""

from __future__ import annotations

from typing import Protocol

from .models import ExecuteRequest, SandboxTaskResult


class SandboxProtocol(Protocol):
    """단일 샌드박스 실행 인터페이스 (Firecracker/Docker 공통)."""

    vm_id: str

    async def execute(self, req: ExecuteRequest) -> SandboxTaskResult:
        """격리된 환경에서 코드를 실행하고 결과를 반환합니다."""
        ...

    async def close(self) -> None:
        """샌드박스 자원(프로세스, 소켓, 네트워크)을 정리합니다."""
        ...
