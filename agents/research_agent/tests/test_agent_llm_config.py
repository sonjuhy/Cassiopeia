"""
ResearchAgent의 에이전트별/per-call LLM 설정 테스트

- RESEARCH_AGENT_LLM_BACKEND 환경변수로 기본 LLM 백엔드 설정
- dispatch 메시지의 llm_config로 per-call 오버라이드
- _llm_config 속성 존재 확인
- 파이프라인 컴포넌트(IntentAnalyzer, ReportSynthesizer)가 per-call LLM을 사용하는지 확인
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from shared_core.llm.llm_config import LLMConfig
from shared_core.search.interfaces import SearchResult


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.search_provider = "gemini"
    cfg.search_api_key = "test"
    cfg.gemini_model = "gemini-2.5-flash"
    cfg.perplexity_model = "sonar"
    cfg.fallback_provider = None
    cfg.fallback_api_key = ""
    return cfg


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.search.return_value = SearchResult(answer="result", citations=[])
    return provider


def _make_llm():
    llm = AsyncMock()
    llm.generate_response.return_value = (
        '["query1", "query2"]',
        MagicMock(prompt_tokens=5, completion_tokens=5, total_tokens=10),
    )
    return llm


# ── _llm_config 속성 ──────────────────────────────────────────────────────────

class TestResearchAgentLLMConfigAttribute:
    def test_agent_has_llm_config_attribute(self, mock_config, mock_provider):
        """ResearchAgent은 초기화 시 _llm_config 속성을 갖는다."""
        mock_llm = _make_llm()
        with patch("agents.research_agent.agent.build_llm_provider_from_config", return_value=mock_llm):
            from agents.research_agent.agent import ResearchAgent
            agent = ResearchAgent(config=mock_config, provider=mock_provider)

        assert hasattr(agent, "_llm_config")
        assert isinstance(agent._llm_config, LLMConfig)

    def test_agent_uses_research_agent_llm_backend_env(self, mock_config, mock_provider, monkeypatch):
        """RESEARCH_AGENT_LLM_BACKEND 환경변수를 읽어 기본 LLM 백엔드를 설정한다."""
        monkeypatch.setenv("RESEARCH_AGENT_LLM_BACKEND", "claude")
        captured = []

        def fake_build(cfg: LLMConfig):
            captured.append(cfg)
            return _make_llm()

        with patch("agents.research_agent.agent.build_llm_provider_from_config", side_effect=fake_build):
            from agents.research_agent.agent import ResearchAgent
            agent = ResearchAgent(config=mock_config, provider=mock_provider)

        assert agent._llm_config.backend == "claude"

    def test_agent_falls_back_to_global_llm_backend(self, mock_config, mock_provider, monkeypatch):
        """RESEARCH_AGENT_LLM_BACKEND가 없으면 LLM_BACKEND 전역 환경변수를 사용한다."""
        monkeypatch.delenv("RESEARCH_AGENT_LLM_BACKEND", raising=False)
        monkeypatch.setenv("LLM_BACKEND", "local")

        mock_llm = _make_llm()
        with patch("agents.research_agent.agent.build_llm_provider_from_config", return_value=mock_llm):
            from agents.research_agent.agent import ResearchAgent
            agent = ResearchAgent(config=mock_config, provider=mock_provider)

        assert agent._llm_config.backend == "local"


# ── per-call LLM 설정 (dispatch 메시지) ──────────────────────────────────────

class TestResearchAgentPerCallLLMConfig:
    @pytest.mark.asyncio
    async def test_dispatch_llm_config_used_for_pipeline(self, mock_config, mock_provider):
        """dispatch에 llm_config가 있으면 파이프라인이 해당 LLM 공급자로 실행된다."""
        per_call_llm = _make_llm()
        call_log: list[LLMConfig] = []

        def fake_build(cfg: LLMConfig):
            call_log.append(cfg)
            return per_call_llm

        raw = '{"task_id":"t1","action":"investigate","params":{"query":"test"},"llm_config":{"backend":"claude","model":"claude-haiku-4-5-20251001"}}'

        with patch("agents.research_agent.agent.build_llm_provider_from_config", side_effect=fake_build):
            from agents.research_agent.agent import ResearchAgent
            agent = ResearchAgent(config=mock_config, provider=mock_provider)

            # 파이프라인 컴포넌트 목킹
            agent._intent_analyzer = AsyncMock()
            agent._intent_analyzer.analyze.return_value = ["q1"]
            agent._search_executor = AsyncMock()
            agent._search_executor.execute.return_value = [SearchResult(answer="r", citations=[])]
            agent._report_synthesizer = AsyncMock()
            agent._report_synthesizer.synthesize.return_value = ("report", [])
            agent._storage = AsyncMock()
            agent._storage.save_data.return_value = "ref-1"
            agent._report_result = AsyncMock()

            await agent._handle_task(raw, "http://cassiopeia:8001")

        per_call_cfgs = [c for c in call_log if c.backend == "claude"]
        assert len(per_call_cfgs) >= 1
        assert per_call_cfgs[0].model == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_dispatch_without_llm_config_uses_agent_default(self, mock_config, mock_provider, monkeypatch):
        """dispatch에 llm_config가 없으면 에이전트 기본 LLM 설정을 사용한다."""
        monkeypatch.setenv("RESEARCH_AGENT_LLM_BACKEND", "gemini")
        mock_llm = _make_llm()

        with patch("agents.research_agent.agent.build_llm_provider_from_config", return_value=mock_llm):
            from agents.research_agent.agent import ResearchAgent
            agent = ResearchAgent(config=mock_config, provider=mock_provider)

        agent._intent_analyzer = AsyncMock()
        agent._intent_analyzer.analyze.return_value = ["q1"]
        agent._search_executor = AsyncMock()
        agent._search_executor.execute.return_value = [SearchResult(answer="r", citations=[])]
        agent._report_synthesizer = AsyncMock()
        agent._report_synthesizer.synthesize.return_value = ("report", [])
        agent._storage = AsyncMock()
        agent._storage.save_data.return_value = "ref-2"
        agent._report_result = AsyncMock()

        raw = '{"task_id":"t2","action":"investigate","params":{"query":"test"}}'
        await agent._handle_task(raw, "http://cassiopeia:8001")

        assert agent._llm_config.backend == "gemini"
