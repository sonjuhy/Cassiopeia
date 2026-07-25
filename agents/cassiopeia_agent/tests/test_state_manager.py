"""
state_manager.py 테스트
- 사용자 프로필: 자동 생성 / 조회 / 업데이트
- 세션: 초기화 / 메시지 추가 / LLM 컨텍스트 빌드
- 태스크 상태: Redis 저장 / 조회
- 에이전트 로그: 삽입 / 필터 / 페이지네이션
- 세션 관리: 삭제 / 이력 / 목록
- 사용자 목록 / scan_task_ids
"""
from __future__ import annotations

import json

import pytest

from agents.cassiopeia_agent.state_manager import StateManager


# ── 사용자 프로필 ──────────────────────────────────────────────────────────────

class TestUserProfile:
    async def test_creates_default_profile_for_new_user(self, state_manager):
        profile = await state_manager.get_user_profile("user-new")
        assert profile["user_id"] == "user-new"
        assert profile["name"] == "User"
        assert "tone" in profile["style_pref"]

    async def test_returns_existing_profile(self, state_manager):
        await state_manager.get_user_profile("user-1")
        profile = await state_manager.get_user_profile("user-1")
        assert profile["user_id"] == "user-1"

    async def test_idempotent_creation(self, state_manager):
        await state_manager.get_user_profile("user-1")
        await state_manager.get_user_profile("user-1")  # 두 번째 호출
        users, total = await state_manager.list_users()
        assert total == 1

    async def test_update_name(self, state_manager):
        await state_manager.get_user_profile("user-1")
        await state_manager.update_user_profile("user-1", {"name": "홍길동"})
        profile = await state_manager.get_user_profile("user-1")
        assert profile["name"] == "홍길동"

    async def test_update_style_pref(self, state_manager):
        await state_manager.get_user_profile("user-1")
        await state_manager.update_user_profile("user-1", {"style_pref": {"tone": "격식체", "language": "한국어", "detail_level": "간략함"}})
        profile = await state_manager.get_user_profile("user-1")
        assert profile["style_pref"]["tone"] == "격식체"

    async def test_default_style_pref_structure(self, state_manager):
        profile = await state_manager.get_user_profile("user-new")
        pref = profile["style_pref"]
        assert "tone" in pref
        assert "language" in pref
        assert "detail_level" in pref


# ── 세션 초기화 ───────────────────────────────────────────────────────────────

