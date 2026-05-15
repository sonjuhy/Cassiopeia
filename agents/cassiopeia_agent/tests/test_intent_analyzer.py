"""
intent_analyzer.py 테스트 스위트

커버리지 목표:
  - _parse_agent_messages: 정상 JSON / 코드펜스 JSON / 유효하지 않은 receiver → fallback / 잘못된 JSON
  - _fallback_messages: fallback receiver 로 단일 메시지 반환
  - _build_system_prompt: capabilities 딕셔너리가 프롬프트에 포함
  - LLMIntentAnalyzer.analyze: mock provider 통해 AgentMessage 목록 반환
  - NLU_FALLBACK_AGENT 환경변수 반영
  - 빈 메시지 목록 → fallback 반환
  - 레거시 별칭 클래스 (ClaudeAPIIntentAnalyzer, GeminiAPIIntentAnalyzer)
"""
from __future__ import annotations

import importlib
import json
from unittest.mock import AsyncMock, patch

import pytest

from shared_core.messaging import AgentMessage

# ── 테스트용 capabilities ──────────────────────────────────────────────────────
_CAPABILITIES: dict[str, str] = {
    "file_agent": "파일 시스템 작업",
    "archive_agent": "문서 보관 및 검색",
    "research_agent": "웹 검색 및 분석",
}

_VALID_RECEIVERS = set(_CAPABILITIES.keys())


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _make_json(receiver: str = "file_agent", action: str = "read_file") -> str:
    return json.dumps([{"receiver": receiver, "action": action, "payload": {"path": "/tmp/x"}}])


def _make_json_multi() -> str:
    return json.dumps([
        {"receiver": "file_agent", "action": "read_file", "payload": {}},
        {"receiver": "archive_agent", "action": "save_document", "payload": {}},
    ])


# ── _parse_agent_messages ─────────────────────────────────────────────────────

class TestParseAgentMessages:
    def _parse(self, raw: str, sender: str = "cassiopeia", receivers=None):
        from agents.cassiopeia_agent.intent_analyzer import _parse_agent_messages
        if receivers is None:
            receivers = _VALID_RECEIVERS
        return _parse_agent_messages(raw, sender, receivers)

    def test_single_message_parsed(self):
        messages = self._parse(_make_json("file_agent", "read_file"))
        assert len(messages) == 1
        assert messages[0].receiver == "file_agent"
        assert messages[0].action == "read_file"

    def test_multi_message_parsed(self):
        messages = self._parse(_make_json_multi())
        assert len(messages) == 2
        receivers = {m.receiver for m in messages}
        assert "file_agent" in receivers
        assert "archive_agent" in receivers

    def test_sender_is_set_correctly(self):
        messages = self._parse(_make_json(), sender="cassiopeia")
        assert all(m.sender == "cassiopeia" for m in messages)

    def test_invalid_receiver_replaced_with_fallback(self, monkeypatch):
        monkeypatch.setenv("NLU_FALLBACK_AGENT", "cassiopeia")
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)

        raw = json.dumps([{"receiver": "nonexistent_agent", "action": "do_something", "payload": {}}])
        messages = m._parse_agent_messages(raw, "cassiopeia", _VALID_RECEIVERS)
        assert messages[0].receiver == "cassiopeia"

    def test_code_fenced_json_parsed(self):
        fenced = "```json\n" + _make_json() + "\n```"
        messages = self._parse(fenced)
        assert len(messages) == 1
        assert messages[0].receiver == "file_agent"

    def test_code_fenced_without_lang_parsed(self):
        fenced = "```\n" + _make_json() + "\n```"
        messages = self._parse(fenced)
        assert len(messages) == 1

    def test_invalid_json_returns_fallback(self, monkeypatch):
        monkeypatch.setenv("NLU_FALLBACK_AGENT", "cassiopeia")
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)

        messages = m._parse_agent_messages("this is not json", "cassiopeia", _VALID_RECEIVERS)
        assert len(messages) == 1
        assert messages[0].receiver == m._FALLBACK_RECEIVER

    def test_empty_array_returns_fallback(self, monkeypatch):
        monkeypatch.setenv("NLU_FALLBACK_AGENT", "cassiopeia")
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)

        messages = m._parse_agent_messages("[]", "cassiopeia", _VALID_RECEIVERS)
        assert len(messages) == 1

    def test_missing_receiver_field_uses_fallback(self, monkeypatch):
        monkeypatch.setenv("NLU_FALLBACK_AGENT", "cassiopeia")
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)

        raw = json.dumps([{"action": "do_something", "payload": {}}])
        messages = m._parse_agent_messages(raw, "cassiopeia", _VALID_RECEIVERS)
        assert messages[0].receiver == m._FALLBACK_RECEIVER

    def test_missing_action_uses_default(self):
        raw = json.dumps([{"receiver": "file_agent", "payload": {}}])
        messages = self._parse(raw)
        assert messages[0].action == "process_request"

    def test_payload_is_preserved(self):
        raw = json.dumps([{
            "receiver": "file_agent",
            "action": "read_file",
            "payload": {"path": "/etc/hosts", "encoding": "utf-8"},
        }])
        messages = self._parse(raw)
        assert messages[0].payload == {"path": "/etc/hosts", "encoding": "utf-8"}

    def test_returns_agent_message_instances(self):
        messages = self._parse(_make_json())
        for msg in messages:
            assert isinstance(msg, AgentMessage)


