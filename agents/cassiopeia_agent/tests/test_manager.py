"""
manager.py 테스트
- resolve_placeholders(): 치환 / 중첩 경로 / 누락 step
- _build_dispatch_message(): 구조 검증
- receive_agent_result(): Redis push
- wait_for_result(): 성공 / 타임아웃 → DLQ
- _push_to_dlq(): 저장 확인
- process_task(): single / direct_response / clarification / multi_step
- _route_single(): 에이전트 불가 시 에러 전송 / requires_approval 승인·거부
- run_plan(): 단계 실패 중단 / 플레이스홀더 치환 / 단계별 승인·거부
- request_user_approval(): 승인 / 거부 / 타임아웃
- _handle_agent_result(): 성공 / 실패
- _get_comm_queue(): 플랫폼별 라우팅
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.cassiopeia_agent.manager import (
    CassiopeiaManager,
    _build_dispatch_message,
    resolve_placeholders,
)
from agents.cassiopeia_agent.models import (
    ClarificationNLUResult,
    DirectResponseNLUResult,
    MultiStepNLUResult,
    NLUMetadata,
    PlanStep,
    PlanStepMetadata,
    SingleNLUResult,
)

_REQUESTER = {"user_id": "user-1", "channel_id": "ch-1"}

_BASE_TASK = {
    "task_id": "task-001",
    "session_id": "sess-001",
    "requester": _REQUESTER,
    "content": "테스트 요청",
    "source": "slack",
    "thread_ts": None,
}


# ── resolve_placeholders ──────────────────────────────────────────────────────

class TestResolvePlaceholders:
    def test_basic_substitution(self):
        params = {"input": "{{step_1.result.text}}"}
        results = {1: {"text": "hello"}}
        resolved = resolve_placeholders(params, results)
        assert resolved["input"] == "hello"

    def test_nested_path(self):
        params = {"val": "{{step_1.result.data}}"}
        results = {1: {"data": "nested_value"}}
        resolved = resolve_placeholders(params, results)
        assert resolved["val"] == "nested_value"

    def test_missing_step_returns_empty_string(self):
        params = {"val": "{{step_99.result.text}}"}
        resolved = resolve_placeholders(params, {})
        assert resolved["val"] == ""

    def test_missing_field_returns_empty_string(self):
        params = {"val": "{{step_1.result.missing_field}}"}
        results = {1: {"other": "x"}}
        resolved = resolve_placeholders(params, results)
        assert resolved["val"] == ""

    def test_multiple_placeholders(self):
        params = {"a": "{{step_1.result.x}}", "b": "{{step_2.result.y}}"}
        results = {1: {"x": "val1"}, 2: {"y": "val2"}}
        resolved = resolve_placeholders(params, results)
        assert resolved["a"] == "val1"
        assert resolved["b"] == "val2"

    def test_no_placeholders_unchanged(self):
        params = {"key": "static_value"}
        resolved = resolve_placeholders(params, {})
        assert resolved["key"] == "static_value"

    def test_empty_params(self):
        resolved = resolve_placeholders({}, {1: {"x": "y"}})
        assert resolved == {}


# ── _build_dispatch_message ───────────────────────────────────────────────────

class TestBuildDispatchMessage:
    def test_required_fields_present(self):
        msg = _build_dispatch_message(
            "task-1", "sess-1", "file_agent", "read_file",
            {"path": "/tmp"}, _REQUESTER, timeout=120,
        )
        assert msg["task_id"] == "task-1"
        assert msg["agent"] == "file_agent"
        assert msg["action"] == "read_file"
        assert msg["timeout"] == 120
        assert msg["version"] == "1.1"

    def test_default_retry_info(self):
        msg = _build_dispatch_message(
            "t", "s", "file_agent", "read_file", {}, _REQUESTER, timeout=60,
        )
        assert msg["retry_info"]["count"] == 0
        assert msg["retry_info"]["max_retries"] == 3

    def test_step_info_in_metadata(self):
        msg = _build_dispatch_message(
            "t", "s", "file_agent", "read_file", {}, _REQUESTER, timeout=60,
            step_info={"current": 1, "total": 3},
        )
        assert msg["metadata"]["step_info"]["current"] == 1

    def test_requires_approval_in_metadata(self):
        msg = _build_dispatch_message(
            "t", "s", "file_agent", "read_file", {}, _REQUESTER, timeout=60,
            requires_approval=True,
        )
        assert msg["metadata"]["requires_user_approval"] is True


# ── receive_agent_result ──────────────────────────────────────────────────────

class TestReceiveAgentResult:
    async def test_pushes_to_results_queue(self, manager, fake_redis):
        result = {
            "task_id": "task-1", "agent": "file_agent",
            "status": "COMPLETED", "result_data": {}, "error": None, "usage_stats": {},
        }
        await manager.receive_agent_result(result)
        raw = await fake_redis.lpop("cassiopeia:results:task-1")
        assert raw is not None
        assert json.loads(raw)["status"] == "COMPLETED"


# ── wait_for_result ───────────────────────────────────────────────────────────

class TestWaitForResult:
    async def test_success_returns_result(self, manager, fake_redis):
        result = {"task_id": "t1", "status": "COMPLETED", "result_data": {"summary": "완료"}}
        await fake_redis.rpush("cassiopeia:results:t1", json.dumps(result))
        outcome = await manager.wait_for_result("t1", timeout=5)
        assert outcome["status"] == "COMPLETED"

    async def test_timeout_returns_failed(self, manager):
        with patch("agents.cassiopeia_agent.manager._BLPOP_TIMEOUT", 1):
            outcome = await manager.wait_for_result("nonexistent", timeout=1)
        assert outcome["status"] == "FAILED"
        assert outcome["error"]["code"] == "TIMEOUT"

    async def test_timeout_pushes_to_dlq(self, manager, fake_redis):
        with patch("agents.cassiopeia_agent.manager._BLPOP_TIMEOUT", 1):
            await manager.wait_for_result("nonexistent", timeout=1)
        dlq_len = await fake_redis.llen("cassiopeia:dlq")
        assert dlq_len == 1

    async def test_dlq_entry_contains_task_id(self, manager, fake_redis):
        with patch("agents.cassiopeia_agent.manager._BLPOP_TIMEOUT", 1):
            await manager.wait_for_result("task-xyz", timeout=1)
        raw = await fake_redis.lpop("cassiopeia:dlq")
        entry = json.loads(raw)
        assert entry["task_id"] == "task-xyz"
        assert entry["reason"] == "timeout"


# ── _push_to_dlq ─────────────────────────────────────────────────────────────

class TestPushToDlq:
    async def test_stores_entry(self, manager, fake_redis):
        await manager._push_to_dlq("http_failed", "task-1", {"code": "NETWORK"})
        raw = await fake_redis.lpop("cassiopeia:dlq")
        assert raw is not None
        entry = json.loads(raw)
        assert entry["task_id"] == "task-1"
        assert entry["reason"] == "http_failed"


# ── _get_comm_receiver ───────────────────────────────────────────────────────────

class TestGetCommReceiver:
    def test_slack_source(self, manager):
        assert manager._get_comm_receiver("slack") == "communication_agent"

    def test_discord_source(self, manager):
        assert manager._get_comm_receiver("discord") == "discord_communication_agent"

    def test_unknown_source_defaults_to_slack(self, manager):
        assert manager._get_comm_receiver("unknown_platform") == "communication_agent"


# ── process_task: direct_response ─────────────────────────────────────────────

class TestProcessTaskDirectResponse:
    async def test_sends_answer_to_comm_agent(self, manager, nlu_engine):
        from shared_core.llm.interfaces import LLMUsage
        nlu_engine._provider.generate_response.return_value = (
            json.dumps({
                "type": "direct_response", "intent": "chitchat",
                "params": {"answer": "안녕하세요!"},
                "metadata": {"reason": "인사", "confidence_score": 1.0, "requires_user_approval": False},
            }),
            LLMUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )
        await manager.process_task(_BASE_TASK)
        
        manager._cassiopeia.send_message.assert_awaited()
        call_args = manager._cassiopeia.send_message.call_args
        assert call_args[1]["action"] == "send_message"
        assert "안녕하세요!" in call_args[1]["payload"]["content"]


# ── process_task: clarification ───────────────────────────────────────────────

class TestProcessTaskClarification:
    async def test_sends_question_to_comm_agent(self, manager, nlu_engine):
        from shared_core.llm.interfaces import LLMUsage
        nlu_engine._provider.generate_response.return_value = (
            json.dumps({
                "type": "clarification", "intent": "unclear",
                "selected_agent": "communication_agent", "action": "ask_clarification",
                "params": {"question": "무엇을 원하시나요?", "options": ["A", "B"]},
                "metadata": {"reason": "r", "confidence_score": 0.2, "requires_user_approval": False},
            }),
            LLMUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )
        await manager.process_task(_BASE_TASK)
        manager._cassiopeia.send_message.assert_awaited()
        msg = manager._cassiopeia.send_message.call_args[1]["payload"]
        assert "무엇을 원하시나요?" in msg["content"]

    async def test_clarification_with_options_includes_bullet_list(self, manager, nlu_engine):
        from shared_core.llm.interfaces import LLMUsage
        nlu_engine._provider.generate_response.return_value = (
            json.dumps({
                "type": "clarification", "intent": "unclear",
                "selected_agent": "communication_agent", "action": "ask_clarification",
                "params": {"question": "선택해주세요", "options": ["옵션A", "옵션B"]},
                "metadata": {"reason": "r", "confidence_score": 0.2, "requires_user_approval": False},
            }),
            LLMUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )
        await manager.process_task(_BASE_TASK)
        manager._cassiopeia.send_message.assert_awaited()
        msg = manager._cassiopeia.send_message.call_args[1]["payload"]
        assert "옵션A" in msg["content"]


# ── _route_single: 에이전트 불가 ─────────────────────────────────────────────

class TestRouteSingleAgentUnavailable:
    async def test_sends_error_when_agent_not_ready(self, manager, health_monitor, fake_redis):
        # 에이전트 미등록 → NOT_FOUND
        nlu_result = SingleNLUResult(
            type="single", intent="파일 읽기",
            selected_agent="file_agent", action="read_file", params={},
            metadata={"reason": "r", "confidence_score": 0.9, "requires_user_approval": False},
        )
        await manager._route_single(nlu_result, _BASE_TASK)
        manager._cassiopeia.send_message.assert_awaited()
        call_args = manager._cassiopeia.send_message.call_args
        msg = call_args[1]["payload"]
        assert "file_agent" in msg["content"]
        assert "오류" in msg["content"] or "불가" in msg["content"]


# ── _route_single: 성공 ───────────────────────────────────────────────────────

class TestRouteSingleSuccess:
    async def _register_agent(self, hm, fake_redis):
        from datetime import datetime, timezone
        await hm._redis.hset("agents:registry", "file_agent", json.dumps({
            "name": "file_agent", "capabilities": [], "lifecycle_type": "long_running",
            "nlu_description": "", "permission_preset": "standard", "allow_llm_access": False,
        }))
        await hm._redis.hset("agent:file_agent:health", mapping={
            "status": "IDLE",
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        })

    async def test_dispatches_to_agent_queue(self, manager, health_monitor, fake_redis):
        await self._register_agent(health_monitor, fake_redis)
        agent_result = json.dumps({
            "task_id": "placeholder", "agent": "file_agent",
            "status": "COMPLETED", "result_data": {"summary": "완료", "content": ""},
            "error": None, "usage_stats": {},
        })

        nlu_result = SingleNLUResult(
            type="single", intent="파일 읽기",
            selected_agent="file_agent", action="read_file", params={"path": "/tmp"},
            metadata={"reason": "r", "confidence_score": 0.9, "requires_user_approval": False},
        )

        async def _fake_wait(task_id, timeout=300):
            return json.loads(agent_result)

        with patch.object(manager, "wait_for_result", side_effect=_fake_wait):
            await manager._route_single(nlu_result, _BASE_TASK)

        manager._cassiopeia.send_message.assert_awaited()
        call_args_list = manager._cassiopeia.send_message.call_args_list
        assert any(call[1]["receiver"] == "file_agent" for call in call_args_list)

    async def test_result_sent_to_comm_agent(self, manager, health_monitor, fake_redis):
        await self._register_agent(health_monitor, fake_redis)
        nlu_result = SingleNLUResult(
            type="single", intent="파일 읽기",
            selected_agent="file_agent", action="read_file", params={},
            metadata={"reason": "r", "confidence_score": 0.9, "requires_user_approval": False},
        )

        async def _fake_wait(task_id, timeout=300):
            return {"task_id": task_id, "status": "COMPLETED",
                    "result_data": {"summary": "읽기 완료"}, "error": None, "agent": "file_agent"}

        with patch.object(manager, "wait_for_result", side_effect=_fake_wait):
            await manager._route_single(nlu_result, _BASE_TASK)

        manager._cassiopeia.send_message.assert_awaited()
        call_args = manager._cassiopeia.send_message.call_args
        msg = call_args[1]["payload"]
        assert "읽기 완료" in msg["content"]


# ── run_plan ──────────────────────────────────────────────────────────────────

class TestRunPlan:
    def _make_plan(self, steps=2) -> MultiStepNLUResult:
        plan = [
            PlanStep(
                step=i, selected_agent="file_agent", action="read_file",
                params={"path": f"/tmp/{i}.txt"}, depends_on=[],
                metadata=PlanStepMetadata(reason="r"),
            )
            for i in range(1, steps + 1)
        ]
        return MultiStepNLUResult(
            type="multi_step", intent="복합",
            plan=plan,
            metadata={"reason": "r", "confidence_score": 0.8, "requires_user_approval": False},
        )

    async def test_all_steps_dispatched(self, manager, fake_redis):
        nlu_result = self._make_plan(steps=2)
        call_count = 0

        async def _fake_wait(task_id, timeout=300):
            nonlocal call_count
            call_count += 1
            return {"task_id": task_id, "status": "COMPLETED",
                    "result_data": {"summary": f"step {call_count} 완료"}, "error": None}

        with patch.object(manager, "wait_for_result", side_effect=_fake_wait):
            await manager.run_plan(nlu_result, _BASE_TASK)

        assert call_count == 2

    async def test_step_failure_aborts_remaining(self, manager, fake_redis):
        nlu_result = self._make_plan(steps=3)
        call_count = 0

        async def _fake_wait(task_id, timeout=300):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"task_id": task_id, "status": "FAILED",
                        "result_data": {}, "error": {"code": "INTERNAL_ERROR", "message": "오류"}}
            return {"task_id": task_id, "status": "COMPLETED",
                    "result_data": {"summary": "완료"}, "error": None}

        with patch.object(manager, "wait_for_result", side_effect=_fake_wait):
            await manager.run_plan(nlu_result, _BASE_TASK)

        assert call_count == 1  # 첫 단계에서 중단

    async def test_placeholder_resolved_between_steps(self, manager, fake_redis):
        plan = [
            PlanStep(step=1, selected_agent="file_agent", action="read_file",
                     params={"path": "/tmp/a.txt"}, depends_on=[],
                     metadata=PlanStepMetadata(reason="r")),
            PlanStep(step=2, selected_agent="archive_agent", action="create_page",
                     params={"title": "결과", "content": "{{step_1.result.text}}"}, depends_on=[1],
                     metadata=PlanStepMetadata(reason="r")),
        ]
        nlu_result = MultiStepNLUResult(
            type="multi_step", intent="복합", plan=plan,
            metadata={"reason": "r", "confidence_score": 0.85, "requires_user_approval": False},
        )

        dispatched_params = {}

        async def _fake_wait(task_id, timeout=300):
            return {"task_id": task_id, "status": "COMPLETED",
                    "result_data": {"text": "파일 내용"}, "error": None}

        original_dispatch = manager._dispatch_to_agent

        async def _capture_dispatch(agent_name, dispatch):
            if agent_name == "archive_agent":
                dispatched_params.update(dispatch["params"])
            await original_dispatch(agent_name, dispatch)

        with patch.object(manager, "wait_for_result", side_effect=_fake_wait), \
             patch.object(manager, "_dispatch_to_agent", side_effect=_capture_dispatch):
            await manager.run_plan(nlu_result, _BASE_TASK)

        assert dispatched_params.get("content") == "파일 내용"


# ── request_user_approval ─────────────────────────────────────────────────────

class TestRequestUserApproval:
    async def test_approved(self, manager, fake_redis):
        result = {"result_data": {"summary": "삭제 예정"}, "agent": "file_agent"}

        original_send_message = manager._cassiopeia.send_message
        async def _fake_send_message(action, payload, receiver):
            if action == "request_approval":
                approval_id = payload["task_id"]
                await fake_redis.rpush(
                    f"cassiopeia:approval:{approval_id}",
                    json.dumps({"action": "approve"}),
                )
            return True
        manager._cassiopeia.send_message = __import__("unittest.mock").mock.AsyncMock(side_effect=_fake_send_message)
        approved = await manager.request_user_approval(result, _BASE_TASK)
        assert approved is True

    async def test_rejected(self, manager, fake_redis):
        result = {"result_data": {"summary": "삭제 예정"}, "agent": "file_agent"}

        async def _fake_send_message(action, payload, receiver):
            if action == "request_approval":
                approval_id = payload["task_id"]
                await fake_redis.rpush(
                    f"cassiopeia:approval:{approval_id}",
                    json.dumps({"action": "reject"}),
                )
            return True
        manager._cassiopeia.send_message = AsyncMock(side_effect=_fake_send_message)

        approved = await manager.request_user_approval(result, _BASE_TASK)
        assert approved is False

    async def test_timeout_returns_false(self, manager):
        result = {"result_data": {"summary": "대기 중"}, "agent": "file_agent"}
        
        async def _fake_send_message(*args, **kwargs):
            return True
            
        manager._cassiopeia.send_message = AsyncMock(side_effect=_fake_send_message)
        
        with patch.object(manager._redis, "blpop", new_callable=AsyncMock) as mock_blpop:
            mock_blpop.return_value = None
            approved = await manager.request_user_approval(result, _BASE_TASK)
            
        assert approved is False


# ── _handle_agent_result ──────────────────────────────────────────────────────

class TestHandleAgentResult:
    async def test_success_sends_to_comm(self, manager, fake_redis):
        result = {
            "task_id": "t1", "status": "COMPLETED",
            "result_data": {"summary": "성공 요약", "content": ""},
            "agent": "file_agent", "error": None,
        }
        await manager._handle_agent_result(result, _BASE_TASK, requires_approval=False)
        manager._cassiopeia.send_message.assert_awaited()
        msg = manager._cassiopeia.send_message.call_args[1]["payload"]
        assert "성공 요약" in msg["content"]

    async def test_failure_sends_error_message(self, manager, fake_redis):
        result = {
            "task_id": "t1", "status": "FAILED",
            "result_data": {},
            "agent": "file_agent",
            "error": {"code": "INTERNAL_ERROR", "message": "처리 중 오류 발생"},
        }
        await manager._handle_agent_result(result, _BASE_TASK, requires_approval=False)
        manager._cassiopeia.send_message.assert_awaited()
        msg = manager._cassiopeia.send_message.call_args[1]["payload"]
        assert "오류" in msg["content"]

    async def test_content_appended_to_summary(self, manager, fake_redis):
        result = {
            "task_id": "t1", "status": "COMPLETED",
            "result_data": {"summary": "요약", "content": "상세 내용"},
            "agent": "file_agent", "error": None,
        }
        await manager._handle_agent_result(result, _BASE_TASK, requires_approval=False)
        manager._cassiopeia.send_message.assert_awaited()
        msg = manager._cassiopeia.send_message.call_args[1]["payload"]
        assert "요약" in msg["content"]
        assert "상세 내용" in msg["content"]


# ── process_task: multi_step → run_plan 위임 ──────────────────────────────────

class TestProcessTaskMultiStep:
    async def test_delegates_to_run_plan(self, manager, nlu_engine):
        from shared_core.llm.interfaces import LLMUsage
        nlu_engine._provider.generate_response.return_value = (
            json.dumps({
                "type": "multi_step", "intent": "복합 작업",
                "plan": [
                    {
                        "step": 1, "selected_agent": "file_agent", "action": "read_file",
                        "params": {}, "depends_on": [],
                        "metadata": {"reason": "r", "requires_user_approval": False},
                    }
                ],
                "metadata": {"reason": "r", "confidence_score": 0.8, "requires_user_approval": False},
            }),
            LLMUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )
        with patch.object(manager, "run_plan", new_callable=AsyncMock) as mock_run:
            await manager.process_task(_BASE_TASK)

        mock_run.assert_awaited_once()
        called_nlu = mock_run.call_args[0][0]
        assert called_nlu.type == "multi_step"
        assert len(called_nlu.plan) == 1

    async def test_passes_original_task_to_run_plan(self, manager, nlu_engine):
        from shared_core.llm.interfaces import LLMUsage
        nlu_engine._provider.generate_response.return_value = (
            json.dumps({
                "type": "multi_step", "intent": "복합",
                "plan": [
                    {
                        "step": 1, "selected_agent": "file_agent", "action": "read_file",
                        "params": {}, "depends_on": [],
                        "metadata": {"reason": "r", "requires_user_approval": False},
                    }
                ],
                "metadata": {"reason": "r", "confidence_score": 0.85, "requires_user_approval": False},
            }),
            LLMUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )
        with patch.object(manager, "run_plan", new_callable=AsyncMock) as mock_run:
            await manager.process_task(_BASE_TASK)

        called_task = mock_run.call_args[0][1]
        assert called_task["task_id"] == _BASE_TASK["task_id"]


# ── _route_single: requires_approval ─────────────────────────────────────────

class TestRouteSingleWithApproval:
    async def _register_file_agent(self, hm):
        await hm._redis.hset("agents:registry", "file_agent", json.dumps({
            "name": "file_agent", "capabilities": [], "lifecycle_type": "long_running",
            "nlu_description": "", "permission_preset": "standard", "allow_llm_access": False,
        }))
        await hm._redis.hset("agent:file_agent:health", mapping={
            "status": "IDLE",
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        })

    async def test_approved_sends_result_to_comm(self, manager, health_monitor, fake_redis):
        await self._register_file_agent(health_monitor)
        nlu_result = SingleNLUResult(
            type="single", intent="파일 삭제",
            selected_agent="file_agent", action="delete_file",
            params={"path": "/tmp/a.txt"},
            metadata={"reason": "r", "confidence_score": 0.9, "requires_user_approval": True},
        )

        async def _fake_wait(task_id, timeout=300):
            return {
                "task_id": task_id, "status": "COMPLETED",
                "result_data": {"summary": "삭제 완료"}, "error": None, "agent": "file_agent",
            }

        async def _fake_send_message(action, payload, receiver):
            if action == "request_approval":
                import json
                await fake_redis.rpush(f"cassiopeia:approval:{payload['task_id']}", json.dumps({"action": "approve"}))
            return True
        manager._cassiopeia.send_message = __import__("unittest.mock").mock.AsyncMock(side_effect=_fake_send_message)

        with patch.object(manager, "wait_for_result", side_effect=_fake_wait):
            await manager._route_single(nlu_result, _BASE_TASK)

        # 승인 후 최종 결과가 comm 큐에 전달되어야 함
        msg = manager._cassiopeia.send_message.call_args[1]["payload"]
        assert "삭제 완료" in msg["content"]

    async def test_rejected_does_not_forward_result(self, manager, health_monitor, fake_redis):
        await self._register_file_agent(health_monitor)
        nlu_result = SingleNLUResult(
            type="single", intent="파일 삭제",
            selected_agent="file_agent", action="delete_file",
            params={},
            metadata={"reason": "r", "confidence_score": 0.9, "requires_user_approval": True},
        )

        async def _fake_wait(task_id, timeout=300):
            return {
                "task_id": task_id, "status": "COMPLETED",
                "result_data": {"summary": "삭제 예정"}, "error": None, "agent": "file_agent",
            }

        async def _fake_send_message(action, payload, receiver):
            if action == "request_approval":
                import json
                await fake_redis.rpush(f"cassiopeia:approval:{payload['task_id']}", json.dumps({"action": "reject"}))
            return True
        manager._cassiopeia.send_message = __import__("unittest.mock").mock.AsyncMock(side_effect=_fake_send_message)

        with patch.object(manager, "wait_for_result", side_effect=_fake_wait):
            await manager._route_single(nlu_result, _BASE_TASK)

        # 거부 시 취소 메시지가 전송되어야 함
        msg = manager._cassiopeia.send_message.call_args[1]["payload"]
        assert "취소되었습니다" in msg["content"]


# ── run_plan: 단계별 requires_approval ───────────────────────────────────────

class TestRunPlanWithApproval:
    def _make_plan_with_approval(self) -> MultiStepNLUResult:
        return MultiStepNLUResult(
            type="multi_step", intent="복합",
            plan=[
                PlanStep(
                    step=1, selected_agent="file_agent", action="read_file",
                    params={}, depends_on=[],
                    metadata=PlanStepMetadata(reason="r", requires_user_approval=True),
                ),
                PlanStep(
                    step=2, selected_agent="archive_agent", action="create_page",
                    params={}, depends_on=[1],
                    metadata=PlanStepMetadata(reason="r"),
                ),
            ],
            metadata={"reason": "r", "confidence_score": 0.8, "requires_user_approval": False},
        )

    async def test_approved_continues_to_next_step(self, manager, fake_redis):
        nlu_result = self._make_plan_with_approval()
        call_count = 0

        async def _fake_wait(task_id, timeout=300):
            nonlocal call_count
            call_count += 1
            return {
                "task_id": task_id, "status": "COMPLETED",
                "result_data": {"summary": f"step {call_count} 완료"}, "error": None,
            }

        async def _fake_send_message(action, payload, receiver):
            if action == "request_approval":
                import json
                await fake_redis.rpush(f"cassiopeia:approval:{payload['task_id']}", json.dumps({"action": "approve"}))
            return True
        manager._cassiopeia.send_message = __import__("unittest.mock").mock.AsyncMock(side_effect=_fake_send_message)

        with patch.object(manager, "wait_for_result", side_effect=_fake_wait):
            await manager.run_plan(nlu_result, _BASE_TASK)

        assert call_count == 2

    async def test_rejected_aborts_remaining_steps(self, manager, fake_redis):
        nlu_result = self._make_plan_with_approval()
        call_count = 0

        async def _fake_wait(task_id, timeout=300):
            nonlocal call_count
            call_count += 1
            return {
                "task_id": task_id, "status": "COMPLETED",
                "result_data": {"summary": "step 1 완료"}, "error": None,
            }

        async def _fake_send_message(action, payload, receiver):
            if action == "request_approval":
                import json
                await fake_redis.rpush(f"cassiopeia:approval:{payload['task_id']}", json.dumps({"action": "reject"}))
            return True
        manager._cassiopeia.send_message = __import__("unittest.mock").mock.AsyncMock(side_effect=_fake_send_message)

        with patch.object(manager, "wait_for_result", side_effect=_fake_wait):
            await manager.run_plan(nlu_result, _BASE_TASK)

        # 거부 → 실행되지 않고 중단
        assert call_count == 0
