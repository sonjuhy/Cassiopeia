"""
build_llm_provider_from_config 테스트

- LLMConfig를 받아 올바른 공급자 인스턴스를 생성하는지 검증
- 지원하지 않는 backend는 ValueError 발생
- config의 model/api_key가 공급자로 전달되는지 확인
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from shared_core.llm.llm_config import LLMConfig
from shared_core.llm.factory import build_llm_provider_from_config


class TestBuildLLMProviderFromConfig:
    def test_gemini_backend_creates_gemini_provider(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        cfg = LLMConfig(backend="gemini")

        with patch("shared_core.llm.factory.build_llm_provider") as mock_build:
            mock_build.return_value = MagicMock()
            build_llm_provider_from_config(cfg)
            mock_build.assert_called_once_with(
                backend="gemini", model=None, api_key=None
            )

    def test_claude_backend_creates_claude_provider(self):
        cfg = LLMConfig(backend="claude", api_key="sk-test")

        with patch("shared_core.llm.factory.build_llm_provider") as mock_build:
            mock_build.return_value = MagicMock()
            build_llm_provider_from_config(cfg)
            mock_build.assert_called_once_with(
                backend="claude", model=None, api_key="sk-test"
            )

    def test_local_backend_creates_local_provider(self):
        cfg = LLMConfig(backend="local", model="llama3.2")

        with patch("shared_core.llm.factory.build_llm_provider") as mock_build:
            mock_build.return_value = MagicMock()
            build_llm_provider_from_config(cfg)
            mock_build.assert_called_once_with(
                backend="local", model="llama3.2", api_key=None
            )

    def test_openai_backend_creates_openai_provider(self):
        cfg = LLMConfig(backend="openai", api_key="openai-key")

        with patch("shared_core.llm.factory.build_llm_provider") as mock_build:
            mock_build.return_value = MagicMock()
            build_llm_provider_from_config(cfg)
            mock_build.assert_called_once_with(
                backend="openai", model=None, api_key="openai-key"
            )

    def test_unsupported_backend_raises_value_error(self):
        cfg = LLMConfig(backend="unknown-ai")
        with pytest.raises(ValueError, match="지원하지 않는"):
            build_llm_provider_from_config(cfg)

    def test_model_passed_through_to_factory(self):
        cfg = LLMConfig(backend="gemini", model="gemini-2.5-flash-lite")

        with patch("shared_core.llm.factory.build_llm_provider") as mock_build:
            mock_build.return_value = MagicMock()
            build_llm_provider_from_config(cfg)
            _, kwargs = mock_build.call_args
            assert kwargs["model"] == "gemini-2.5-flash-lite"

    def test_api_key_passed_through_to_factory(self):
        cfg = LLMConfig(backend="claude", api_key="custom-key")

        with patch("shared_core.llm.factory.build_llm_provider") as mock_build:
            mock_build.return_value = MagicMock()
            build_llm_provider_from_config(cfg)
            _, kwargs = mock_build.call_args
            assert kwargs["api_key"] == "custom-key"
