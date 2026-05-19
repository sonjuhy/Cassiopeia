"""
[TDD] agents/schedule_agent/agent.py — cassiopeia-sdk 통신 단위 테스트

변경 사항:
- BLPOP(Redis Lists) 대신 CassiopeiaClient.listen() (Pub/Sub) 으로 메시지 수신
- _handle_task: raw JSON 문자열 → cassiopeia AgentMessage 객체 수신
- run(): aioredis.blpop 루프 제거, CassiopeiaClient 기반 루프 사용
"""
from __future__ import annotations

import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, call
from typing import AsyncIterator

from cassiopeia_sdk.client import AgentMessage as SdkAgentMessage
from cassiopeia_sdk.brain import BrainDecision

from agents.schedule_agent.agent import ScheduleAgent
from agents.schedule_agent.config import ScheduleAgentConfig


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_calendar_provider():
    provider = MagicMock()
    provider.get_events = AsyncMock(return_value=[])
    provider.create_event = AsyncMock(return_value="event-001")
    provider.update_event = AsyncMock(return_value=True)
    provider.delete_event = AsyncMock(return_value=True)
    return provider


@pytest.fixture
def agent_config():
    return ScheduleAgentConfig(
        calendar_id="test@example.com",
        service_account_key_file=None,
        service_account_key_json=None,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )


@pytest.fixture
def agent(agent_config, mock_calendar_provider):
    return ScheduleAgent(config=agent_config, calendar_provider=mock_calendar_provider)


def _make_sdk_message(action: str, task_id: str = "t-001", params: dict | None = None) -> SdkAgentMessage:
    return SdkAgentMessage(
        sender="cassiopeia",
        receiver="schedule-agent",
        action=action,
        payload={"task_id": task_id, "params": params or {}},
    )


async def _listen_gen(*messages: SdkAgentMessage) -> AsyncIterator[SdkAgentMessage]:
    for msg in messages:
        yield msg


# ---------------------------------------------------------------------------
# _handle_task — cassiopeia AgentMessage 수신 처리
# ---------------------------------------------------------------------------

