"""
LLMConfig 및 load_llm_config_for_agent 테스트

- LLMConfig 기본값 검증
- 에이전트별 전용 환경변수 우선 적용
- 전역 LLM_BACKEND 폴백
- 최종 기본값 "gemini"
- 모델/API키 환경변수 해석
- dispatch 메시지에서 per-call 설정 추출
"""
from __future__ import annotations

import pytest

from shared_core.llm.llm_config import (
    LLMConfig,
    load_llm_config_for_agent,
    llm_config_from_dispatch,
)


# ── LLMConfig dataclass ────────────────────────────────────────────────────────

class TestLLMConfig:
    def test_default_backend_is_gemini(self):
        cfg = LLMConfig()
        assert cfg.backend == "gemini"

    def test_default_model_is_none(self):
        cfg = LLMConfig()
        assert cfg.model is None

    def test_default_api_key_is_none(self):
        cfg = LLMConfig()
        assert cfg.api_key is None

    def test_explicit_backend(self):
        cfg = LLMConfig(backend="claude")
        assert cfg.backend == "claude"

    def test_explicit_model(self):
        cfg = LLMConfig(backend="claude", model="haiku4.5")
        assert cfg.model == "haiku4.5"

    def test_explicit_api_key(self):
        cfg = LLMConfig(backend="claude", api_key="sk-test")
        assert cfg.api_key == "sk-test"

    def test_is_frozen(self):
        cfg = LLMConfig(backend="gemini")
        with pytest.raises((AttributeError, TypeError)):
            cfg.backend = "claude"  # type: ignore[misc]


# ── load_llm_config_for_agent ─────────────────────────────────────────────────

class TestLoadLLMConfigForAgent:
    def test_agent_specific_env_var_takes_priority(self, monkeypatch):
        monkeypatch.setenv("ARCHIVE_AGENT_LLM_BACKEND", "claude")
        monkeypatch.setenv("LLM_BACKEND", "gemini")
        cfg = load_llm_config_for_agent("archive_agent")
        assert cfg.backend == "claude"

    def test_falls_back_to_global_llm_backend(self, monkeypatch):
        monkeypatch.delenv("ARCHIVE_AGENT_LLM_BACKEND", raising=False)
        monkeypatch.setenv("LLM_BACKEND", "local")
        cfg = load_llm_config_for_agent("archive_agent")
        assert cfg.backend == "local"

    def test_defaults_to_gemini_when_no_env(self, monkeypatch):
        monkeypatch.delenv("ARCHIVE_AGENT_LLM_BACKEND", raising=False)
        monkeypatch.delenv("LLM_BACKEND", raising=False)
        cfg = load_llm_config_for_agent("archive_agent")
        assert cfg.backend == "gemini"

    def test_agent_name_with_hyphen_normalized(self, monkeypatch):
        monkeypatch.setenv("RESEARCH_AGENT_LLM_BACKEND", "claude")
        cfg = load_llm_config_for_agent("research-agent")
        assert cfg.backend == "claude"

    def test_agent_name_already_uppercase_normalized(self, monkeypatch):
        monkeypatch.setenv("FILE_AGENT_LLM_BACKEND", "local")
        cfg = load_llm_config_for_agent("file-agent")
        assert cfg.backend == "local"

    def test_reads_model_from_agent_specific_env(self, monkeypatch):
        monkeypatch.setenv("ARCHIVE_AGENT_LLM_BACKEND", "claude")
        monkeypatch.setenv("ARCHIVE_AGENT_LLM_MODEL", "claude-sonnet-4-6")
        cfg = load_llm_config_for_agent("archive_agent")
        assert cfg.model == "claude-sonnet-4-6"

    def test_model_is_none_when_not_set(self, monkeypatch):
        monkeypatch.delenv("ARCHIVE_AGENT_LLM_MODEL", raising=False)
        cfg = load_llm_config_for_agent("archive_agent")
        assert cfg.model is None

    def test_reads_api_key_from_agent_specific_env(self, monkeypatch):
        monkeypatch.setenv("ARCHIVE_AGENT_LLM_BACKEND", "claude")
        monkeypatch.setenv("ARCHIVE_AGENT_LLM_API_KEY", "agent-specific-key")
        cfg = load_llm_config_for_agent("archive_agent")
        assert cfg.api_key == "agent-specific-key"

    def test_api_key_is_none_when_not_set(self, monkeypatch):
        monkeypatch.delenv("ARCHIVE_AGENT_LLM_API_KEY", raising=False)
        cfg = load_llm_config_for_agent("archive_agent")
        assert cfg.api_key is None

    def test_different_agents_get_different_configs(self, monkeypatch):
        monkeypatch.setenv("ARCHIVE_AGENT_LLM_BACKEND", "claude")
        monkeypatch.setenv("RESEARCH_AGENT_LLM_BACKEND", "gemini")
        monkeypatch.delenv("LLM_BACKEND", raising=False)

        archive_cfg = load_llm_config_for_agent("archive_agent")
        research_cfg = load_llm_config_for_agent("research-agent")

        assert archive_cfg.backend == "claude"
        assert research_cfg.backend == "gemini"

    def test_backend_value_is_lowercased(self, monkeypatch):
        monkeypatch.setenv("ARCHIVE_AGENT_LLM_BACKEND", "CLAUDE")
        cfg = load_llm_config_for_agent("archive_agent")
        assert cfg.backend == "claude"


# ── llm_config_from_dispatch ──────────────────────────────────────────────────

class TestLLMConfigFromDispatch:
    def test_returns_none_when_no_llm_config_field(self):
        dispatch = {"task_id": "t1", "action": "search", "params": {}}
        assert llm_config_from_dispatch(dispatch) is None

    def test_returns_config_from_dispatch_message(self):
        dispatch = {
            "task_id": "t1",
            "action": "search",
            "llm_config": {"backend": "claude"},
        }
        cfg = llm_config_from_dispatch(dispatch)
        assert cfg is not None
        assert cfg.backend == "claude"

    def test_dispatch_config_with_model(self):
        dispatch = {
            "llm_config": {"backend": "gemini", "model": "gemini-2.5-flash"},
        }
        cfg = llm_config_from_dispatch(dispatch)
        assert cfg is not None
        assert cfg.model == "gemini-2.5-flash"

    def test_dispatch_config_with_api_key(self):
        dispatch = {
            "llm_config": {"backend": "claude", "api_key": "per-call-key"},
        }
        cfg = llm_config_from_dispatch(dispatch)
        assert cfg is not None
        assert cfg.api_key == "per-call-key"

    def test_dispatch_config_backend_lowercased(self):
        dispatch = {"llm_config": {"backend": "Claude"}}
        cfg = llm_config_from_dispatch(dispatch)
        assert cfg is not None
        assert cfg.backend == "claude"

    def test_returns_none_when_llm_config_is_none(self):
        dispatch = {"llm_config": None}
        assert llm_config_from_dispatch(dispatch) is None

    def test_returns_none_when_llm_config_missing_backend(self):
        dispatch = {"llm_config": {"model": "gemini-2.5-flash"}}
        assert llm_config_from_dispatch(dispatch) is None
