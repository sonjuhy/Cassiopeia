"""
Anthropic Claude API LLM 공급자.
"""

from __future__ import annotations

import logging
import os

from ..interfaces import LLMGenerateOptions, LLMUsage

logger = logging.getLogger("shared_core.llm.claude")

_DEFAULT_MODEL = "haiku4.5"
_DEFAULT_MAX_TOKENS = 1024


class ClaudeProvider:
    """
    Anthropic Claude API를 사용하는 LLM 공급자.

    환경 변수:
        ANTHROPIC_API_KEY: Anthropic API 키 (필수)
        CLAUDE_MODEL: 사용할 모델 (기본값: haiku4.5)
    """

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        import anthropic
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise ValueError("ANTHROPIC_API_KEY가 제공되지 않았거나 환경변수가 설정되지 않았습니다.")
        
        # 따옴표 제거
        self._api_key = self._api_key.strip("\"'")
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        
        raw_model = model or os.environ.get("CLAUDE_MODEL", _DEFAULT_MODEL)
        self._model = raw_model.strip("\"'")

    async def generate_response(
        self,
        prompt: str,
        system_instruction: str | None = None,
        options: LLMGenerateOptions | None = None,
    ) -> tuple[str, LLMUsage]:
        max_tokens = (
            options.max_tokens
            if options and options.max_tokens is not None
            else _DEFAULT_MAX_TOKENS
        )

        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_instruction:
            kwargs["system"] = system_instruction
        if options and options.temperature is not None:
            kwargs["temperature"] = options.temperature

        response = await self._client.messages.create(**kwargs)
        text = response.content[0].text if response.content else ""
        usage = LLMUsage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )
        logger.debug(
            "[Claude] tokens: %d prompt + %d completion",
            usage.prompt_tokens,
            usage.completion_tokens,
        )
        return text, usage

    async def validate(self) -> bool:
        try:
            await self._client.messages.create(
                model=self._model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            logger.info("[Claude] 연결 검증 성공")
            return True
        except Exception as e:
            logger.error("[Claude] 연결 검증 실패: %s", e)
            raise