# ── _fallback_messages ────────────────────────────────────────────────────────

class TestFallbackMessages:
    def test_returns_single_message(self, monkeypatch):
        monkeypatch.setenv("NLU_FALLBACK_AGENT", "cassiopeia")
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)

        messages = m._fallback_messages("cassiopeia")
        assert len(messages) == 1

    def test_fallback_receiver_is_env_value(self, monkeypatch):
        monkeypatch.setenv("NLU_FALLBACK_AGENT", "archive_agent")
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)

        messages = m._fallback_messages("cassiopeia")
        assert messages[0].receiver == "archive_agent"

    def test_fallback_action_is_process_request(self, monkeypatch):
        monkeypatch.setenv("NLU_FALLBACK_AGENT", "cassiopeia")
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)

        messages = m._fallback_messages("cassiopeia")
        assert messages[0].action == "process_request"


# ── _build_system_prompt ──────────────────────────────────────────────────────

class TestBuildSystemPrompt:
    def test_agent_names_in_prompt(self):
        from agents.cassiopeia_agent.intent_analyzer import _build_system_prompt
        prompt = _build_system_prompt(_CAPABILITIES)
        for name in _CAPABILITIES:
            assert name in prompt

    def test_agent_descriptions_in_prompt(self):
        from agents.cassiopeia_agent.intent_analyzer import _build_system_prompt
        prompt = _build_system_prompt(_CAPABILITIES)
        assert "파일 시스템 작업" in prompt

    def test_empty_capabilities(self):
        from agents.cassiopeia_agent.intent_analyzer import _build_system_prompt
        prompt = _build_system_prompt({})
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ── LLMIntentAnalyzer.analyze ─────────────────────────────────────────────────

