"""
OpenAI-호환 LLM 서버 공급자 (ChatGPT 용).
"""

from __future__ import annotations

import logging
import os

import httpx

from ..interfaces import LLMGenerateOptions, LLMUsage

logger = logging.getLogger("shared_core.llm.openai")

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o"
_DEFAULT_MAX_TOKENS = 1024
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 120.0


class OpenAIProvider:
    """
    OpenAI 공식 API를 사용하는 LLM 공급자.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("OPENAI_API_BASE", _DEFAULT_BASE_URL)
        ).strip("\"'").rstrip("/")
        
        raw_model = model or os.environ.get("OPENAI_API_MODEL", _DEFAULT_MODEL)
        self._model = raw_model.strip("\"'")
        
        raw_api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._api_key = raw_api_key.strip("\"'")
        
        self._endpoint = f"{self._base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

    def _build_payload(
        self,
        prompt: str,
        system_instruction: str | None,
        options: LLMGenerateOptions | None,
    ) -> dict:
        messages: list[dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": (
                options.max_tokens
                if options and options.max_tokens is not None
                else _DEFAULT_MAX_TOKENS
            ),
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        if options and options.temperature is not None:
            payload["temperature"] = options.temperature

        return payload

    async def generate_response(
        self,
        prompt: str,
        system_instruction: str | None = None,
        options: LLMGenerateOptions | None = None,
    ) -> tuple[str, LLMUsage]:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
            
        payload = self._build_payload(prompt, system_instruction, options)
        timeout = httpx.Timeout(
            connect=_CONNECT_TIMEOUT,
            read=_READ_TIMEOUT,
            write=10.0,
            pool=5.0,
        )

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self._endpoint,
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        text: str = data["choices"][0]["message"]["content"]
        raw_usage = data.get("usage", {})
        usage = LLMUsage(
            prompt_tokens=raw_usage.get("prompt_tokens", 0),
            completion_tokens=raw_usage.get("completion_tokens", 0),
            total_tokens=raw_usage.get("total_tokens", 0),
        )
        logger.debug("[OpenAI/%s] tokens: %d total", self._model, usage.total_tokens)
        return text, usage

    async def validate(self) -> bool:
        """
        API 키가 설정되어 있는지 기본적으로 확인합니다.
        """
        if not self._api_key:
            logger.error("[OpenAI] 연결 검증 실패: OPENAI_API_KEY 누락")
            return False
        return True
