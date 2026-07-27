from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from pydantic import BaseModel, ConfigDict


class LLMUsage(BaseModel):
    """
    LLM 사용량 통계 정보입니다.
    """
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class LLMLogEntry(BaseModel):
    """
    LLM 요청 및 응답 로그 엔트리입니다.
    """
    model_config = ConfigDict(frozen=True)

    request_id: str
    model_name: str
    prompt: str
    response: str
    usage: LLMUsage
    timestamp: datetime = datetime.now()
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMGenerateOptions:
    """
    generate_response()에 전달하는 선택적 생성 파라미터.
    None 값은 공급자 기본값을 사용합니다.
    """
    max_tokens: int | None = None
    temperature: float | None = None


class LLMProviderProtocol(Protocol):
    """
    LLM 엔진과의 통신을 위한 인터페이스입니다.
    """

    async def generate_response(
        self,
        prompt: str,
        system_instruction: str | None = None,
        options: LLMGenerateOptions | None = None,
    ) -> tuple[str, LLMUsage]:
        """
        프롬프트를 전송하고 응답과 사용량을 반환합니다.

        Args:
            prompt: 사용자 프롬프트.
            system_instruction: 시스템 지침.
            options: 생성 파라미터 오버라이드 (max_tokens, temperature).

        Returns:
            응답 텍스트와 토큰 사용량 정보의 튜플.
        """
        ...

    async def validate(self) -> bool:
        """
        API 키 유효성 및 연결 상태를 검증합니다.

        Returns:
            연결 성공 시 True, 실패 시 False.
        """
        ...