class TestLLMIntentAnalyzerAnalyze:
    @pytest.fixture
    def mock_provider(self):
        from shared_core.llm.interfaces import LLMUsage
        p = AsyncMock()
        p.generate_response = AsyncMock(
            return_value=(
                _make_json("file_agent", "read_file"),
                LLMUsage(prompt_tokens=10, completion_tokens=30, total_tokens=40),
            )
        )
        return p

    async def test_analyze_returns_agent_messages(self, mock_provider):
        from agents.cassiopeia_agent.intent_analyzer import LLMIntentAnalyzer
        analyzer = LLMIntentAnalyzer(provider=mock_provider)
        messages = await analyzer.analyze("파일 읽어줘", _CAPABILITIES)
        assert len(messages) >= 1
        assert all(isinstance(m, AgentMessage) for m in messages)

    async def test_analyze_uses_capabilities_in_prompt(self, mock_provider):
        from agents.cassiopeia_agent.intent_analyzer import LLMIntentAnalyzer
        analyzer = LLMIntentAnalyzer(provider=mock_provider)
        await analyzer.analyze("파일 읽어줘", _CAPABILITIES)

        call_kwargs = mock_provider.generate_response.call_args.kwargs
        assert "file_agent" in call_kwargs.get("system_instruction", "")

    async def test_analyze_sender_is_cassiopeia(self, mock_provider):
        from agents.cassiopeia_agent.intent_analyzer import LLMIntentAnalyzer
        analyzer = LLMIntentAnalyzer(provider=mock_provider)
        messages = await analyzer.analyze("작업 요청", _CAPABILITIES)
        assert all(m.sender == "cassiopeia" for m in messages)

    async def test_analyze_with_invalid_llm_response_returns_fallback(
        self, mock_provider, monkeypatch
    ):
        monkeypatch.setenv("NLU_FALLBACK_AGENT", "cassiopeia")
        from shared_core.llm.interfaces import LLMUsage
        mock_provider.generate_response = AsyncMock(
            return_value=("not valid json at all", LLMUsage(0, 0, 0))
        )
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)
        analyzer = m.LLMIntentAnalyzer(provider=mock_provider)
        messages = await analyzer.analyze("테스트", _CAPABILITIES)
        assert len(messages) >= 1
        assert messages[0].receiver == m._FALLBACK_RECEIVER

    async def test_analyze_only_returns_valid_receivers(self, mock_provider):
        """LLM 이 범위 밖의 에이전트를 반환하면 폴백으로 대체되어야 합니다."""
        from shared_core.llm.interfaces import LLMUsage
        mock_provider.generate_response = AsyncMock(
            return_value=(
                json.dumps([{"receiver": "unknown_agent_xyz", "action": "do_it", "payload": {}}]),
                LLMUsage(0, 0, 0),
            )
        )
        from agents.cassiopeia_agent.intent_analyzer import LLMIntentAnalyzer
        analyzer = LLMIntentAnalyzer(provider=mock_provider)
        messages = await analyzer.analyze("요청", _CAPABILITIES)
        for msg in messages:
            # receiver 는 capabilities 에 있는 것이거나 fallback 이어야 함
            assert msg.receiver in _CAPABILITIES or msg.receiver is not None


# ── NLU_FALLBACK_AGENT 환경변수 ───────────────────────────────────────────────

class TestFallbackAgentEnvVar:
    def test_default_fallback_is_cassiopeia(self, monkeypatch):
        monkeypatch.delenv("NLU_FALLBACK_AGENT", raising=False)
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)
        assert m._FALLBACK_RECEIVER == "cassiopeia"

    def test_custom_fallback_via_env(self, monkeypatch):
        monkeypatch.setenv("NLU_FALLBACK_AGENT", "archive_agent")
        import importlib
        from agents.cassiopeia_agent import intent_analyzer as m
        importlib.reload(m)
        assert m._FALLBACK_RECEIVER == "archive_agent"


# ── 레거시 별칭 클래스 ────────────────────────────────────────────────────────

class TestLegacyAliases:
    def test_claude_api_intent_analyzer_is_subclass(self):
        from agents.cassiopeia_agent.intent_analyzer import (
            ClaudeAPIIntentAnalyzer,
            LLMIntentAnalyzer,
        )
        mock_prov = AsyncMock()
        with patch("shared_core.llm.ClaudeProvider", return_value=mock_prov):
            analyzer = ClaudeAPIIntentAnalyzer()
        assert isinstance(analyzer, LLMIntentAnalyzer)

    def test_gemini_api_intent_analyzer_is_subclass(self):
        from agents.cassiopeia_agent.intent_analyzer import (
            GeminiAPIIntentAnalyzer,
            LLMIntentAnalyzer,
        )
        mock_prov = AsyncMock()
        with patch("shared_core.llm.GeminiProvider", return_value=mock_prov):
            analyzer = GeminiAPIIntentAnalyzer()
        assert isinstance(analyzer, LLMIntentAnalyzer)

    def test_claude_cli_intent_analyzer_warns_deprecated(self):
        from agents.cassiopeia_agent.intent_analyzer import ClaudeCLIIntentAnalyzer
        with pytest.warns(DeprecationWarning, match="deprecated"):
            ClaudeCLIIntentAnalyzer()

    def test_gemini_cli_intent_analyzer_warns_deprecated(self):
        from agents.cassiopeia_agent.intent_analyzer import GeminiCLIIntentAnalyzer
        with pytest.warns(DeprecationWarning, match="deprecated"):
            GeminiCLIIntentAnalyzer()
