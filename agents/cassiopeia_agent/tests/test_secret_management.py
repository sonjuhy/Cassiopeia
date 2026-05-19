import pytest
import os
import json
from unittest.mock import AsyncMock, MagicMock
from agents.cassiopeia_agent.state_manager import StateManager

@pytest.fixture
def mock_redis():
    return AsyncMock()

@pytest.fixture
def state_manager(mock_redis):
    # Use a dummy Fernet key (32 base64-encoded bytes)
    os.environ["ENCRYPTION_KEY"] = "8_I-q-g-G_I-q-g-G_I-q-g-G_I-q-g-G_I-q-g-G_I="
    sm = StateManager(redis_client=mock_redis)
    return sm

@pytest.mark.asyncio
class TestSecretManagement:
    async def test_save_and_get_agent_secret(self, state_manager, mock_redis):
        """에이전트별 시크릿(API 키)을 암호화하여 저장하고 조회하는 기능을 검증합니다."""
        agent_name = "test-agent"
        secrets = {"API_KEY": "super-secret-value", "OTHER_KEY": "another-one"}
        
        # 1. 저장 테스트
        await state_manager.save_agent_secrets(agent_name, secrets)
        
        # Redis에 encrypted_secrets 필드로 저장되었는지 확인
        mock_redis.hset.assert_called_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"agent:{agent_name}:secrets"
        assert "encrypted_secrets" in kwargs["mapping"]
        
        # 2. 조회 테스트
        # hgetall 이 암호화된 데이터를 반환하도록 설정
        encrypted_val = kwargs["mapping"]["encrypted_secrets"]
        mock_redis.hgetall.return_value = {"encrypted_secrets": encrypted_val}
        
        retrieved = await state_manager.get_agent_secrets(agent_name)
        assert retrieved == secrets
        assert retrieved["API_KEY"] == "super-secret-value"

    async def test_get_non_existent_secret(self, state_manager, mock_redis):
        """존재하지 않는 에이전트의 시크릿 조회 시 빈 dict를 반환하는지 확인합니다."""
        mock_redis.hgetall.return_value = {}
        retrieved = await state_manager.get_agent_secrets("non-existent")
        assert retrieved == {}

    async def test_delete_agent_secret(self, state_manager, mock_redis):
        """에이전트 시크릿 삭제 기능을 검증합니다."""
        agent_name = "test-agent"
        await state_manager.delete_agent_secrets(agent_name)
        mock_redis.delete.assert_called_once_with(f"agent:{agent_name}:secrets")
