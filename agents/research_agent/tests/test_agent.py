import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agents.research_agent.agent import ResearchAgent
from shared_core.search.interfaces import SearchResult

@pytest.fixture
def mock_pipeline_components():
    analyzer = AsyncMock()
    analyzer.analyze.return_value = ["query 1", "query 2"]
    
    executor = AsyncMock()
    executor.execute.return_value = [
        SearchResult(answer="Result 1", citations=["http://doc1.com"]),
        SearchResult(answer="Result 2", citations=["http://doc2.com"]),
    ]
    
    synthesizer = AsyncMock()
    synthesizer.synthesize.return_value = ("Synthesized Report Content", ["http://doc1.com", "http://doc2.com"])
    
    return analyzer, executor, synthesizer

@pytest.fixture
def mock_config():
    config = MagicMock()
    config.search_provider = "gemini"
    config.search_api_key = "test"
    config.gemini_model = "gemini-2.5-flash"
    config.perplexity_model = "sonar"
    config.fallback_provider = None
    config.fallback_api_key = ""
    return config

@pytest.mark.asyncio
async def test_agent_investigate_pipeline_success(mock_config, mock_pipeline_components):
    analyzer, executor, synthesizer = mock_pipeline_components
    
    agent = ResearchAgent(config=mock_config, provider=AsyncMock())
    
    # Inject mocked components into agent
    agent._intent_analyzer = analyzer
    agent._search_executor = executor
    agent._report_synthesizer = synthesizer
    
    result = await agent.investigate("broad query")
    
    analyzer.analyze.assert_awaited_once_with("broad query")
    executor.execute.assert_awaited_once_with(["query 1", "query 2"])
    synthesizer.synthesize.assert_awaited_once()
    
    assert "Synthesized Report Content" in result
    assert "http://doc1.com" in result
    assert "http://doc2.com" in result

@pytest.mark.asyncio
async def test_agent_investigate_pipeline_failure(mock_config, mock_pipeline_components):
    analyzer, executor, synthesizer = mock_pipeline_components
    analyzer.analyze.side_effect = Exception("Pipeline crashed")
    
    agent = ResearchAgent(config=mock_config, provider=AsyncMock())
    
    # Inject mocked components into agent
    agent._intent_analyzer = analyzer
    agent._search_executor = executor
    agent._report_synthesizer = synthesizer
    
    result = await agent.investigate("broad query")
    
    assert "검색 중 오류 발생: Pipeline crashed" in result

@pytest.mark.asyncio
async def test_agent_handle_task_storage(mock_config, mock_pipeline_components):
    analyzer, executor, synthesizer = mock_pipeline_components
    
    agent = ResearchAgent(config=mock_config, provider=AsyncMock())
    
    # Inject mocked components into agent
    agent._intent_analyzer = analyzer
    agent._search_executor = executor
    agent._report_synthesizer = synthesizer
    
    # Mock storage
    agent._storage = AsyncMock()
    agent._storage.save_data.return_value = "ref_123"
    
    # Mock _report_result to avoid actual HTTP calls
    agent._report_result = AsyncMock()
    
    raw_msg = json.dumps({
        "task_id": "test_task_1",
        "action": "investigate",
        "params": {"query": "test query"}
    })
    
    await agent._handle_task(raw_msg, "http://cassiopeia")
    
    # Verify storage was called correctly
    agent._storage.save_data.assert_awaited_once()
    args, kwargs = agent._storage.save_data.call_args
    assert "raw_text" in kwargs["data"]
    assert "Synthesized Report Content" in kwargs["data"]["raw_text"]
    assert kwargs["metadata"] == {"action": "investigate", "task_id": "test_task_1"}
    
    # Verify result reporting included the reference_id
    agent._report_result.assert_awaited_once()
    _, report_kwargs = agent._report_result.call_args
    assert report_kwargs["reference_id"] == "ref_123"
    assert report_kwargs["status"] == "COMPLETED"
