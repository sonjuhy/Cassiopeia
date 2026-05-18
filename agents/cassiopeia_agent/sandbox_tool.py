"""
SandboxTool — 카시오페아 샌드박스 클라이언트 도구

운영 모드:
1. remote (기본): 상시 가동 중인 sandbox_agent (HTTP) 호출.
2. disabled: 샌드박스 비활성화.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from shared_core.sandbox.client import SandboxClient, SandboxError
from .app_context import ctx

logger = logging.getLogger("cassiopeia_agent.sandbox_tool")


def _detect_runtime() -> str:
    """환경변수에서 샌드박스 운영 모드를 결정합니다. (remote | disabled)"""
    if os.environ.get("SANDBOX_RUNTIME") == "disabled":
        return "disabled"
    return os.environ.get("SANDBOX_MODE", "remote").lower()


class SandboxTool:
    """sandbox_agent HTTP 서비스를 통해 코드를 실행합니다."""

    def __init__(self) -> None:
        self._mode = _detect_runtime()

        self._url = os.environ.get("SANDBOX_URL", "http://sandbox_agent:8003")
        raw_key = os.environ.get("SANDBOX_API_KEY")
        self._api_key = raw_key.strip("\"'") if raw_key else None

        self._http_client: SandboxClient | None = None
        if self._mode == "remote":
            self._http_client = SandboxClient(self._url, api_key=self._api_key)

    async def start(self) -> None:
        if self._mode == "disabled":
            logger.info("[SandboxTool] 비활성화 모드")
            return

        logger.info("[SandboxTool] 시작 (mode=%s)", self._mode)

        if not self._api_key:
            try:
                keys = await ctx.redis_client.hkeys("sandbox:keys")
                if keys:
                    self._api_key = keys[0]
                    self._http_client._api_key = self._api_key
                    logger.info("[SandboxTool] Redis에서 동적 API 키를 로드했습니다.")
                else:
                    logger.warning("[SandboxTool] 사용 가능한 Sandbox API 키가 없습니다.")
            except Exception as exc:
                logger.error("[SandboxTool] Redis 키 조회 실패: %s", exc)

        try:
            await self._http_client.health()
            logger.info("[SandboxTool] sandbox_agent 연결 확인됨")
        except Exception as exc:
            logger.warning("[SandboxTool] sandbox_agent 연결 확인 실패: %s", exc)

    async def execute_code(self, params: dict[str, Any]) -> dict[str, Any]:
        """sandbox_agent를 통해 코드를 실행합니다."""
        if self._mode == "disabled":
            raise RuntimeError("샌드박스 기능이 비활성화되어 있습니다.")

        if "language" not in params:
            raise ValueError("필수 파라미터 'language'가 없습니다.")
        if "code" not in params:
            raise ValueError("필수 파라미터 'code'가 없습니다.")

        try:
            result = await self._http_client.execute(
                language=params["language"],
                code=params["code"],
                stdin=params.get("stdin", ""),
                timeout=int(params.get("timeout", 30)),
                memory_mb=int(params.get("memory_mb", 256)),
                env=params.get("env", {}),
            )
            return dict(result)
        except SandboxError as exc:
            logger.error("[SandboxTool] 원격 실행 실패: %s", exc)
            raise RuntimeError(f"샌드박스 실행 오류: {exc}") from exc

    async def shutdown(self) -> None:
        pass

    def pool_stats(self) -> dict[str, Any]:
        return {"status": self._mode, "url": self._url if self._mode == "remote" else None}

    @property
    def runtime(self) -> str:
        return self._mode
