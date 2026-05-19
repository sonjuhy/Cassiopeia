import asyncio
import json
import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agents.archive_agent.notion.agent import ArchiveAgent

@pytest.fixture
def mock_env():
    with patch.dict(os.environ, {
        "NOTION_TOKEN": "test", 
        "NOTION_DATABASE_ID": "test_db", 
        "GEMINI_API_KEY": "test_key"
    }):
        yield

@pytest.mark.asyncio
async def test_list_databases_success(mock_env):
    # Arrange
    agent = ArchiveAgent()
    agent.logger = AsyncMock()
    
    # Mock Notion Search API response (databases only)
    mock_search_results = [
        {
            "id": "db-id-1",
            "object": "database",
            "title": [{"plain_text": "프로젝트 관리"}],
            "created_time": "2026-05-01T00:00:00Z"
        },
        {
            "id": "db-id-2",
            "object": "database",
            "title": [{"plain_text": "회의록"}],
            "created_time": "2026-05-02T00:00:00Z"
        }
    ]
    agent.search_notion = AsyncMock(return_value=mock_search_results)
    
    dispatch_msg = {
        "task_id": "discovery-01",
        "action": "list_databases",
        "params": {}
    }
    
    # Act
    result = await agent.handle_dispatch(dispatch_msg)
    
    # Assert
    assert result["status"] == "COMPLETED"
    assert "데이터베이스 목록" in result["result_data"]["summary"]
    assert "프로젝트 관리" in result["result_data"]["content"]
    assert "회의록" in result["result_data"]["content"]
    # Check if search_notion was called with correct filter
    agent.search_notion.assert_awaited_once_with(filter_obj={"property": "object", "value": "database"})

@pytest.mark.asyncio
async def test_search_objects_success(mock_env):
    # Arrange
    agent = ArchiveAgent()
    agent.logger = AsyncMock()
    
    # Mock Notion Search API response (mixed)
    mock_search_results = [
        {
            "id": "page-01",
            "object": "page",
            "properties": {"title": {"type": "title", "title": [{"plain_text": "문서 가이드"}]}},
            "url": "https://notion.so/page-01"
        },
        {
            "id": "db-01",
            "object": "database",
            "title": [{"plain_text": "태스크 DB"}],
            "url": "https://notion.so/db-01"
        }
    ]
    agent.search_notion = AsyncMock(return_value=mock_search_results)
    
    dispatch_msg = {
        "task_id": "discovery-02",
        "action": "search_objects",
        "params": {"query": "가이드"}
    }
    
    # Act
    result = await agent.handle_dispatch(dispatch_msg)
    
    # Assert
    assert result["status"] == "COMPLETED"
    assert "가이드" in result["result_data"]["content"]
    assert "태스크 DB" in result["result_data"]["content"]
    # Check if search_notion was called with correct query
    agent.search_notion.assert_awaited_once_with(query="가이드")
