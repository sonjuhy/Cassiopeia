"""
health_monitor.py 테스트
- is_agent_ready(): NOT_FOUND / INACTIVE / MAINTENANCE / CIRCUIT_OPEN / OK
- circuit breaker: record_failure, record_success, reset, threshold
- register_agent(), get_available_agents(), get_system_health()
- get_nlu_capabilities(): 캐시 히트/미스
- _is_heartbeat_recent(): 유효/만료
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agents.cassiopeia_agent.health_monitor import HealthMonitor, _is_heartbeat_recent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _old_iso(seconds: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


async def _register(hm: HealthMonitor, name: str, lifecycle: str = "long_running") -> None:
    await hm._redis.hset("agents:registry", name, json.dumps({
        "name": name,
        "capabilities": [],
        "lifecycle_type": lifecycle,
        "nlu_description": "",
        "permission_preset": "standard",
        "allow_llm_access": False,
    }))


async def _set_health(hm: HealthMonitor, name: str, **fields) -> None:
    await hm._redis.hset(f"agent:{name}:health", mapping=fields)


# ── _is_heartbeat_recent ──────────────────────────────────────────────────────

class TestIsHeartbeatRecent:
    def test_recent(self):
        assert _is_heartbeat_recent(_now_iso()) is True

    def test_old(self):
        assert _is_heartbeat_recent(_old_iso(60)) is False

    def test_empty_string(self):
        assert _is_heartbeat_recent("") is False

    def test_invalid_format(self):
        assert _is_heartbeat_recent("not-a-date") is False

    def test_boundary_just_within(self):
        ts = (datetime.now(timezone.utc) - timedelta(seconds=29)).isoformat()
        assert _is_heartbeat_recent(ts) is True

    def test_boundary_just_outside(self):
        ts = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
        assert _is_heartbeat_recent(ts) is False


# ── is_agent_ready ────────────────────────────────────────────────────────────

class TestIsAgentReady:
    async def test_not_registered_returns_not_found(self, health_monitor):
        ready, reason = await health_monitor.is_agent_ready("unknown_agent")
        assert ready is False
        assert reason == "NOT_FOUND"

    async def test_long_running_no_health_returns_inactive(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        ready, reason = await health_monitor.is_agent_ready("agent_a")
        assert ready is False
        assert reason == "INACTIVE"

    async def test_long_running_stale_heartbeat_returns_inactive(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        await _set_health(health_monitor, "agent_a", last_heartbeat=_old_iso(60), status="IDLE")
        ready, reason = await health_monitor.is_agent_ready("agent_a")
        assert ready is False
        assert reason == "INACTIVE"

    async def test_long_running_fresh_heartbeat_returns_ok(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        await _set_health(health_monitor, "agent_a", last_heartbeat=_now_iso(), status="IDLE")
        ready, reason = await health_monitor.is_agent_ready("agent_a")
        assert ready is True
        assert reason == "OK"

    async def test_maintenance_status_returns_maintenance(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        await _set_health(health_monitor, "agent_a", last_heartbeat=_now_iso(), status="MAINTENANCE")
        ready, reason = await health_monitor.is_agent_ready("agent_a")
        assert ready is False
        assert reason == "MAINTENANCE"

    async def test_circuit_open_returns_circuit_open(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        await _set_health(health_monitor, "agent_a", last_heartbeat=_now_iso(), status="IDLE")
        await health_monitor._redis.set("circuit:agent_a:failures", "3")
        ready, reason = await health_monitor.is_agent_ready("agent_a")
        assert ready is False
        assert reason == "CIRCUIT_OPEN"

    async def test_ephemeral_always_ready(self, health_monitor):
        await _register(health_monitor, "ephemeral_a", "ephemeral")
        ready, reason = await health_monitor.is_agent_ready("ephemeral_a")
        assert ready is True
        assert reason == "OK"

    async def test_ephemeral_maintenance_still_blocked(self, health_monitor):
        await _register(health_monitor, "ephemeral_a", "ephemeral")
        await _set_health(health_monitor, "ephemeral_a", status="MAINTENANCE")
        ready, reason = await health_monitor.is_agent_ready("ephemeral_a")
        assert ready is False
        assert reason == "MAINTENANCE"


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class TestCircuitBreaker:
    async def test_check_no_failures(self, health_monitor):
        assert await health_monitor.check_circuit_breaker("agent_x") is False

    async def test_check_below_threshold(self, health_monitor):
        await health_monitor._redis.set("circuit:agent_x:failures", "2")
        assert await health_monitor.check_circuit_breaker("agent_x") is False

    async def test_check_at_threshold(self, health_monitor):
        await health_monitor._redis.set("circuit:agent_x:failures", "3")
        assert await health_monitor.check_circuit_breaker("agent_x") is True

    async def test_check_above_threshold(self, health_monitor):
        await health_monitor._redis.set("circuit:agent_x:failures", "5")
        assert await health_monitor.check_circuit_breaker("agent_x") is True

    async def test_record_failure_increments(self, health_monitor):
        await _register(health_monitor, "agent_x", "long_running")
        await _set_health(health_monitor, "agent_x", status="IDLE")
        await health_monitor.record_failure("agent_x")
        val = await health_monitor._redis.get("circuit:agent_x:failures")
        assert int(val) == 1

    async def test_record_failure_opens_circuit_at_threshold(self, health_monitor):
        await _register(health_monitor, "agent_x", "long_running")
        await _set_health(health_monitor, "agent_x", status="IDLE")
        for _ in range(3):
            await health_monitor.record_failure("agent_x")
        assert await health_monitor.check_circuit_breaker("agent_x") is True

    async def test_record_failure_sets_maintenance_at_threshold(self, health_monitor):
        await _register(health_monitor, "agent_x", "long_running")
        await _set_health(health_monitor, "agent_x", status="IDLE")
        for _ in range(3):
            await health_monitor.record_failure("agent_x")
        status = await health_monitor._redis.hget("agent:agent_x:health", "status")
        assert status == "MAINTENANCE"

    async def test_record_success_resets_counter(self, health_monitor):
        await health_monitor._redis.set("circuit:agent_x:failures", "2")
        await health_monitor.record_success("agent_x")
        val = await health_monitor._redis.get("circuit:agent_x:failures")
        assert val is None

    async def test_reset_circuit_breaker_clears_failures(self, health_monitor):
        await _register(health_monitor, "agent_x", "long_running")
        await _set_health(health_monitor, "agent_x", status="MAINTENANCE")
        await health_monitor._redis.set("circuit:agent_x:failures", "3")
        await health_monitor.reset_circuit_breaker("agent_x")
        assert await health_monitor.check_circuit_breaker("agent_x") is False

    async def test_reset_circuit_breaker_sets_idle(self, health_monitor):
        await _register(health_monitor, "agent_x", "long_running")
        await _set_health(health_monitor, "agent_x", status="MAINTENANCE")
        await health_monitor.reset_circuit_breaker("agent_x")
        status = await health_monitor._redis.hget("agent:agent_x:health", "status")
        assert status == "IDLE"


# ── register_agent ────────────────────────────────────────────────────────────

class TestRegisterAgent:
    async def test_stores_in_registry(self, health_monitor):
        await health_monitor.register_agent("new_agent", ["cap1", "cap2"], lifecycle_type="ephemeral")
        raw = await health_monitor._redis.hget("agents:registry", "new_agent")
        assert raw is not None
        data = json.loads(raw)
        assert data["name"] == "new_agent"
        assert data["lifecycle_type"] == "ephemeral"
        assert "cap1" in data["capabilities"]

    async def test_default_preset_standard(self, health_monitor):
        await health_monitor.register_agent("a1", [])
        data = json.loads(await health_monitor._redis.hget("agents:registry", "a1"))
        assert data["permission_preset"] == "standard"
        assert data["allow_llm_access"] is False

    async def test_trusted_preset_allows_llm(self, health_monitor):
        await health_monitor.register_agent("a1", [], permission_preset="trusted")
        data = json.loads(await health_monitor._redis.hget("agents:registry", "a1"))
        assert data["allow_llm_access"] is True

    async def test_explicit_llm_access_override(self, health_monitor):
        await health_monitor.register_agent("a1", [], permission_preset="standard", allow_llm_access=True)
        data = json.loads(await health_monitor._redis.hget("agents:registry", "a1"))
        assert data["allow_llm_access"] is True


# ── get_available_agents ──────────────────────────────────────────────────────

class TestGetAvailableAgents:
    async def test_long_running_with_fresh_heartbeat(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        await _set_health(health_monitor, "agent_a", last_heartbeat=_now_iso())
        agents = await health_monitor.get_available_agents()
        assert "agent_a" in agents

    async def test_long_running_stale_heartbeat_excluded(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        await _set_health(health_monitor, "agent_a", last_heartbeat=_old_iso(60))
        agents = await health_monitor.get_available_agents()
        assert "agent_a" not in agents

    async def test_ephemeral_always_included(self, health_monitor):
        await _register(health_monitor, "ephemeral_a", "ephemeral")
        agents = await health_monitor.get_available_agents()
        assert "ephemeral_a" in agents

    async def test_empty_registry_returns_empty(self, health_monitor):
        agents = await health_monitor.get_available_agents()
        assert agents == []


# ── get_nlu_capabilities ──────────────────────────────────────────────────────

class TestGetNluCapabilities:
    async def test_returns_nlu_description_from_registry(self, health_monitor):
        await health_monitor._redis.hset("agents:registry", "agent_a", json.dumps({
            "name": "agent_a",
            "capabilities": [],
            "lifecycle_type": "long_running",
            "nlu_description": "agent_a: 파일 작업",
            "permission_preset": "standard",
            "allow_llm_access": False,
        }))
        await _set_health(health_monitor, "agent_a",
                          last_heartbeat=_now_iso())
        
        # 캐시 초기화
        health_monitor._capabilities_cache = ""
        health_monitor._capabilities_cache_at = 0.0
        caps = await health_monitor.get_nlu_capabilities()
        assert "agent_a: 파일 작업" in caps

    async def test_cache_hit_returns_same_value(self, health_monitor):
        await health_monitor._redis.hset("agents:registry", "agent_a", json.dumps({
            "name": "agent_a",
            "capabilities": [],
            "lifecycle_type": "long_running",
            "nlu_description": "desc_A",
            "permission_preset": "standard",
            "allow_llm_access": False,
        }))
        await _set_health(health_monitor, "agent_a", last_heartbeat=_now_iso())
        
        # 캐시 초기화
        health_monitor._capabilities_cache = ""
        health_monitor._capabilities_cache_at = 0.0
        
        first = await health_monitor.get_nlu_capabilities()
        # 캐시가 갱신되기 전에 Redis 값을 바꿔도 캐시된 값을 반환해야 한다
        await health_monitor._redis.hset("agents:registry", "agent_a", json.dumps({
            "name": "agent_a",
            "capabilities": [],
            "lifecycle_type": "long_running",
            "nlu_description": "desc_CHANGED",
            "permission_preset": "standard",
            "allow_llm_access": False,
        }))
        second = await health_monitor.get_nlu_capabilities()
        assert first == second

    async def test_stale_agent_excluded(self, health_monitor):
        await health_monitor._redis.hset("agents:registry", "agent_a", json.dumps({
            "name": "agent_a",
            "capabilities": [],
            "lifecycle_type": "long_running",
            "nlu_description": "오래된 에이전트",
            "permission_preset": "standard",
            "allow_llm_access": False,
        }))
        await _set_health(health_monitor, "agent_a",
                          last_heartbeat=_old_iso(60))
        
        # 캐시 초기화
        health_monitor._capabilities_cache = ""
        health_monitor._capabilities_cache_at = 0.0
        caps = await health_monitor.get_nlu_capabilities()
        assert "오래된 에이전트" not in caps

    async def test_ephemeral_uses_registry_description(self, health_monitor):
        await health_monitor._redis.hset("agents:registry", "eph_agent", json.dumps({
            "name": "eph_agent",
            "capabilities": [],
            "lifecycle_type": "ephemeral",
            "nlu_description": "eph: 빠른 작업",
            "permission_preset": "standard",
            "allow_llm_access": False,
        }))
        # 캐시 초기화 (다른 테스트와 격리)
        health_monitor._capabilities_cache = ""
        health_monitor._capabilities_cache_at = 0.0
        caps = await health_monitor.get_nlu_capabilities()
        assert "eph: 빠른 작업" in caps


# ── get_system_health ─────────────────────────────────────────────────────────

class TestGetSystemHealth:
    async def test_returns_all_agents(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        await _register(health_monitor, "agent_b", "ephemeral")
        health = await health_monitor.get_system_health()
        assert "agent_a" in health
        assert "agent_b" in health

    async def test_circuit_open_reflected(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        await health_monitor._redis.set("circuit:agent_a:failures", "3")
        health = await health_monitor.get_system_health()
        assert health["agent_a"]["circuit_breaker_open"] is True

    async def test_heartbeat_valid_reflected(self, health_monitor):
        await _register(health_monitor, "agent_a", "long_running")
        await _set_health(health_monitor, "agent_a", last_heartbeat=_now_iso())
        health = await health_monitor.get_system_health()
        assert health["agent_a"]["heartbeat_valid"] is True
