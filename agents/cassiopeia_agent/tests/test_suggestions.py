import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from agents.cassiopeia_agent.main import app
from agents.cassiopeia_agent.app_context import ctx

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_mocks():
    # Mock health_monitor's get_available_agents
    ctx.health_monitor = AsyncMock()
    yield

@pytest.mark.asyncio
async def test_get_prompt_suggestions_no_agents():
    ctx.health_monitor.get_available_agents.return_value = []
    
    response = client.get("/prompts/suggestions")
    assert response.status_code == 200
    data = response.json()
    
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)
    # Should contain default suggestions even if no agents are available
    assert len(data["suggestions"]) >= 2
    assert "오늘 날씨 어때?" in data["suggestions"]
    assert "간단한 인사말 작성해줘" in data["suggestions"]
    
    ctx.health_monitor.get_available_agents.assert_called_once()

@pytest.mark.asyncio
async def test_get_prompt_suggestions_with_agents():
    ctx.health_monitor.get_available_agents.return_value = ["archive_agent", "schedule-agent", "unknown_agent"]

    response = client.get("/prompts/suggestions")
    assert response.status_code == 200
    data = response.json()

    assert "suggestions" in data
    suggestions = data["suggestions"]

    # Defaults + 2 specific agent suggestions (archive_agent, schedule-agent) = 4
    assert len(suggestions) == 4
    assert "오늘 날씨 어때?" in suggestions
    assert "최근 회의록 찾아줘" in suggestions  # archive_agent
    assert "내일 오후 3시에 회의 일정 추가해줘" in suggestions  # schedule-agent

    # unknown_agent should not cause errors, just be ignored or no specific suggestion

    ctx.health_monitor.get_available_agents.assert_called_once()

@pytest.mark.asyncio
async def test_get_prompt_suggestions_max_limit():
    ctx.health_monitor.get_available_agents.return_value = [
        "archive_agent", "research_agent", "schedule-agent", "file_agent", "communication_agent"
    ]
    
    response = client.get("/prompts/suggestions")
    assert response.status_code == 200
    data = response.json()
    
    assert "suggestions" in data
    suggestions = data["suggestions"]
    
    # Should be limited to 5
    assert len(suggestions) == 5
    
    ctx.health_monitor.get_available_agents.assert_called_once()