class TestHandleTask:
    async def test_handle_list_schedules_success(self, agent, mock_calendar_provider):
        mock_calendar_provider.get_events = AsyncMock(return_value=[])
        # Mock Brain
        mock_decision = BrainDecision(action="list_schedules", params={
            "start_time": "2026-05-01T00:00:00",
            "end_time": "2026-05-31T23:59:59",
        })
        agent.brain.analyze_task = AsyncMock(return_value=mock_decision)

        msg = _make_sdk_message("list_schedules", params={
            "start_time": "2026-05-01T00:00:00",
            "end_time": "2026-05-31T23:59:59",
        })
        agent._report_result = AsyncMock()

        await agent._handle_task(msg, "http://cassiopeia:8001")

        kwargs = agent._report_result.await_args.kwargs
        assert kwargs["status"] == "COMPLETED"
        assert kwargs["task_id"] == "t-001"

    async def test_handle_add_schedule_success(self, agent, mock_calendar_provider):
        mock_calendar_provider.create_event = AsyncMock(return_value="new-event-123")
        # Mock Brain
        mock_decision = BrainDecision(action="add_schedule", params={
            "event": {
                "title": "테스트 미팅",
                "start_time": "2026-05-10T10:00:00",
                "end_time": "2026-05-10T11:00:00",
            }
        })
        agent.brain.analyze_task = AsyncMock(return_value=mock_decision)

        msg = _make_sdk_message("add_schedule", task_id="t-add-01", params={
            "event": {
                "title": "테스트 미팅",
                "start_time": "2026-05-10T10:00:00",
                "end_time": "2026-05-10T11:00:00",
            }
        })
        agent._report_result = AsyncMock()

        await agent._handle_task(msg, "http://cassiopeia:8001")

        kwargs = agent._report_result.await_args.kwargs
        assert kwargs["status"] == "COMPLETED"
        assert kwargs["task_id"] == "t-add-01"

    async def test_handle_unknown_action_reports_failed(self, agent):
        # Mock Brain returning unknown action
        mock_decision = BrainDecision(action="unknown", params={})
        agent.brain.analyze_task = AsyncMock(return_value=mock_decision)

        msg = _make_sdk_message("unknown_calendar_action")
        agent._report_result = AsyncMock()

        await agent._handle_task(msg, "http://cassiopeia:8001")

        kwargs = agent._report_result.await_args.kwargs
        assert kwargs["status"] == "FAILED"

    async def test_handle_task_extracts_task_id_from_payload(self, agent, mock_calendar_provider):
        mock_calendar_provider.get_events = AsyncMock(return_value=[])
        # Mock Brain
        mock_decision = BrainDecision(action="list_schedules", params={
            "start_time": "2026-05-01T00:00:00",
            "end_time": "2026-05-31T23:59:59",
        })
        agent.brain.analyze_task = AsyncMock(return_value=mock_decision)

        msg = _make_sdk_message("list_schedules", task_id="my-sched-99", params={
            "start_time": "2026-05-01T00:00:00",
            "end_time": "2026-05-31T23:59:59",
        })
        agent._report_result = AsyncMock()

        await agent._handle_task(msg, "http://cassiopeia:8001")

        kwargs = agent._report_result.await_args.kwargs
        assert kwargs["task_id"] == "my-sched-99"

    async def test_handle_modify_schedule_success(self, agent, mock_calendar_provider):
        mock_calendar_provider.update_event = AsyncMock(return_value=True)
        # Mock Brain
        mock_decision = BrainDecision(action="modify_schedule", params={
            "event_id": "existing-event",
            "event": {
                "title": "수정된 미팅",
                "start_time": "2026-05-10T14:00:00",
                "end_time": "2026-05-10T15:00:00",
            }
        })
        agent.brain.analyze_task = AsyncMock(return_value=mock_decision)

        msg = _make_sdk_message("modify_schedule", params={
            "event_id": "existing-event",
            "event": {
                "title": "수정된 미팅",
                "start_time": "2026-05-10T14:00:00",
                "end_time": "2026-05-10T15:00:00",
            }
        })
        agent._report_result = AsyncMock()

        await agent._handle_task(msg, "http://cassiopeia:8001")

        kwargs = agent._report_result.await_args.kwargs
        assert kwargs["status"] == "COMPLETED"

    async def test_handle_remove_schedule_success(self, agent, mock_calendar_provider):
        mock_calendar_provider.delete_event = AsyncMock(return_value=True)
        # Mock Brain
        mock_decision = BrainDecision(action="remove_schedule", params={"event_id": "event-to-delete"})
        agent.brain.analyze_task = AsyncMock(return_value=mock_decision)

        msg = _make_sdk_message("remove_schedule", params={"event_id": "event-to-delete"})
        agent._report_result = AsyncMock()

        await agent._handle_task(msg, "http://cassiopeia:8001")

        kwargs = agent._report_result.await_args.kwargs
        assert kwargs["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# run() — CassiopeiaClient.listen() 사용 검증
# ---------------------------------------------------------------------------

class TestRun:
    async def test_run_uses_cassiopeia_client(self, agent, mock_calendar_provider):
        """run()이 aioredis.blpop 대신 CassiopeiaClient.listen()을 사용하는지 검증합니다."""
        mock_calendar_provider.get_events = AsyncMock(return_value=[])
        msg = _make_sdk_message("list_schedules", params={
            "start_time": "2026-05-01T00:00:00",
            "end_time": "2026-05-31T23:59:59",
        })

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.listen = MagicMock(return_value=_listen_gen(msg))

        with patch("agents.schedule_agent.agent.CassiopeiaClient", return_value=mock_client):
            with patch("agents.schedule_agent.agent.aioredis") as mock_aioredis:
                mock_redis = AsyncMock()
                mock_aioredis.from_url.return_value = mock_redis
                mock_redis.hset = AsyncMock()
                mock_redis.expire = AsyncMock()
                mock_redis.aclose = AsyncMock()

                agent._report_result = AsyncMock()
                await agent.run()

        mock_client.connect.assert_awaited_once()
        mock_client.listen.assert_called_once()

    async def test_run_creates_cassiopeia_client_with_agent_name(self, agent):
        """run()이 에이전트 이름으로 CassiopeiaClient를 생성하는지 검증합니다."""
        captured_args = {}

        async def fake_listen():
            return
            yield

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.listen = MagicMock(return_value=fake_listen())

        def capture_client(agent_id, redis_url):
            captured_args["agent_id"] = agent_id
            return mock_client

        with patch("agents.schedule_agent.agent.CassiopeiaClient", side_effect=capture_client):
            with patch("agents.schedule_agent.agent.aioredis") as mock_aioredis:
                mock_redis = AsyncMock()
                mock_aioredis.from_url.return_value = mock_redis
                mock_redis.hset = AsyncMock()
                mock_redis.expire = AsyncMock()
                mock_redis.aclose = AsyncMock()

                agent._report_result = AsyncMock()
                await agent.run()

        assert captured_args["agent_id"] == agent.agent_name

    async def test_run_disconnects_client_on_finish(self, agent):
        """run() 종료 시 CassiopeiaClient.disconnect()를 호출하는지 검증합니다."""
        async def empty_listen():
            return
            yield

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.listen = MagicMock(return_value=empty_listen())

        with patch("agents.schedule_agent.agent.CassiopeiaClient", return_value=mock_client):
            with patch("agents.schedule_agent.agent.aioredis") as mock_aioredis:
                mock_redis = AsyncMock()
                mock_aioredis.from_url.return_value = mock_redis
                mock_redis.hset = AsyncMock()
                mock_redis.expire = AsyncMock()
                mock_redis.aclose = AsyncMock()

                await agent.run()

        mock_client.disconnect.assert_awaited_once()


# ---------------------------------------------------------------------------
# _report_result — 기존 HTTP 보고 로직 유지 검증
# ---------------------------------------------------------------------------

class TestReportResult:
    async def test_report_result_posts_to_cassiopeia(self, agent):
        with patch("agents.schedule_agent.agent.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await agent._report_result(
                cassiopeia_url="http://cassiopeia:8001",
                task_id="t-001",
                status="COMPLETED",
                result_data={"summary": "ok"},
                error=None,
            )

        mock_http.post.assert_awaited_once()
        call_kwargs = mock_http.post.call_args
        assert "http://cassiopeia:8001/results" in call_kwargs[0]

    async def test_report_result_sends_to_dlq_after_max_retries(self, agent):
        with patch("agents.schedule_agent.agent.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=Exception("connection refused"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_redis = AsyncMock()
            mock_redis.rpush = AsyncMock()

            with patch("asyncio.sleep", new_callable=AsyncMock):
                await agent._report_result(
                    cassiopeia_url="http://cassiopeia:8001",
                    task_id="failed-sched-task",
                    status="FAILED",
                    result_data={},
                    error={"code": "ERR"},
                    redis=mock_redis,
                )

        mock_redis.rpush.assert_awaited_once()
        dlq_call = mock_redis.rpush.call_args
        assert "cassiopeia:dlq" in dlq_call[0]
