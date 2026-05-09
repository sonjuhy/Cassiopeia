"""
Google Gemini API LLM 공급자.
"""

from __future__ import annotations

import logging
import os

from ..interfaces import LLMGenerateOptions, LLMUsage

logger = logging.getLogger("shared_core.llm.gemini")

_DEFAULT_MODEL = "gemini-2.5-flash-lite"
_DEFAULT_MAX_TOKENS = 1024


class GeminiProvider:
    """
    Google Gemini API를 사용하는 LLM 공급자.

    환경 변수:
        GEMINI_API_KEY: Google AI API 키 (필수)
        GEMINI_MODEL: 사용할 모델 (기본값: gemini-2.5-flash-lite)
    """

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from google import genai
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise ValueError("GEMINI_API_KEY가 제공되지 않았거나 환경변수가 설정되지 않았습니다.")
        self._client = genai.Client(api_key=self._api_key)
        self._model = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)

    async def generate_response(
        self,
        prompt: str,
        system_instruction: str | None = None,
        options: LLMGenerateOptions | None = None,
    ) -> tuple[str, LLMUsage]:
        from google.genai import types

        config_kwargs: dict = {
            "max_output_tokens": (
                options.max_tokens
                if options and options.max_tokens is not None
                else _DEFAULT_MAX_TOKENS
            ),
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if options and options.temperature is not None:
            config_kwargs["temperature"] = options.temperature

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        text = response.text or ""

        meta = getattr(response, "usage_metadata", None)
        if meta:
            usage = LLMUsage(
                prompt_tokens=getattr(meta, "prompt_token_count", 0) or 0,
                completion_tokens=getattr(meta, "candidates_token_count", 0) or 0,
                total_tokens=getattr(meta, "total_token_count", 0) or 0,
            )
        else:
            usage = LLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)

        logger.debug("[Gemini] tokens: %d total", usage.total_tokens)
        return text, usage

    async def validate(self) -> bool:
        try:
            await self._client.aio.models.generate_content(
                model=self._model,
                contents="hi",
            )
            logger.info("[Gemini] 연결 검증 성공")
            return True
        except Exception as e:
            logger.error("[Gemini] 연결 검증 실패: %s", e)
            raise
