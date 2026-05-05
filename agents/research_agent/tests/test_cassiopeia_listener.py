"""
[TDD] ResearchCassiopeiaListener 테스트
- listen_tasks: Pub/Sub 메시지 수신 후 _handle_task 위임
- _handle_task: AgentMessage → JSON 직렬화 후 ResearchAgent._handle_task 호출
- heartbeat: _update_health 주기적 갱신
- 헬스 상태: BUSY/IDLE 전환
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.research_agent.cassiopeia_listener import ResearchCassiopeiaListener


@pytest.fixture
def mock_agent():
    agent = AsyncMock()
    agent._handle_task = AsyncMock()
    return agent


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.hset = AsyncMock(return_value=1)
    r.expire = AsyncMock(return_value=1)
    return r


@pytest.fixture
def listener(mock_agent, mock_redis):
    inst = ResearchCassiopeiaListener(
        agent=mock_agent,
        redis_url="redis://127.0.0.1:6379",
        orchestra_url="http://orchestra:8001",
    )
    inst._redis = mock_redis
    return inst


def _make_msg(payload: dict, action: str = "investigate") -> MagicMock:
    msg = MagicMock()
    msg.payload = payload
    msg.action = action
    return msg


class TestHandleTask:
    @pytest.mark.asyncio
    async def test_delegates_to_agent_handle_task(self, listener, mock_agent):
        payload = {"task_id": "t-1", "action": "investigate", "params": {"query": "test"}}
        msg = _make_msg(payload)

        await listener._handle_task(msg)

        mock_agent._handle_task.assert_awaited_once()
        raw_arg, url_arg = mock_agent._handle_task.call_args.args
        assert json.loads(raw_arg) == payload
        assert url_arg == "http://orchestra:8001"

    @pytest.mark.asyncio
    async def test_sets_busy_then_idle(self, listener, mock_agent, mock_redis):
        payload = {"task_id": "t-2", "action": "investigate", "params": {}}
        msg = _make_msg(payload)

        statuses: list[str] = []

        async def capture_health(key, mapping):
            statuses.append(mapping["status"])
            return 1

        mock_redis.hset = capture_health

        await listener._handle_task(msg)

        assert "BUSY" in statuses
        assert statuses[-1] == "IDLE"

    @pytest.mark.asyncio
    async def test_handles_agent_exception_gracefully(self, listener, mock_agent):
        mock_agent._handle_task.side_effect = RuntimeError("search failed")
        payload = {"task_id": "t-3", "action": "investigate", "params": {}}
        msg = _make_msg(payload)

        await listener._handle_task(msg)  # should not raise

        assert listener._current_task_count == 0

    @pytest.mark.asyncio
    async def test_task_count_decrements_on_completion(self, listener, mock_agent):
        payload = {"task_id": "t-4", "action": "investigate", "params": {}}
        msg = _make_msg(payload)

        await listener._handle_task(msg)

        assert listener._current_task_count == 0


class TestListenTasks:
    @pytest.mark.asyncio
    async def test_processes_messages_from_pubsub(self, listener, mock_agent):
        payloads = [
            {"task_id": f"t-{i}", "action": "investigate", "params": {}}
            for i in range(3)
        ]
        messages = [_make_msg(p) for p in payloads]

        async def _fake_listen():
            for m in messages:
                yield m

        mock_cassiopeia = AsyncMock()
        mock_cassiopeia.listen = _fake_listen
        listener._cassiopeia = mock_cassiopeia

        await listener.listen_tasks()
        await asyncio.sleep(0.05)  # allow created tasks to finish

        assert mock_agent._handle_task.call_count == 3

    @pytest.mark.asyncio
    async def test_cancelled_error_exits_cleanly(self, listener):
        async def _hang():
            await asyncio.sleep(10)
            yield  # pragma: no cover

        mock_cassiopeia = AsyncMock()
        mock_cassiopeia.listen = _hang
        listener._cassiopeia = mock_cassiopeia

        task = asyncio.create_task(listener.listen_tasks())
        await asyncio.sleep(0.01)
        task.cancel()
        await task  # CancelledError는 내부에서 처리됨 — 정상 종료

        assert not task.cancelled()


class TestUpdateHealth:
    @pytest.mark.asyncio
    async def test_writes_health_hash(self, listener, mock_redis):
        await listener._update_health("IDLE")

        mock_redis.hset.assert_awaited_once()
        call_kwargs = mock_redis.hset.call_args
        mapping = call_kwargs.kwargs.get("mapping") or call_kwargs.args[1]
        assert mapping["status"] == "IDLE"
        assert mapping["agent_id"] == "research-agent"

    @pytest.mark.asyncio
    async def test_health_error_does_not_raise(self, listener, mock_redis):
        mock_redis.hset.side_effect = ConnectionError("redis down")

        await listener._update_health("IDLE")  # should not raise
