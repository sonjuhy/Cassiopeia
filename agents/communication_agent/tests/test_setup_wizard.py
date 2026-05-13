import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.communication_agent.slack.agent import SlackCommAgent

@pytest.fixture
def mock_redis():
    return AsyncMock()

@pytest.fixture
def mock_web_client():
    return AsyncMock()

@pytest.fixture
def slack_agent(mock_redis):
    # Mock AsyncWebClient
    web_client = AsyncMock()
    # Mock CassiopeiaClient
    cassiopeia = AsyncMock()
    
    agent = SlackCommAgent(
        web_client=web_client,
        redis=mock_redis,
        cassiopeia=cassiopeia
    )
    return agent

@pytest.mark.asyncio
class TestSetupWizardConversational:
    async def test_handle_setup_command(self, slack_agent):
        """'/설정' 명령어 입력 시 에이전트 선택 메뉴가 출력되는지 검증합니다."""
        # 1. 시뮬레이션: /설정 명령어 수신
        body = {
            "trigger_id": "test-trigger",
            "user_id": "U123",
            "channel_id": "C456",
            "text": ""
        }
        
        # 2. 핸들러 실행
        await slack_agent.handle_setup_command(AsyncMock(), body)
        
        # 3. 검증: 에이전트 선택 버튼이 포함된 메시지 전송 확인
        slack_agent._web_client.chat_postMessage.assert_called_once()
        args, kwargs = slack_agent._web_client.chat_postMessage.call_args
        assert "어떤 에이전트의 설정을" in kwargs["text"]
        
    async def test_open_secret_modal(self, slack_agent):
        """특정 에이전트 설정 버튼 클릭 시 입력 모달이 뜨는지 검증합니다."""
        # 1. 시뮬레이션: 'schedule-agent' 설정 버튼 클릭
        body = {
            "trigger_id": "modal-trigger",
            "actions": [{"value": "schedule-agent"}]
        }
        
        # 2. 핸들러 실행
        await slack_agent.handle_setup_agent_click(AsyncMock(), body)
        
        # 3. 검증: views.open API 호출 확인
        slack_agent._web_client.views_open.assert_called_once()
        args, kwargs = slack_agent._web_client.views_open.call_args
        assert kwargs["view"]["title"]["text"] == "에이전트 키 설정"
        assert "schedule-agent" in kwargs["view"]["private_metadata"]

