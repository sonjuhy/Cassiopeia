"""
LocalProvider 테스트  (shared_core/llm/providers/local.py)
- generate_response(): 정상 / HTTP 에러 / 연결 실패
- response_format=json_object 페이로드 포함 여부
- system_instruction 포함 여부
- options(max_tokens, temperature) 반영
- validate(): 성공 / HTTP 에러 / 연결 실패
- 환경변수 기반 설정(base_url, model, api_key)
"""
from __future__ import annotations

import json
import os
import pytest
import httpx
from pytest_httpx import HTTPXMock

from shared_core.llm.providers.local import LocalProvider

_BASE = "http://localhost:11434/v1"
_MODEL = "test-model"
_CHAT_URL = f"{_BASE}/chat/completions"
_MODELS_URL = f"{_BASE}/models"


@pytest.fixture
def provider():
    return LocalProvider(base_url=_BASE, model=_MODEL, api_key="test-key")


def _chat_resp(content: str, prompt: int = 10, completion: int = 20) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


# ── generate_response: 정상 ───────────────────────────────────────────────────

class TestGenerateResponseSuccess:
    async def test_returns_text_and_usage(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_CHAT_URL, method="POST",
            json=_chat_resp('{"result": "ok"}'),
        )
        text, usage = await provider.generate_response("테스트")
        assert text == '{"result": "ok"}'
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 20
        assert usage.total_tokens == 30

    async def test_payload_contains_response_format_json(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_CHAT_URL, method="POST", json=_chat_resp("{}"))
        await provider.generate_response("테스트")
        request = httpx_mock.get_requests()[0]
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}

    async def test_payload_contains_model(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_CHAT_URL, method="POST", json=_chat_resp("{}"))
        await provider.generate_response("테스트")
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload["model"] == _MODEL

    async def test_payload_stream_is_false(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_CHAT_URL, method="POST", json=_chat_resp("{}"))
        await provider.generate_response("테스트")
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload["stream"] is False

    async def test_system_instruction_added_as_system_message(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_CHAT_URL, method="POST", json=_chat_resp("{}"))
        await provider.generate_response("질문", system_instruction="당신은 도우미입니다")
        messages = json.loads(httpx_mock.get_requests()[0].content)["messages"]
        assert messages[0]["role"] == "system"
        assert "도우미" in messages[0]["content"]
        assert messages[1]["role"] == "user"

    async def test_no_system_instruction_only_user_message(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_CHAT_URL, method="POST", json=_chat_resp("{}"))
        await provider.generate_response("질문")
        messages = json.loads(httpx_mock.get_requests()[0].content)["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    async def test_options_max_tokens_applied(self, provider, httpx_mock: HTTPXMock):
        from shared_core.llm.interfaces import LLMGenerateOptions
        httpx_mock.add_response(url=_CHAT_URL, method="POST", json=_chat_resp("{}"))
        await provider.generate_response("질문", options=LLMGenerateOptions(max_tokens=512))
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload["max_tokens"] == 512

    async def test_options_temperature_applied(self, provider, httpx_mock: HTTPXMock):
        from shared_core.llm.interfaces import LLMGenerateOptions
        httpx_mock.add_response(url=_CHAT_URL, method="POST", json=_chat_resp("{}"))
        await provider.generate_response("질문", options=LLMGenerateOptions(temperature=0.2))
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload["temperature"] == pytest.approx(0.2)

    async def test_missing_usage_fields_default_to_zero(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_CHAT_URL, method="POST",
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )
        _, usage = await provider.generate_response("질문")
        assert usage.prompt_tokens == 0
        assert usage.total_tokens == 0


# ── generate_response: 오류 ───────────────────────────────────────────────────

class TestGenerateResponseErrors:
    async def test_raises_on_http_error(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_CHAT_URL, method="POST", status_code=500)
        with pytest.raises(httpx.HTTPStatusError):
            await provider.generate_response("테스트")

    async def test_raises_on_connection_error(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(
            httpx.ConnectError("refused"), url=_CHAT_URL,
        )
        with pytest.raises(httpx.ConnectError):
            await provider.generate_response("테스트")

    async def test_raises_on_401_unauthorized(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_CHAT_URL, method="POST", status_code=401)
        with pytest.raises(httpx.HTTPStatusError):
            await provider.generate_response("테스트")


# ── validate ─────────────────────────────────────────────────────────────────

class TestValidate:
    async def test_returns_true_on_200(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=_MODELS_URL, method="GET",
            json={"data": [{"id": _MODEL}]},
        )
        assert await provider.validate() is True

    async def test_returns_false_on_connection_error(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(
            httpx.ConnectError("refused"), url=_MODELS_URL,
        )
        with pytest.raises(httpx.ConnectError):
            await provider.validate()

    async def test_returns_false_on_http_error(self, provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=_MODELS_URL, method="GET", status_code=503)
        with pytest.raises(httpx.HTTPStatusError):
            await provider.validate()


# ── 환경변수 설정 ──────────────────────────────────────────────────────────────

class TestEnvConfig:
    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://custom-host:1234/v1")
        p = LocalProvider()
        assert "custom-host" in p._base_url

    def test_model_from_env(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_MODEL", "gemma3:4b")
        p = LocalProvider()
        assert p._model == "gemma3:4b"

    def test_explicit_args_override_env(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_MODEL", "env-model")
        p = LocalProvider(model="arg-model")
        assert p._model == "arg-model"

    def test_default_api_key_is_ollama(self, monkeypatch):
        monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)
        p = LocalProvider()
        assert p._api_key == "ollama"


from unittest.mock import patch

class TestGemma4e4bOptimization:
    @pytest.mark.asyncio
    @patch("shared_core.llm.providers.local.detect_hardware")
    async def test_applies_gemma_hardware_options(self, mock_detect, httpx_mock: HTTPXMock):
        from shared_core.llm.gemma_inference import OSPlatform, HardwareType
        mock_detect.return_value = (OSPlatform.MACOS, HardwareType.APPLE_SILICON)

        provider = LocalProvider(base_url="http://localhost/v1", model="gemma-4-e4b", api_key="test")
        httpx_mock.add_response(url="http://localhost/v1/chat/completions", method="POST", json=_chat_resp("{}"))

        await provider.generate_response("test")

        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert "options" in payload
        assert payload["options"]["num_gpu"] == -1
        assert payload["options"]["use_mmap"] is True
        assert payload["options"]["num_thread"] == 8


