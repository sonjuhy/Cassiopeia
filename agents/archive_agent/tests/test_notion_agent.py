import asyncio
import json
import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agents.archive_agent.notion.agent import ArchiveAgent

@pytest.fixture
def mock_env():
    with patch.dict(os.environ, {"NOTION_TOKEN": "test", "NOTION_DATABASE_ID": "test_db", "ANTHROPIC_API_KEY": "test_key"}):
        yield

@pytest.mark.asyncio
async def test_notion_agent_storage_integration(mock_env):
    # Arrange
    agent = ArchiveAgent()
    agent._storage = AsyncMock()
    agent._storage.save_data.return_value = "ref_notion_123"
    agent.logger = AsyncMock()
    
    # Mock brain decision
    from cassiopeia_sdk.brain import BrainDecision
    mock_decision = BrainDecision(
        action="query_database", 
        params={"target_id": "db_123", "query": "test"}
    )
    agent.brain.analyze_task = AsyncMock(return_value=mock_decision)
    
    # Mock Notion DB search response
    agent.search_notion = AsyncMock(return_value=[{"id": "db_123", "object": "database", "title": "Test Page"}])
    agent.query_database = AsyncMock(return_value=[{"id": "page_1", "object": "page", "properties": {}}])
    
    dispatch_msg = {
        "task_id": "test_notion_task",
        "content": "find in db",
        "params": {
            "action": "query_database",
            "target_id": "db_123",
            "query": "test"
        }
    }
    
    # Mock human friendly content to bypass LLM logic
    agent._generate_human_friendly_content = lambda x: "Friendly Markdown Content"
    
    # Act
    result = await agent.handle_dispatch(dispatch_msg)
    
    print(f"Result: {result}")
    
    # Assert
    assert result["status"] == "COMPLETED"
    assert result["result_data"]["reference_id"] == "ref_notion_123"
    assert "raw_data" not in result["result_data"]
    
    agent._storage.save_data.assert_awaited_once()
    args, kwargs = agent._storage.save_data.call_args
    assert "results" in kwargs["data"]
    assert kwargs["metadata"] == {"action": "query_database", "task_id": "test_notion_task", "source": "notion"}
