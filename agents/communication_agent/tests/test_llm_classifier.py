"""
LLMClassifier 테스트 스위트

커버리지 목표:
  - _parse_agent_name: 정확 일치 / 부분 포함 / 폴백
  - _build_system_prompt: 레지스트리 기반 프롬프트 생성 / 빈 레지스트리
  - LLMClassifier: 의존성 주입 레지스트리 / env var 레지스트리 / 기본 AGENT_REGISTRY
  - LLMClassifier.classify: LLM 응답 파싱 → 에이전트 이름
  - CLASSIFIER_FALLBACK_AGENT env var
  - COMM_AGENT_REGISTRY env var (JSON 파싱)
  - ClaudeAPIClassifier / GeminiAPIClassifier: agent_registry 전달
  - ClaudeCLIClassifier / GeminiCLIClassifier: agent_registry 전달
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── 테스트 전용 레지스트리 ──────────────────────────────────────────────────────
_TEST_REGISTRY: dict[str, str] = {
    "archive_agent": "문서 보관 및 기획 처리",
    "file_agent": "파일 시스템 작업 처리",
    "research_agent": "웹 검색 및 리서치",
}

# ── 픽스처 ────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_provider():
    """generate_response 를 AsyncMock 으로 대체한 LLM 공급자."""
    from shared_core.llm.interfaces import LLMUsage
    provider = AsyncMock()
    provider.generate_response = AsyncMock(
        return_value=("archive_agent", LLMUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10))
    )
    return provider


@pytest.fixture
def classifier(mock_provider):
    """테스트 레지스트리를 주입한 LLMClassifier."""
    from agents.communication_agent.slack.llm_classifier import LLMClassifier
    return LLMClassifier(provider=mock_provider, agent_registry=_TEST_REGISTRY)


# ── _parse_agent_name ─────────────────────────────────────────────────────────

class TestParseAgentName:
    def test_exact_match(self):
        from agents.communication_agent.slack.llm_classifier import _parse_agent_name
        assert _parse_agent_name("archive_agent", _TEST_REGISTRY) == "archive_agent"

    def test_match_with_whitespace(self):
        from agents.communication_agent.slack.llm_classifier import _parse_agent_name
        assert _parse_agent_name("  file_agent  ", _TEST_REGISTRY) == "file_agent"

    def test_case_insensitive_match(self):
        from agents.communication_agent.slack.llm_classifier import _parse_agent_name
        assert _parse_agent_name("ARCHIVE_AGENT", _TEST_REGISTRY) == "archive_agent"

    def test_partial_match_in_longer_response(self):
        from agents.communication_agent.slack.llm_classifier import _parse_agent_name
        # LLM 이 "I recommend archive_agent for this task" 같은 응답을 반환한 경우
        assert _parse_agent_name("I recommend archive_agent for this", _TEST_REGISTRY) == "archive_agent"

    def test_unknown_returns_fallback(self, monkeypatch):
        from agents.communication_agent.slack.llm_classifier import _parse_agent_name
        monkeypatch.setenv("CLASSIFIER_FALLBACK_AGENT", "archive_agent")
        result = _parse_agent_name("unknown_xyz_agent", _TEST_REGISTRY)
        assert result == "archive_agent"

    def test_empty_registry_returns_fallback(self, monkeypatch):
        from agents.communication_agent.slack.llm_classifier import _parse_agent_name
        monkeypatch.setenv("CLASSIFIER_FALLBACK_AGENT", "default_agent")
        result = _parse_agent_name("archive_agent", {})
        assert result == "default_agent"


# ── _build_system_prompt ──────────────────────────────────────────────────────

class TestBuildSystemPrompt:
    def test_contains_agent_names(self):
        from agents.communication_agent.slack.llm_classifier import _build_system_prompt
        prompt = _build_system_prompt(_TEST_REGISTRY)
        assert "archive_agent" in prompt
        assert "file_agent" in prompt
        assert "research_agent" in prompt

    def test_contains_descriptions(self):
        from agents.communication_agent.slack.llm_classifier import _build_system_prompt
        prompt = _build_system_prompt(_TEST_REGISTRY)
        assert "문서 보관 및 기획 처리" in prompt

    def test_empty_registry_uses_fallback_name(self, monkeypatch):
        from agents.communication_agent.slack.llm_classifier import _build_system_prompt
        monkeypatch.setenv("CLASSIFIER_FALLBACK_AGENT", "default_agent")
        prompt = _build_system_prompt({})
        assert "default_agent" in prompt


# ── LLMClassifier 초기화 ──────────────────────────────────────────────────────

class TestLLMClassifierInit:
    def test_injected_registry_is_used(self, mock_provider):
        from agents.communication_agent.slack.llm_classifier import LLMClassifier
        clf = LLMClassifier(provider=mock_provider, agent_registry=_TEST_REGISTRY)
        assert clf._agent_registry == _TEST_REGISTRY

    def test_none_registry_falls_back_to_module_constant(self, mock_provider, monkeypatch):
        """agent_registry=None 이면 models.AGENT_REGISTRY(env var 기반)를 사용합니다."""
        monkeypatch.setenv("COMM_AGENT_REGISTRY", json.dumps({"env_agent": "env 기반 에이전트"}))
        # models 모듈을 다시 로드해서 env var 반영
        import importlib
        import agents.communication_agent.models as models_mod
        importlib.reload(models_mod)
        from agents.communication_agent.slack import llm_classifier as clf_mod
        importlib.reload(clf_mod)

        clf = clf_mod.LLMClassifier(provider=mock_provider, agent_registry=None)
        # COMM_AGENT_REGISTRY 에 있는 에이전트가 포함되어야 함
        assert "env_agent" in clf._agent_registry

    def test_empty_comm_agent_registry_env_gives_empty_dict(self, mock_provider, monkeypatch):
        monkeypatch.delenv("COMM_AGENT_REGISTRY", raising=False)
        import importlib
        import agents.communication_agent.models as models_mod
        importlib.reload(models_mod)
        from agents.communication_agent.slack import llm_classifier as clf_mod
        importlib.reload(clf_mod)

        clf = clf_mod.LLMClassifier(provider=mock_provider, agent_registry=None)
        assert isinstance(clf._agent_registry, dict)


# ── LLMClassifier.classify ────────────────────────────────────────────────────

class TestLLMClassifierClassify:
    @pytest.fixture
    def slack_event(self):
        return {
            "user": "U001",
            "channel": "C001",
            "text": "최근 기획서를 보관해줘",
            "ts": "123456.789",
            "thread_ts": None,
        }

    async def test_classify_returns_valid_agent(self, classifier, slack_event):
        result = await classifier.classify(slack_event)
        assert result == "archive_agent"

    async def test_classify_calls_provider(self, classifier, mock_provider, slack_event):
        await classifier.classify(slack_event)
        mock_provider.generate_response.assert_called_once()

    async def test_classify_with_unknown_llm_response_returns_fallback(
        self, mock_provider, slack_event, monkeypatch
    ):
        from shared_core.llm.interfaces import LLMUsage
        from agents.communication_agent.slack.llm_classifier import LLMClassifier

        monkeypatch.setenv("CLASSIFIER_FALLBACK_AGENT", "archive_agent")
        mock_provider.generate_response = AsyncMock(
            return_value=("xyz_unknown_bot", LLMUsage(0, 0, 0))
        )
        clf = LLMClassifier(provider=mock_provider, agent_registry=_TEST_REGISTRY)
        result = await clf.classify(slack_event)
        assert result == "archive_agent"

    async def test_classify_partial_match_in_llm_response(
        self, mock_provider, slack_event
    ):
        from shared_core.llm.interfaces import LLMUsage
        from agents.communication_agent.slack.llm_classifier import LLMClassifier

        mock_provider.generate_response = AsyncMock(
            return_value=("The best agent is file_agent.", LLMUsage(0, 0, 0))
        )
        clf = LLMClassifier(provider=mock_provider, agent_registry=_TEST_REGISTRY)
        result = await clf.classify(slack_event)
        assert result == "file_agent"

    async def test_classify_prompt_contains_registry_agents(
        self, mock_provider, slack_event
    ):
        from agents.communication_agent.slack.llm_classifier import LLMClassifier
        clf = LLMClassifier(provider=mock_provider, agent_registry=_TEST_REGISTRY)
        await clf.classify(slack_event)

        call_kwargs = mock_provider.generate_response.call_args
        system_instruction = call_kwargs.kwargs.get("system_instruction", "")
        assert "archive_agent" in system_instruction
        assert "file_agent" in system_instruction


# ── CLASSIFIER_FALLBACK_AGENT 환경변수 ────────────────────────────────────────

class TestFallbackAgentEnvVar:
    def test_default_fallback_is_archive_agent(self, monkeypatch):
        monkeypatch.delenv("CLASSIFIER_FALLBACK_AGENT", raising=False)
        import importlib
        from agents.communication_agent.slack import llm_classifier as m
        importlib.reload(m)
        assert m._FALLBACK_AGENT == "archive_agent"

    def test_custom_fallback_via_env(self, monkeypatch):
        monkeypatch.setenv("CLASSIFIER_FALLBACK_AGENT", "research_agent")
        import importlib
        from agents.communication_agent.slack import llm_classifier as m
        importlib.reload(m)
        assert m._FALLBACK_AGENT == "research_agent"


# ── 레거시 클래스 agent_registry 전달 ─────────────────────────────────────────

class TestLegacyClassifiers:
    def test_claude_api_classifier_accepts_registry(self, monkeypatch):
        from agents.communication_agent.slack.llm_classifier import ClaudeAPIClassifier

        mock_prov = AsyncMock()
        with patch(
            "agents.communication_agent.slack.llm_classifier.build_llm_provider",
            return_value=mock_prov,
        ), patch(
            "shared_core.llm.ClaudeProvider",
            return_value=mock_prov,
        ):
            clf = ClaudeAPIClassifier(agent_registry=_TEST_REGISTRY)
            assert clf._agent_registry == _TEST_REGISTRY

    def test_gemini_api_classifier_accepts_registry(self, monkeypatch):
        from agents.communication_agent.slack.llm_classifier import GeminiAPIClassifier

        mock_prov = AsyncMock()
        with patch(
            "shared_core.llm.GeminiProvider",
            return_value=mock_prov,
        ):
            clf = GeminiAPIClassifier(agent_registry=_TEST_REGISTRY)
            assert clf._agent_registry == _TEST_REGISTRY

    def test_claude_cli_classifier_accepts_registry(self):
        from agents.communication_agent.slack.llm_classifier import ClaudeCLIClassifier
        with pytest.warns(DeprecationWarning):
            clf = ClaudeCLIClassifier(agent_registry=_TEST_REGISTRY)
        assert clf._agent_registry == _TEST_REGISTRY

    def test_gemini_cli_classifier_accepts_registry(self):
        from agents.communication_agent.slack.llm_classifier import GeminiCLIClassifier
        with pytest.warns(DeprecationWarning):
            clf = GeminiCLIClassifier(agent_registry=_TEST_REGISTRY)
        assert clf._agent_registry == _TEST_REGISTRY

    def test_cli_classifier_fallback_on_process_error(self, monkeypatch):
        """subprocess 실패 시 폴백 에이전트를 반환해야 합니다."""
        from agents.communication_agent.slack.llm_classifier import ClaudeCLIClassifier

        monkeypatch.setenv("CLASSIFIER_FALLBACK_AGENT", "archive_agent")

        with pytest.warns(DeprecationWarning):
            clf = ClaudeCLIClassifier(agent_registry=_TEST_REGISTRY)

        event = {"user": "U1", "channel": "C1", "text": "test", "ts": "1", "thread_ts": None}

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(clf.classify(event))
        assert result == "archive_agent"
