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

HANDLE_TASK_TEST_CASES = [
    {
        "id": "list_schedules_success",
        "mock_calendar": {"get_events": []},
        "mock_brain": BrainDecision(action="list_schedules", params={"start_time": "2026-05-01T00:00:00", "end_time": "2026-05-31T23:59:59"}),
        "msg_action": "list_schedules",
        "msg_params": {"start_time": "2026-05-01T00:00:00", "end_time": "2026-05-31T23:59:59"},
        "expected_status": "COMPLETED",
        "expected_result_data": {"status": "success", "events": []}
    },
    {
        "id": "add_schedule_success",
        "mock_calendar": {"create_event": "new-event-123"},
        "mock_brain": BrainDecision(action="add_schedule", params={"event": {"title": "테스트 미팅", "start_time": "2026-05-10T10:00:00", "end_time": "2026-05-10T11:00:00"}}),
        "msg_action": "add_schedule",
        "msg_params": {"event": {"title": "테스트 미팅", "start_time": "2026-05-10T10:00:00", "end_time": "2026-05-10T11:00:00"}},
        "expected_status": "COMPLETED",
        "expected_result_data": {"status": "success", "event_id": "new-event-123"}
    },
    {
        "id": "modify_schedule_success",
        "mock_calendar": {"update_event": True},
        "mock_brain": BrainDecision(action="modify_schedule", params={"event_id": "existing-event", "event": {"title": "수정된 미팅", "start_time": "2026-05-10T14:00:00", "end_time": "2026-05-10T15:00:00"}}),
        "msg_action": "modify_schedule",
        "msg_params": {"event_id": "existing-event", "event": {"title": "수정된 미팅", "start_time": "2026-05-10T14:00:00", "end_time": "2026-05-10T15:00:00"}},
        "expected_status": "COMPLETED",
        "expected_result_data": {"status": "success"}
    },
    {
        "id": "remove_schedule_success",
        "mock_calendar": {"delete_event": True},
        "mock_brain": BrainDecision(action="remove_schedule", params={"event_id": "event-to-delete"}),
        "msg_action": "remove_schedule",
        "msg_params": {"event_id": "event-to-delete"},
        "expected_status": "COMPLETED",
        "expected_result_data": {"status": "success"}
    },
    {
        "id": "unknown_action_failed",
        "mock_calendar": {},
        "mock_brain": BrainDecision(action="unknown", params={}),
        "msg_action": "unknown_calendar_action",
        "msg_params": {},
        "expected_status": "FAILED",
        "expected_result_data": None
    },
    {
        "id": "direct_response_success",
        "mock_calendar": {},
        "mock_brain": BrainDecision(action="direct_response", params={"message": "네, 2026년 일정입니다."}, suggested_reply="네, 2026년 일정입니다."),
        "msg_action": "schedule_action",
        "msg_params": {"user_request": "몇 년도 일정이니?"},
        "expected_status": "COMPLETED",
        "expected_result_data": {"status": "success", "action": "direct_response", "message": "네, 2026년 일정입니다."}
    }
]

class TestHandleTask:
    @pytest.mark.parametrize("case", HANDLE_TASK_TEST_CASES, ids=lambda c: c["id"])
    async def test_handle_task_scenarios(self, agent, mock_calendar_provider, case):
        # 1. Calendar Provider 동적 Mock 설정
        for method, return_val in case.get("mock_calendar", {}).items():
            setattr(mock_calendar_provider, method, AsyncMock(return_value=return_val))
            
        # 2. AgentBrain 결정 Mock 설정
        agent.brain.analyze_task = AsyncMock(return_value=case["mock_brain"])
        
        # 3. 수신 메시지 생성
        msg = _make_sdk_message(
            case["msg_action"], 
            task_id=f"t-{case['id']}", 
            params=case.get("msg_params", {})
        )
        
        # 4. 결과 보고 파이프라인 Mocking
        agent._report_result = AsyncMock()
        
        # 5. 에이전트 실행
        await agent._handle_task(msg, "http://cassiopeia:8001")
        
        # 6. 검증
        kwargs = agent._report_result.await_args.kwargs
        assert kwargs["status"] == case["expected_status"]
        assert kwargs["task_id"] == f"t-{case['id']}"
        
        if case["expected_result_data"] is not None:
            for k, v in case["expected_result_data"].items():
                assert kwargs["result_data"]["data"][k] == v


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
