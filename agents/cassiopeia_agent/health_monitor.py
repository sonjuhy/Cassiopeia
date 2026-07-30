"""
에이전트 헬스 모니터링 및 Circuit Breaker
- 에이전트 하트비트 수집 (agent:{name}:health Redis Hash)
- 에이전트 유형 구분 (long_running, ephemeral)
- 가용 에이전트 목록 조회 (30초 이내 하트비트)
- Circuit Breaker 및 주기적 감시 루프
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("cassiopeia_agent.health_monitor")

_CB_THRESHOLD: int = int(os.environ.get("CB_THRESHOLD", "3"))
_CB_WINDOW_SEC: int = int(os.environ.get("CB_WINDOW_SEC", "300"))
_HEARTBEAT_VALID_SEC: int = int(os.environ.get("HEARTBEAT_VALID_SEC", "30"))


def _is_heartbeat_recent(last_heartbeat: str) -> bool:
    if not last_heartbeat: return False
    try:
        hb_time = datetime.fromisoformat(last_heartbeat.replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - hb_time).total_seconds()
        return diff <= _HEARTBEAT_VALID_SEC
    except Exception: return False


class HealthMonitor:
    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        if redis_client is not None:
            self._redis = redis_client
        else:
            redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
            self._redis = aioredis.from_url(redis_url, decode_responses=True, socket_timeout=5.0)

    async def is_agent_ready(self, agent_name: str) -> tuple[bool, str]:
        reg_raw = await self._redis.hget("agents:registry", agent_name)
        if not reg_raw: return False, "NOT_FOUND"
        
        reg_data = json.loads(reg_raw)
        health = await self._redis.hgetall(f"agent:{agent_name}:health")
        
        # Ephemeral 에이전트는 하트비트 체크를 유연하게 적용 (필요 시 로직 확장)
        if not health and reg_data.get("lifecycle_type") == "long_running":
            return False, "INACTIVE"

        if reg_data.get("lifecycle_type") == "long_running":
            if not _is_heartbeat_recent(health.get("last_heartbeat", "")):
                return False, "INACTIVE"

        if health.get("status") == "MAINTENANCE": return False, "MAINTENANCE"
        if await self.check_circuit_breaker(agent_name): return False, "CIRCUIT_OPEN"
        
        return True, "OK"

    async def register_agent(
        self,
        agent_name: str,
        capabilities: list[str],
        lifecycle_type: str = "long_running",
        nlu_description: str = "",
        permission_preset: str = "standard",
        allow_llm_access: bool | None = None,
        params_schema: dict | None = None,
        default_timeout: int | None = None,
        routing: dict | None = None,
    ) -> None:
        """에이전트를 레지스트리에 등록합니다.

        지휘자가 에이전트 이름을 코드에서 특별 취급하지 않도록, 라우팅에 필요한
        모든 메타데이터를 에이전트가 직접 선언해 여기에 영속화합니다.

            params_schema:   지휘자가 LLM 라우팅 시 노출할 액션/파라미터 가이드.
                             미선언 시 None(지휘자는 일반 가이드로 대체).
            default_timeout: 이 에이전트 작업의 기본 타임아웃(초). 미선언 시 None
                             (지휘자는 전역 기본값 사용).
            routing:         라우팅 메타데이터(예: {"role": "communication",
                             "platforms": [...]}). 미선언 시 {}.
        """
        from .admin_router import LLM_ENV_VARS, PERMISSION_PRESETS

        preset = PERMISSION_PRESETS.get(permission_preset, PERMISSION_PRESETS["standard"])
        # allow_llm_access 미지정 시 프리셋 기본값 사용
        effective_llm_access = (
            allow_llm_access if allow_llm_access is not None
            else preset.get("allow_llm_access", False)
        )
        await self._redis.hset("agents:registry", agent_name, json.dumps({
            "name": agent_name,
            "capabilities": capabilities,
            "lifecycle_type": lifecycle_type,
            "nlu_description": nlu_description,
            "permission_preset": permission_preset,
            "allow_llm_access": effective_llm_access,
            "params_schema": params_schema,
            "default_timeout": default_timeout,
            "routing": routing or {},
            "llm_env_vars": LLM_ENV_VARS,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False))
        logger.info(
            "[HealthMonitor] 에이전트 등록: %s (%s, preset=%s, llm_access=%s)",
            agent_name, lifecycle_type, permission_preset, effective_llm_access,
        )

    async def get_available_agents(self) -> list[str]:
        registry = await self._redis.hgetall("agents:registry")
        available = []
        for name, data_raw in registry.items():
            data = json.loads(data_raw)
            if data.get("lifecycle_type") == "ephemeral":
                available.append(name) # 일회성은 항상 가용으로 간주 (필요 시 구동)
                continue
            
            health = await self._redis.hgetall(f"agent:{name}:health")
            if _is_heartbeat_recent(health.get("last_heartbeat", "")):
                available.append(name)
        return available

    async def check_circuit_breaker(self, agent_name: str) -> bool:
        failures = await self._redis.get(f"circuit:{agent_name}:failures")
        return int(failures or 0) >= _CB_THRESHOLD

    async def record_failure(self, agent_name: str) -> None:
        key = f"circuit:{agent_name}:failures"
        count = await self._redis.incr(key)
        if count == 1: await self._redis.expire(key, _CB_WINDOW_SEC)
        if count >= _CB_THRESHOLD:
            await self._redis.hset(f"agent:{agent_name}:health", "status", "MAINTENANCE")

    async def record_success(self, agent_name: str) -> None:
        await self._redis.delete(f"circuit:{agent_name}:failures")

    async def reset_circuit_breaker(self, agent_name: str) -> None:
        await self._redis.delete(f"circuit:{agent_name}:failures")
        await self._redis.hset(f"agent:{agent_name}:health", "status", "IDLE")

    async def get_system_health(self) -> dict[str, Any]:
        registry = await self._redis.hgetall("agents:registry")
        summary = {}
        for name, data_raw in registry.items():
            data = json.loads(data_raw)
            health = await self._redis.hgetall(f"agent:{name}:health")
            cb_failures = await self._redis.get(f"circuit:{name}:failures")
            current_task = await self._redis.hgetall(f"agent:{name}:current_task") or None

            heartbeat_valid = _is_heartbeat_recent(health.get("last_heartbeat", ""))
            cb_open = int(cb_failures or 0) >= _CB_THRESHOLD
            raw_status = health.get("status", "UNKNOWN")

            # 활동 상태 계산
            if raw_status == "MAINTENANCE" or cb_open:
                activity = "MAINTENANCE" if raw_status == "MAINTENANCE" else "CIRCUIT_OPEN"
            elif not heartbeat_valid and data.get("lifecycle_type") == "long_running":
                activity = "OFFLINE"
            elif current_task:
                activity = "BUSY"
            else:
                activity = "IDLE"

            summary[name] = {
                "activity": activity,                         # IDLE | BUSY | OFFLINE | MAINTENANCE | CIRCUIT_OPEN
                "status": raw_status,                         # 에이전트가 직접 기록한 상태값
                "lifecycle_type": data.get("lifecycle_type", "long_running"),
                "heartbeat_valid": heartbeat_valid,
                "last_heartbeat": health.get("last_heartbeat"),
                "circuit_breaker_open": cb_open,
                "current_task": current_task,                 # 작업 중일 때만 존재
                "capabilities": data.get("capabilities", []),
            }
        return summary

    async def get_all_queues_status(self) -> dict[str, dict[str, Any]]:
        """
        레지스트리에 등록된 모든 에이전트의 큐 길이를 반환합니다.
        GUI 대시보드의 큐 현황 위젯에 사용합니다.
        """
        registry = await self._redis.hgetall("agents:registry")
        result: dict[str, dict[str, Any]] = {}
        for name in registry:
            queue_key = f"agent:{name}:tasks"
            length = await self._redis.llen(queue_key)
            result[name] = {"queue_key": queue_key, "length": length}
        return result

    async def monitor_loop(self, interval: int = 30) -> None:
        logger.info("[HealthMonitor] 감시 루프 시작 (%ds)", interval)
        last_states = {}
        while True:
            try:
                health = await self.get_system_health()
                for name, info in health.items():
                    # Ephemeral은 하트비트 변화 감시에서 제외 (또는 특별 처리)
                    if info["lifecycle_type"] == "ephemeral": continue
                    
                    curr = info["heartbeat_valid"]
                    prev = last_states.get(name)
                    if prev is not None and prev != curr:
                        logger.warning("[HealthMonitor] %s 상태 변화: %s", name, "온라인" if curr else "오프라인")
                    last_states[name] = curr
                await asyncio.sleep(interval)
            except asyncio.CancelledError: break
            except Exception as e:
                logger.error("[HealthMonitor] 루프 오류: %s", e)
                await asyncio.sleep(5)
