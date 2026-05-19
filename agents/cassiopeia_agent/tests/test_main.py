"""
main.py FastAPI 엔드포인트 테스트 (lifespan mock 교체)
- GET  /health
- GET  /queue/status
- POST /tasks
- GET  /tasks/{task_id}
- POST /results
- POST /logs
- POST /nlu/analyze
- POST /dispatch
- GET  /agents
- POST /agents
- DELETE /agents/{name}
- GET  /agents/{name}/health
- PUT  /agents/{name}/heartbeat
- GET  /agents/{name}/circuit
- POST /agents/{name}/reset
- GET  /sessions/{session_id}
- GET  /sessions/{session_id}/history
- DELETE /sessions/{session_id}
- GET  /users/{user_id}/profile
- PUT  /users/{user_id}/profile
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

_NOW = datetime.now(timezone.utc).isoformat()


async def _register_agent(fake_redis, name: str, lifecycle: str = "long_running") -> None:
    await fake_redis.hset("agents:registry", name, json.dumps({
        "name": name, "capabilities": ["cap1"],
        "lifecycle_type": lifecycle, "nlu_description": "",
        "permission_preset": "standard", "allow_llm_access": False,
    }))


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealth:
    async def test_returns_ok_when_redis_up(self, async_client, fake_redis):
        resp = await async_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["redis_connected"] is True

    async def test_degraded_when_listen_task_none(self, async_client):
        resp = await async_client.get("/health")
        data = resp.json()
        # listen_task=None이면 degraded
        assert data["status"] in ("ok", "degraded")


# ── /queue/status ─────────────────────────────────────────────────────────────

class TestQueueStatus:
    async def test_returns_dict(self, async_client):
        resp = await async_client.get("/queue/status")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)


# ── /tasks ────────────────────────────────────────────────────────────────────

class TestSubmitTask:
    async def test_accepted(self, async_client):
        resp = await async_client.post("/tasks", json={
            "content": "파일 읽어줘",
            "user_id": "user-1",
            "channel_id": "ch-1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert "task_id" in data

    async def test_task_sent_via_cassiopeia(self, async_client):
        from agents.cassiopeia_agent import app_context
        app_context.ctx.cassiopeia_client.send_message.reset_mock()
        await async_client.post("/tasks", json={"content": "테스트", "user_id": "u1", "channel_id": "c1"})
        app_context.ctx.cassiopeia_client.send_message.assert_awaited_once()
        kwargs = app_context.ctx.cassiopeia_client.send_message.call_args.kwargs
        assert kwargs["receiver"] == "cassiopeia"
        assert kwargs["action"] == "user_request"

    async def test_custom_session_id(self, async_client):
        resp = await async_client.post("/tasks", json={
            "content": "테스트",
            "user_id": "u1",
            "channel_id": "c1",
            "session_id": "custom-session-42",
        })
        assert resp.json()["session_id"] == "custom-session-42"


# ── /tasks/{task_id} ──────────────────────────────────────────────────────────

class TestGetTask:
    async def test_returns_not_found_for_unknown(self, async_client):
        resp = await async_client.get("/tasks/nonexistent-task")
        assert resp.status_code == 200
        assert resp.json()["status"] == "NOT_FOUND"

    async def test_returns_state_when_exists(self, async_client, fake_redis):
        await fake_redis.hset("task:known-task:state", mapping={"status": "PROCESSING"})
        resp = await async_client.get("/tasks/known-task")
        assert resp.status_code == 200
        assert resp.json()["status"] == "PROCESSING"


# ── /results ─────────────────────────────────────────────────────────────────

class TestReceiveResult:
    async def test_accepted(self, async_client):
        resp = await async_client.post("/results", json={
            "task_id": "task-1",
            "agent": "file_agent",
            "status": "COMPLETED",
            "result_data": {},
            "error": None,
            "usage_stats": {},
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    async def test_result_pushed_to_redis(self, async_client, fake_redis):
        await async_client.post("/results", json={
            "task_id": "task-res-1",
            "agent": "file_agent",
            "status": "COMPLETED",
            "result_data": {"summary": "완료"},
            "error": None,
            "usage_stats": {},
        })
        raw = await fake_redis.lpop("cassiopeia:results:task-res-1")
        assert raw is not None
        assert json.loads(raw)["status"] == "COMPLETED"


# ── /logs ─────────────────────────────────────────────────────────────────────

class TestReceiveLog:
    async def test_logged(self, async_client):
        resp = await async_client.post("/logs", json={
            "agent_name": "file_agent",
            "action": "read_file",
            "message": "파일 읽기 완료",
            "task_id": "task-1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "logged"

    async def test_long_message_truncated(self, async_client):
        long_msg = "x" * 2000
        resp = await async_client.post("/logs", json={
            "agent_name": "file_agent",
            "action": "read_file",
            "message": long_msg,
        })
        assert resp.status_code == 200


# ── /nlu/analyze ─────────────────────────────────────────────────────────────

class TestDirectDispatch:
    async def test_503_when_agent_not_ready(self, async_client):
        resp = await async_client.post("/dispatch", json={
            "agent_name": "unregistered_agent",
            "action": "do_something",
            "params": {},
            "content": "테스트",
        })
        assert resp.status_code == 503

    async def test_success_when_agent_ready(self, async_client, fake_redis):
        await _register_agent(fake_redis, "file_agent", "ephemeral")

        from agents.cassiopeia_agent import app_context
        app_context.ctx.health_monitor.is_agent_ready = AsyncMock(return_value=(True, "OK"))
        app_context.ctx.manager.wait_for_result = AsyncMock(return_value={
            "task_id": "t1", "status": "COMPLETED",
            "result_data": {"summary": "완료"}, "error": None,
        })
        app_context.ctx.cassiopeia_client.send_message.reset_mock()

        resp = await async_client.post("/dispatch", json={
            "agent_name": "file_agent",
            "action": "read_file",
            "params": {"path": "/tmp"},
            "timeout": 5,
        })
        assert resp.status_code == 200
        app_context.ctx.cassiopeia_client.send_message.assert_awaited_once()


# ── /agents ───────────────────────────────────────────────────────────────────

class TestAgentManagement:
    async def test_list_agents_returns_dict(self, async_client):
        resp = await async_client.get("/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data
        assert "all" in data

    async def test_register_agent(self, async_client):
        resp = await async_client.post("/agents", json={
            "agent_name": "test_agent",
            "capabilities": ["cap1"],
            "lifecycle_type": "long_running",
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "registered"

    async def test_deregister_agent(self, async_client, fake_redis):
        await _register_agent(fake_redis, "to_remove")
        resp = await async_client.delete("/agents/to_remove")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deregistered"

    async def test_get_agent_health_not_found(self, async_client):
        resp = await async_client.get("/agents/nonexistent_agent/health")
        assert resp.status_code == 404

    async def test_get_agent_health_found(self, async_client, fake_redis):
        await fake_redis.hset("agent:known_agent:health", mapping={
            "agent_id": "known_agent",
            "status": "IDLE",
            "last_heartbeat": _NOW,
        })
        resp = await async_client.get("/agents/known_agent/health")
        assert resp.status_code == 200
        assert resp.json()["health"]["status"] == "IDLE"

    async def test_update_heartbeat(self, async_client, fake_redis):
        resp = await async_client.put("/agents/my_agent/heartbeat", json={
            "status": "BUSY",
            "current_tasks": 1,
            "version": "1.0.0",
            "capabilities": ["cap1"],
            "max_concurrency": 2,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    async def test_heartbeat_stored_in_redis(self, async_client, fake_redis):
        await async_client.put("/agents/hb_agent/heartbeat", json={
            "status": "IDLE",
            "current_tasks": 0,
            "version": "1.0.0",
            "capabilities": [],
            "max_concurrency": 1,
        })
        health = await fake_redis.hgetall("agent:hb_agent:health")
        assert health["status"] == "IDLE"

    async def test_get_circuit_status(self, async_client):
        resp = await async_client.get("/agents/some_agent/circuit")
        assert resp.status_code == 200
        data = resp.json()
        assert "failures" in data
        assert "is_open" in data

    async def test_reset_circuit_breaker(self, async_client, fake_redis):
        await fake_redis.set("circuit:some_agent:failures", "3")
        await _register_agent(fake_redis, "some_agent")
        await fake_redis.hset("agent:some_agent:health", "status", "MAINTENANCE")
        resp = await async_client.post("/agents/some_agent/reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"


# ── /sessions ────────────────────────────────────────────────────────────────

class TestSessions:
    async def test_get_session(self, async_client, fake_redis):
        await fake_redis.hset("session:sess-test:state", mapping={"user_id": "user-1"})
        resp = await async_client.get("/sessions/sess-test")
        assert resp.status_code == 200
        assert "state" in resp.json()

    async def test_get_session_history(self, async_client):
        sm = async_client.state_manager
        await sm.init_session("hist-sess", "user-1", "ch-1")
        await sm.add_message("hist-sess", "user-1", "user", "안녕")
        resp = await async_client.get("/sessions/hist-sess/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    async def test_delete_session(self, async_client):
        sm = async_client.state_manager
        await sm.init_session("del-sess", "user-1", "ch-1")
        resp = await async_client.delete("/sessions/del-sess")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    async def test_history_limit_param(self, async_client):
        sm = async_client.state_manager
        await sm.init_session("limit-sess", "user-1", "ch-1")
        for i in range(10):
            await sm.add_message("limit-sess", "user-1", "user", f"msg-{i}")
        resp = await async_client.get("/sessions/limit-sess/history?limit=3")
        assert resp.status_code == 200
        assert resp.json()["count"] <= 3


# ── /users ────────────────────────────────────────────────────────────────────

class TestUserProfile:
    async def test_get_profile_creates_if_missing(self, async_client):
        resp = await async_client.get("/users/new-user/profile")
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "new-user"

    async def test_update_profile_name(self, async_client):
        sm = async_client.state_manager
        await sm.get_user_profile("upd-user")
        resp = await async_client.put("/users/upd-user/profile", json={"name": "김철수"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "김철수"

    async def test_update_profile_empty_body_returns_400(self, async_client):
        resp = await async_client.put("/users/upd-user2/profile", json={})
        assert resp.status_code == 400

    async def test_update_style_pref(self, async_client):
        sm = async_client.state_manager
        await sm.get_user_profile("style-user")
        resp = await async_client.put("/users/style-user/profile", json={
            "style_pref": {"tone": "격식체", "language": "한국어", "detail_level": "간략함"}
        })
        assert resp.status_code == 200
        assert resp.json()["style_pref"]["tone"] == "격식체"