class TestInitSession:
    async def test_creates_session_record(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "channel-1")
        sessions, total = await state_manager.list_sessions()
        assert total == 1
        assert sessions[0]["session_id"] == "sess-1"

    async def test_creates_redis_cache(self, state_manager, fake_redis):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        state = await fake_redis.hgetall("session:sess-1:state")
        assert state["user_id"] == "user-1"
        assert state["channel_id"] == "ch-1"

    async def test_redis_key_has_ttl(self, state_manager, fake_redis):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        ttl = await fake_redis.ttl("session:sess-1:state")
        assert ttl > 0

    async def test_idempotent_init(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.init_session("sess-1", "user-1", "ch-1")  # 두 번째
        sessions, total = await state_manager.list_sessions()
        assert total == 1


# ── 메시지 추가 ───────────────────────────────────────────────────────────────

class TestAddMessage:
    async def test_stores_in_sqlite(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.add_message("sess-1", "user-1", "user", "안녕하세요")
        history = await state_manager.get_session_history("sess-1")
        assert len(history) == 1
        assert history[0]["content"] == "안녕하세요"

    async def test_stores_in_redis_cache(self, state_manager, fake_redis):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.add_message("sess-1", "user-1", "user", "안녕")
        msgs = await fake_redis.lrange("session:sess-1:messages", 0, -1)
        assert len(msgs) == 1
        assert json.loads(msgs[0])["content"] == "안녕"

    async def test_redis_cache_trims_to_max_20(self, state_manager, fake_redis):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        for i in range(25):
            await state_manager.add_message("sess-1", "user-1", "user", f"msg-{i}")
        count = await fake_redis.llen("session:sess-1:messages")
        assert count == 20

    async def test_multiple_roles(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.add_message("sess-1", "user-1", "user", "질문")
        await state_manager.add_message("sess-1", "user-1", "assistant", "답변")
        history = await state_manager.get_session_history("sess-1")
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"


# ── LLM 컨텍스트 빌드 ─────────────────────────────────────────────────────────

class TestBuildContextForLlm:
    async def test_includes_user_profile(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        context = await state_manager.build_context_for_llm("sess-1", "user-1")
        combined = " ".join(str(m) for m in context)
        assert "user-1" in combined

    async def test_returns_history_in_order(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        for i in range(3):
            await state_manager.add_message("sess-1", "user-1", "user", f"msg-{i}")
        context = await state_manager.build_context_for_llm("sess-1", "user-1")
        messages = [m for m in context if "msg-" in m.get("content", "")]
        assert messages[0]["content"] == "msg-0"
        assert messages[-1]["content"] == "msg-2"

    async def test_limits_to_history_limit(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        for i in range(15):
            await state_manager.add_message("sess-1", "user-1", "user", f"msg-{i}")
        context = await state_manager.build_context_for_llm("sess-1", "user-1")
        msg_items = [m for m in context if "msg-" in m.get("content", "")]
        assert len(msg_items) <= 10

    async def test_gemini_provider_uses_model_role(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.add_message("sess-1", "user-1", "assistant", "답변")
        context = await state_manager.build_context_for_llm("sess-1", "user-1", provider="gemini")
        roles = [m["role"] for m in context]
        assert "model" in roles

    async def test_non_gemini_uses_system_role(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        context = await state_manager.build_context_for_llm("sess-1", "user-1", provider="claude")
        assert context[0]["role"] == "system"


# ── 태스크 상태 ───────────────────────────────────────────────────────────────

class TestTaskState:
    async def test_update_and_get(self, state_manager):
        await state_manager.update_task_state("task-1", {"status": "PROCESSING"})
        state = await state_manager.get_task_state("task-1")
        assert state["status"] == "PROCESSING"

    async def test_has_ttl(self, state_manager, fake_redis):
        await state_manager.update_task_state("task-1", {"status": "DONE"})
        ttl = await fake_redis.ttl("task:task-1:state")
        assert ttl > 0

    async def test_updated_at_auto_set(self, state_manager):
        await state_manager.update_task_state("task-1", {"status": "OK"})
        state = await state_manager.get_task_state("task-1")
        assert "updated_at" in state

    async def test_nonexistent_returns_empty(self, state_manager):
        state = await state_manager.get_task_state("nonexistent")
        assert state == {}

    async def test_dict_value_serialized(self, state_manager):
        await state_manager.update_task_state("task-1", {"meta": {"key": "val"}})
        state = await state_manager.get_task_state("task-1")
        assert state["meta"] is not None


# ── 에이전트 로그 ─────────────────────────────────────────────────────────────

class TestAgentLogs:
    async def test_add_and_retrieve(self, state_manager):
        await state_manager.add_agent_log("file_agent", "read_file", "파일 읽기 완료", task_id="t1")
        logs = await state_manager.get_agent_logs()
        assert len(logs) == 1
        assert logs[0]["agent_name"] == "file_agent"

    async def test_filter_by_agent_name(self, state_manager):
        await state_manager.add_agent_log("file_agent", "read_file", "msg1")
        await state_manager.add_agent_log("archive_agent", "search", "msg2")
        logs = await state_manager.get_agent_logs(agent_name="file_agent")
        assert all(l["agent_name"] == "file_agent" for l in logs)

    async def test_filter_by_action(self, state_manager):
        await state_manager.add_agent_log("file_agent", "read_file", "msg1")
        await state_manager.add_agent_log("file_agent", "write_file", "msg2")
        logs = await state_manager.get_agent_logs(action="read_file")
        assert all(l["action"] == "read_file" for l in logs)

    async def test_filter_by_task_id(self, state_manager):
        await state_manager.add_agent_log("file_agent", "read_file", "msg", task_id="task-X")
        await state_manager.add_agent_log("file_agent", "read_file", "msg", task_id="task-Y")
        logs = await state_manager.get_agent_logs(task_id="task-X")
        assert len(logs) == 1

    async def test_pagination_limit(self, state_manager):
        for i in range(10):
            await state_manager.add_agent_log("file_agent", "read_file", f"msg-{i}")
        logs = await state_manager.get_agent_logs(limit=3)
        assert len(logs) == 3

    async def test_pagination_offset(self, state_manager):
        for i in range(5):
            await state_manager.add_agent_log("file_agent", "read_file", f"msg-{i}")
        logs_all = await state_manager.get_agent_logs(limit=10)
        logs_offset = await state_manager.get_agent_logs(limit=10, offset=2)
        assert len(logs_offset) == len(logs_all) - 2

    async def test_count_logs(self, state_manager):
        for i in range(7):
            await state_manager.add_agent_log("file_agent", "read_file", f"msg-{i}")
        count = await state_manager.count_agent_logs(agent_name="file_agent")
        assert count == 7

    async def test_count_filtered(self, state_manager):
        await state_manager.add_agent_log("file_agent", "read_file", "msg")
        await state_manager.add_agent_log("archive_agent", "search", "msg")
        count = await state_manager.count_agent_logs(agent_name="archive_agent")
        assert count == 1


# ── 세션 관리 ─────────────────────────────────────────────────────────────────

class TestSessionManagement:
    async def test_list_sessions(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.init_session("sess-2", "user-1", "ch-1")
        sessions, total = await state_manager.list_sessions()
        assert total == 2

    async def test_list_sessions_pagination(self, state_manager):
        for i in range(5):
            await state_manager.init_session(f"sess-{i}", "user-1", "ch-1")
        sessions, total = await state_manager.list_sessions(limit=2)
        assert len(sessions) == 2
        assert total == 5

    async def test_delete_session_removes_from_sqlite(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.delete_session("sess-1")
        sessions, total = await state_manager.list_sessions()
        assert total == 0

    async def test_delete_session_removes_chat_history(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.add_message("sess-1", "user-1", "user", "hello")
        await state_manager.delete_session("sess-1")
        history = await state_manager.get_session_history("sess-1")
        assert history == []

    async def test_delete_session_removes_redis_keys(self, state_manager, fake_redis):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.add_message("sess-1", "user-1", "user", "hello")
        await state_manager.delete_session("sess-1")
        assert await fake_redis.hgetall("session:sess-1:state") == {}
        assert await fake_redis.llen("session:sess-1:messages") == 0

    async def test_get_session_history_ordered(self, state_manager):
        await state_manager.init_session("sess-1", "user-1", "ch-1")
        await state_manager.add_message("sess-1", "user-1", "user", "first")
        await state_manager.add_message("sess-1", "user-1", "assistant", "second")
        history = await state_manager.get_session_history("sess-1")
        assert history[0]["content"] == "first"
        assert history[1]["content"] == "second"


# ── 사용자 목록 / scan_task_ids ───────────────────────────────────────────────

class TestListUsersAndScanTasks:
    async def test_list_users(self, state_manager):
        await state_manager.get_user_profile("user-1")
        await state_manager.get_user_profile("user-2")
        users, total = await state_manager.list_users()
        assert total == 2

    async def test_list_users_pagination(self, state_manager):
        for i in range(5):
            await state_manager.get_user_profile(f"user-{i}")
        users, total = await state_manager.list_users(limit=2)
        assert len(users) == 2
        assert total == 5

    async def test_scan_task_ids(self, state_manager):
        await state_manager.update_task_state("AAA", {"status": "DONE"})
        await state_manager.update_task_state("BBB", {"status": "DONE"})
        ids = await state_manager.scan_task_ids()
        assert "AAA" in ids
        assert "BBB" in ids

    async def test_scan_task_ids_limit(self, state_manager):
        for i in range(10):
            await state_manager.update_task_state(f"task-{i:03d}", {"status": "DONE"})
        ids = await state_manager.scan_task_ids(limit=3)
        assert len(ids) <= 3
