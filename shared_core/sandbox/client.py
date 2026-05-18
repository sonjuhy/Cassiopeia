"""
SandboxClient — sandbox_agent HTTP API 래퍼

sandbox_agent의 POST /execute 엔드포인트를 호출하는 경량 비동기 클라이언트.
모든 에이전트에서 임포트해서 사용할 수 있습니다.

사용 예시:
    client = SandboxClient("http://sandbox-agent:8003")
    result = await client.execute("python", "print(42)")
    print(result["stdout"])  # "42\n"
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from .models import SandboxRequest, SandboxResult

logger = logging.getLogger("shared_core.sandbox.client")

_DEFAULT_SANDBOX_URL = "http://sandbox-agent:8003"


class SandboxError(Exception):
    """sandbox_agent 호출 실패 시 발생하는 예외."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SandboxClient:
    """
    sandbox_agent HTTP API 클라이언트.

    sandbox_agent 서비스의 POST /execute 엔드포인트를 비동기로 호출합니다.
    VMPool 관리, Firecracker/Docker 선택은 sandbox_agent가 담당하므로
    이 클라이언트는 단순히 요청을 전달하고 결과를 받습니다.

    Args:
        sandbox_url: sandbox_agent 베이스 URL (기본값: http://sandbox-agent:8003)
        http_timeout: HTTP 요청 타임아웃(초). 코드 실행 timeout보다 넉넉하게 설정하세요.
    """

    def __init__(
        self,
        sandbox_url: str = _DEFAULT_SANDBOX_URL,
        api_key: str | None = None,
        http_timeout: float = 330.0,
    ) -> None:
        self._url = sandbox_url.rstrip("/")
        self._api_key = api_key.strip("\"'") if api_key else None
        self._http_timeout = http_timeout

    async def execute(
        self,
        language: str,
        code: str,
        *,
        task_id: str | None = None,
        stdin: str = "",
        timeout: int = 30,
        memory_mb: int = 256,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """
        격리된 환경에서 코드를 실행하고 결과를 반환합니다.

        Args:
            language: 실행 언어 ("python", "javascript", "bash" 등)
            code: 실행할 코드 문자열
            task_id: 추적용 ID (미지정 시 UUID 자동 생성)
            stdin: 표준 입력
            timeout: 코드 실행 제한 시간(초), 최대 300
            memory_mb: 메모리 제한(MB), 최대 4096
            env: 추가 환경 변수

        Returns:
            SandboxResult: stdout, stderr, exit_code, runtime_used, execution_time_ms

        Raises:
            SandboxError: 실행 실패 또는 HTTP 오류 발생 시
        """
        req = SandboxRequest(
            language=language,
            code=code,
            stdin=stdin,
            timeout=timeout,
            memory_mb=memory_mb,
            env=env or {},
        )
        payload = {
            "task_id": task_id or str(uuid.uuid4()),
            "params": req.model_dump(),
        }

        headers = {}
        if self._api_key:
            headers["X-Sandbox-API-Key"] = self._api_key

        async with httpx.AsyncClient(timeout=self._http_timeout) as http:
            try:
                resp = await http.post(
                    f"{self._url}/execute",
                    json=payload,
                    headers=headers
                )
            except httpx.ConnectError as exc:
                raise SandboxError(
                    f"sandbox_agent에 연결할 수 없습니다: {self._url}"
                ) from exc
            except httpx.TimeoutException as exc:
                raise SandboxError(
                    f"sandbox_agent 응답 타임아웃 ({self._http_timeout}s)"
                ) from exc

        if resp.status_code >= 400:
            raise SandboxError(
                f"sandbox_agent 오류: {resp.text}", status_code=resp.status_code
            )

        body: dict[str, Any] = resp.json()

        if body.get("status") == "FAILED":
            error = body.get("error", {})
            raise SandboxError(
                f"코드 실행 실패 [{error.get('code')}]: {error.get('message')}"
            )

        result_data: dict[str, Any] = body.get("result_data", {})
        return SandboxResult(
            stdout=result_data.get("stdout", ""),
            stderr=result_data.get("stderr", ""),
            exit_code=result_data.get("exit_code", -1),
            runtime_used=result_data.get("runtime_used", "docker"),
            execution_time_ms=result_data.get("execution_time_ms", 0),
        )

    async def health(self) -> dict[str, Any]:
        """
        sandbox_agent 상태를 확인합니다.

        Returns:
            pool_stats, runtime, 태스크 수 등 상태 정보

        Raises:
            SandboxError: 연결 실패 시
        """
        async with httpx.AsyncClient(timeout=10.0) as http:
            try:
                resp = await http.get(f"{self._url}/health")
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise SandboxError(f"sandbox_agent health check 실패: {self._url}") from exc

        resp.raise_for_status()
        return resp.json()
