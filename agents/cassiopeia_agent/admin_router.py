"""
Cassiopeia Agent 관리자 API 라우터  (GUI 외부 제어 전용)

접두사: /admin

엔드포인트 목록:
  [대시보드]
  GET  /admin/dashboard                    전체 시스템 현황 (에이전트·큐·로그 요약)

  [에이전트 관리]
  GET  /admin/agents                       전체 에이전트 목록 + 상세 상태
  GET  /admin/agents/{name}                특정 에이전트 상세 (헬스·큐·서킷브레이커)
  POST /admin/agents                       에이전트 수동 등록
  DELETE /admin/agents/{name}              에이전트 등록 해제 (큐 정리 옵션)
  POST /admin/agents/{name}/maintenance    유지보수 모드 전환·복구

  [권한 관리]
  GET  /admin/permissions/presets          사용 가능한 권한 프리셋 목록
  GET  /admin/agents/{name}/permissions    특정 에이전트 권한 조회
  PUT  /admin/agents/{name}/permissions    권한 프리셋 변경

  [서킷브레이커]
  GET  /admin/agents/{name}/circuit        서킷브레이커 상태 조회
  POST /admin/agents/{name}/circuit/reset  서킷브레이커 수동 초기화
  POST /admin/agents/{name}/circuit/trip   서킷브레이커 수동 차단 (긴급)

  [큐 관리]
  GET  /admin/queues                       전체 큐 현황
  GET  /admin/queues/{name}                특정 에이전트 큐 상세 + 미리보기
  DELETE /admin/queues/{name}              큐 강제 비우기

  [태스크 관리]
  GET  /admin/tasks                        최근 태스크 목록 (페이지네이션)
  GET  /admin/tasks/{task_id}              태스크 상세 조회

  [로그 조회]
  GET  /admin/logs                         에이전트 활동 로그 (필터·페이지네이션)

  [세션 관리]
  GET  /admin/sessions                     세션 목록
  GET  /admin/sessions/{session_id}        세션 상세 + 대화 이력
  DELETE /admin/sessions/{session_id}      세션 삭제

  [사용자 관리]
  GET  /admin/users                        사용자 목록
  GET  /admin/users/{user_id}              사용자 프로필
  PUT  /admin/users/{user_id}              프로필 수정

  [시스템]
  GET  /admin/system/metrics               시스템 전체 메트릭
  POST /admin/system/broadcast             전체/특정 에이전트 브로드캐스트
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from .app_context import ctx
from .auth import verify_admin_key

router = APIRouter(
    prefix="/admin", 
    tags=["관리자 (Admin)"], 
    dependencies=[Depends(verify_admin_key)]
)

# ── 권한 프리셋 정의 ───────────────────────────────────────────────────────────
# AGENT_CONTAINER_MANAGEMENT.md §4 권한 부여 기반

PERMISSION_PRESETS: dict[str, dict[str, Any]] = {
    "minimal": {
        "network": "none",
        "filesystem": "readonly",
        "memory_limit": "256m",
        "cpu_limit": "0.5",
        "pids_limit": 50,
        "cap_drop": "ALL",
        "allow_llm_access": False,
        "description": "단순 계산, 격리된 코드 실행 (네트워크 차단, LLM 접근 불가)",
    },
    "standard": {
        "network": "internal",
        "filesystem": "readonly",
        "memory_limit": "512m",
        "cpu_limit": "1.0",
        "pids_limit": 100,
        "cap_drop": "ALL",
        "allow_llm_access": False,
        "description": "일반 에이전트 간 통신 — 기본값 (내부 네트워크만 허용, LLM 접근 불가)",
    },
    "trusted": {
        "network": "full",
        "filesystem": "readwrite",
        "memory_limit": "1g",
        "cpu_limit": "2.0",
        "pids_limit": 200,
        "cap_drop": "ALL",
        "cap_add": ["NET_BIND_SERVICE"],
        "allow_llm_access": True,
        "description": "외부 API 호출, 로컬 파일 수정, LLM 사용 (전체 네트워크 허용)",
    },
}

# LLM 접근 시 컨테이너에 주입되는 환경변수 목록 (ContainerPermissions 기본값과 동일)
LLM_ENV_VARS: list[str] = [
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "LOCAL_LLM_BASE_URL",
    "LOCAL_LLM_MODEL",
    "LOCAL_LLM_API_KEY",
]

_CB_THRESHOLD = 3  # health_monitor와 동일 값


# ── Request 모델 ──────────────────────────────────────────────────────────────

_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class RegisterAgentBody(BaseModel):
    agent_name: str = Field(..., description="에이전트 고유 이름 (예: my_agent)")
    capabilities: list[str] = Field(default_factory=list, description="에이전트 액션 목록")
    lifecycle_type: str = Field("long_running", description="long_running | ephemeral")
    nlu_description: str = Field("", description="NLU 시스템 프롬프트용 자연어 설명")
    permission_preset: str = Field("standard", description="minimal | standard | trusted")
    allow_llm_access: bool | None = Field(
        None,
        description="LLM 접근 허용 여부. None이면 프리셋 기본값 사용",
    )

    @field_validator("agent_name")
    @classmethod
    def validate_agent_name(cls, v: str) -> str:
        if not _AGENT_NAME_RE.match(v):
            raise ValueError(
                "agent_name은 영문자·숫자·언더스코어만 허용되며 1~64자여야 합니다."
            )
        return v


class SetPermissionsBody(BaseModel):
    preset: str = Field(..., description="minimal | standard | trusted")


class SetLLMAccessBody(BaseModel):
    allow_llm_access: bool = Field(..., description="LLM API 접근 허용 여부")
    llm_env_vars: list[str] | None = Field(
        None,
        description="주입할 환경변수 목록. None이면 기본값(ANTHROPIC_API_KEY 등) 유지",
    )


class UpdateUserBody(BaseModel):
    name: str | None = None
    style_pref: dict[str, str] | None = None


class BroadcastBody(BaseModel):
    message: str = Field(..., description="브로드캐스트할 메시지 내용")
    target_agents: list[str] = Field(
        default_factory=list,
        description="대상 에이전트 목록. 비어있으면 전체 에이전트에 전송",
    )


class DLQReplayBody(BaseModel):
    task_id: str = Field(..., description="재처리할 태스크 ID")


class SandboxKeyGenerateBody(BaseModel):
    label: str = Field(..., description="키 식별을 위한 라벨 (예: 'prod-cassiopeia')")


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

async def _require_agent(agent_name: str) -> dict[str, Any]:
    """레지스트리에서 에이전트를 찾거나 404 반환."""
    raw = await ctx.redis_client.hget("agents:registry", agent_name)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"에이전트 '{agent_name}'이 레지스트리에 없습니다.",
        )
    return json.loads(raw)


async def _circuit_info(agent_name: str) -> dict[str, Any]:
    failures = int(await ctx.redis_client.get(f"circuit:{agent_name}:failures") or 0)
    return {
        "failures": failures,
        "threshold": _CB_THRESHOLD,
        "is_open": failures >= _CB_THRESHOLD,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 대시보드
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard", summary="시스템 전체 현황 대시보드")
async def get_dashboard() -> dict[str, Any]:
    """
    GUI 홈 화면용 단일 응답:
    에이전트 상태 요약, 큐 현황, 최근 로그 10건, 서킷브레이커 이상 에이전트 목록.
    """
    system_health = await ctx.health_monitor.get_system_health()
    available = await ctx.health_monitor.get_available_agents()
    queues = await ctx.health_monitor.get_all_queues_status()
    recent_logs = await ctx.state_manager.get_agent_logs(limit=10, offset=0)

    # 서킷브레이커가 열린 에이전트
    open_circuits = [
        name for name, info in system_health.items()
        if info.get("circuit_breaker_open")
    ]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "agents_total": len(system_health),
            "agents_online": len(available),
            "agents_offline": len(system_health) - len(available),
            "total_queued_tasks": sum(q["length"] for q in queues.values()),
            "open_circuit_count": len(open_circuits),
        },
        "agents": system_health,
        "open_circuits": open_circuits,
        "queues": queues,
        "recent_logs": recent_logs,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 에이전트 관리
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/agents", summary="전체 에이전트 목록 상세 조회")
async def list_all_agents() -> dict[str, Any]:
    """
    레지스트리에 등록된 모든 에이전트의 헬스, 큐 길이, 서킷브레이커 상태를 한 번에 반환합니다.
    """
    registry = await ctx.redis_client.hgetall("agents:registry")
    result: dict[str, Any] = {}

    for name, raw in registry.items():
        data = json.loads(raw)
        health = await ctx.redis_client.hgetall(f"agent:{name}:health")
        queue_len = await ctx.redis_client.llen(f"agent:{name}:tasks")
        cb = await _circuit_info(name)
        current_task = await ctx.redis_client.hgetall(f"agent:{name}:current_task") or None

        # 활동 상태 계산
        from .health_monitor import _is_heartbeat_recent, _CB_THRESHOLD
        heartbeat_ok = _is_heartbeat_recent(health.get("last_heartbeat", ""))
        cb_open = cb["failure_count"] >= _CB_THRESHOLD
        raw_status = health.get("status", "UNKNOWN")
        if raw_status == "MAINTENANCE":
            activity = "MAINTENANCE"
        elif cb_open:
            activity = "CIRCUIT_OPEN"
        elif not heartbeat_ok and data.get("lifecycle_type") == "long_running":
            activity = "OFFLINE"
        elif current_task:
            activity = "BUSY"
        else:
            activity = "IDLE"

        result[name] = {
            "activity": activity,
            "lifecycle_type": data.get("lifecycle_type", "long_running"),
            "capabilities": data.get("capabilities", []),
            "permission_preset": data.get("permission_preset", "standard"),
            "registered_at": data.get("registered_at"),
            "health": {
                "status": raw_status,
                "last_heartbeat": health.get("last_heartbeat"),
                "version": health.get("version"),
                "current_tasks": health.get("current_tasks", "0"),
                "max_concurrency": health.get("max_concurrency", "1"),
            },
            "current_task": current_task,
            "queue_length": queue_len,
            "circuit_breaker": cb,
        }

    return {"total": len(result), "agents": result}


@router.get("/agents/{agent_name}", summary="특정 에이전트 상세 조회")
async def get_agent_detail(
    agent_name: str,
    queue_preview: int = Query(5, ge=1, le=50, description="큐 미리보기 메시지 수"),
) -> dict[str, Any]:
    """
    헬스, 큐, 서킷브레이커, 큐 메시지 미리보기까지 한 번에 반환.
    """
    data = await _require_agent(agent_name)
    health = await ctx.redis_client.hgetall(f"agent:{agent_name}:health")
    cb = await _circuit_info(agent_name)
    current_task = await ctx.redis_client.hgetall(f"agent:{agent_name}:current_task") or None
    queue_key = f"agent:{agent_name}:tasks"
    queue_len = await ctx.redis_client.llen(queue_key)

    raw_items = await ctx.redis_client.lrange(queue_key, 0, queue_preview - 1)
    previews: list[dict[str, Any]] = []
    for item in raw_items:
        try:
            msg = json.loads(item)
            previews.append({
                "task_id": msg.get("task_id"),
                "action": msg.get("action"),
                "priority": msg.get("priority"),
                "timestamp": msg.get("timestamp"),
                "content_preview": str(msg.get("content", ""))[:120],
            })
        except Exception:
            previews.append({"raw_preview": item[:200]})

    available = await ctx.health_monitor.get_available_agents()

    # 활동 상태 계산
    from .health_monitor import _is_heartbeat_recent, _CB_THRESHOLD
    heartbeat_ok = _is_heartbeat_recent(health.get("last_heartbeat", ""))
    cb_open = cb["failure_count"] >= _CB_THRESHOLD
    raw_status = health.get("status", "UNKNOWN")
    if raw_status == "MAINTENANCE":
        activity = "MAINTENANCE"
    elif cb_open:
        activity = "CIRCUIT_OPEN"
    elif not heartbeat_ok and data.get("lifecycle_type") == "long_running":
        activity = "OFFLINE"
    elif current_task:
        activity = "BUSY"
    else:
        activity = "IDLE"

    return {
        "agent_name": agent_name,
        "activity": activity,
        "is_available": agent_name in available,
        "current_task": current_task,
        "registry": {
            "lifecycle_type": data.get("lifecycle_type"),
            "capabilities": data.get("capabilities", []),
            "permission_preset": data.get("permission_preset", "standard"),
            "nlu_description": data.get("nlu_description", ""),
            "registered_at": data.get("registered_at"),
        },
        "health": health,
        "circuit_breaker": cb,
        "queue": {
            "total_length": queue_len,
            "preview": previews,
        },
    }


@router.post("/agents", status_code=status.HTTP_201_CREATED, summary="에이전트 수동 등록")
async def register_agent(body: RegisterAgentBody) -> dict[str, Any]:
    """
    새 에이전트를 레지스트리에 등록합니다.
    에이전트 자동 자기 등록이 불가능한 경우 관리자가 직접 수행합니다.
    """
    if body.lifecycle_type not in ("long_running", "ephemeral"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="lifecycle_type은 'long_running' 또는 'ephemeral'이어야 합니다.",
        )
    if body.permission_preset not in PERMISSION_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"유효하지 않은 권한 프리셋: '{body.permission_preset}'. "
                   f"가능한 값: {list(PERMISSION_PRESETS.keys())}",
        )

    await ctx.health_monitor.register_agent(
        body.agent_name,
        body.capabilities,
        lifecycle_type=body.lifecycle_type,
        nlu_description=body.nlu_description,
        permission_preset=body.permission_preset,
        allow_llm_access=body.allow_llm_access,
    )

    preset = PERMISSION_PRESETS[body.permission_preset]
    effective_llm_access = (
        body.allow_llm_access if body.allow_llm_access is not None
        else preset["allow_llm_access"]
    )
    return {
        "status": "registered",
        "agent_name": body.agent_name,
        "lifecycle_type": body.lifecycle_type,
        "permission_preset": body.permission_preset,
        "permissions": {**preset, "allow_llm_access": effective_llm_access},
        "llm_access": {
            "allow_llm_access": effective_llm_access,
            "llm_env_vars": LLM_ENV_VARS if effective_llm_access else [],
        },
    }


@router.delete("/agents/{agent_name}", summary="에이전트 등록 해제")
async def deregister_agent(
    agent_name: str,
    flush_queue: bool = Query(True, description="큐 메시지도 함께 삭제할지 여부"),
) -> dict[str, Any]:
    """
    에이전트를 레지스트리에서 제거하고 관련 Redis 데이터를 정리합니다.
    flush_queue=true 이면 대기 중인 태스크도 모두 삭제됩니다.
    """
    await _require_agent(agent_name)

    await ctx.redis_client.hdel("agents:registry", agent_name)
    await ctx.redis_client.delete(f"agent:{agent_name}:health")
    await ctx.redis_client.delete(f"circuit:{agent_name}:failures")

    flushed = 0
    if flush_queue:
        queue_key = f"agent:{agent_name}:tasks"
        flushed = await ctx.redis_client.llen(queue_key)
        await ctx.redis_client.delete(queue_key)

    return {
        "status": "deregistered",
        "agent_name": agent_name,
        "queue_flushed": flush_queue,
        "flushed_tasks": flushed,
    }


@router.post("/agents/{agent_name}/maintenance", summary="유지보수 모드 전환·복구")
async def toggle_maintenance(
    agent_name: str,
    enable: bool = Query(..., description="true=유지보수 ON, false=정상 복구"),
) -> dict[str, Any]:
    """
    에이전트를 유지보수 모드로 전환하거나 복구합니다.
    복구 시 서킷브레이커도 함께 초기화됩니다.
    """
    await _require_agent(agent_name)

    new_status = "MAINTENANCE" if enable else "IDLE"
    await ctx.redis_client.hset(f"agent:{agent_name}:health", "status", new_status)

    if not enable:
        await ctx.health_monitor.reset_circuit_breaker(agent_name)

    return {
        "agent_name": agent_name,
        "maintenance_enabled": enable,
        "current_status": new_status,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 권한 관리
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/permissions/presets", summary="권한 프리셋 목록")
async def list_permission_presets() -> dict[str, Any]:
    """minimal / standard / trusted 권한 프리셋 정의 전체 반환."""
    return {"presets": PERMISSION_PRESETS}


@router.get("/agents/{agent_name}/permissions", summary="에이전트 권한 조회")
async def get_agent_permissions(agent_name: str) -> dict[str, Any]:
    data = await _require_agent(agent_name)
    preset_name = data.get("permission_preset", "standard")
    preset = PERMISSION_PRESETS.get(preset_name, PERMISSION_PRESETS["standard"])

    # allow_llm_access는 에이전트별 개별 설정이 프리셋 기본값보다 우선
    allow_llm_access: bool = data.get(
        "allow_llm_access", preset.get("allow_llm_access", False)
    )
    llm_env_vars: list[str] = data.get("llm_env_vars", LLM_ENV_VARS)

    return {
        "agent_name": agent_name,
        "preset": preset_name,
        "permissions": {**preset, "allow_llm_access": allow_llm_access},
        "llm_access": {
            "allow_llm_access": allow_llm_access,
            "llm_env_vars": llm_env_vars if allow_llm_access else [],
        },
        "available_presets": list(PERMISSION_PRESETS.keys()),
    }


@router.put("/agents/{agent_name}/permissions", summary="에이전트 권한 프리셋 변경")
async def set_agent_permissions(
    agent_name: str, body: SetPermissionsBody
) -> dict[str, Any]:
    """
    권한 프리셋을 변경합니다.
    프리셋 변경 시 allow_llm_access는 해당 프리셋의 기본값으로 초기화됩니다.
    실제 Docker 컨테이너 권한은 재시작 시 적용됩니다.
    """
    if body.preset not in PERMISSION_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"유효하지 않은 프리셋: '{body.preset}'. "
                   f"가능한 값: {list(PERMISSION_PRESETS.keys())}",
        )

    preset = PERMISSION_PRESETS[body.preset]
    data = await _require_agent(agent_name)
    data["permission_preset"] = body.preset
    # 프리셋 변경 시 LLM 접근을 해당 프리셋 기본값으로 초기화
    data["allow_llm_access"] = preset["allow_llm_access"]
    data.setdefault("llm_env_vars", LLM_ENV_VARS)
    await ctx.redis_client.hset(
        "agents:registry", agent_name,
        json.dumps(data, ensure_ascii=False),
    )

    return {
        "agent_name": agent_name,
        "preset": body.preset,
        "permissions": {**preset, "allow_llm_access": data["allow_llm_access"]},
        "llm_access": {
            "allow_llm_access": data["allow_llm_access"],
            "llm_env_vars": data["llm_env_vars"] if data["allow_llm_access"] else [],
        },
        "note": "컨테이너 재시작 후 실제 Docker 권한에 반영됩니다.",
    }


@router.patch("/agents/{agent_name}/permissions/llm-access", summary="에이전트 LLM 접근 권한 개별 설정")
async def set_agent_llm_access(
    agent_name: str, body: SetLLMAccessBody
) -> dict[str, Any]:
    """
    프리셋을 변경하지 않고 LLM 접근 권한만 독립적으로 켜거나 끕니다.

    - `allow_llm_access=true`: ANTHROPIC_API_KEY 등 LLM 환경변수를 컨테이너에 주입합니다.
    - `allow_llm_access=false`: LLM 환경변수 주입을 비활성화합니다.
    - `llm_env_vars`: 주입할 변수 목록을 덮어씁니다. 생략 시 기존 목록 유지.

    minimal 프리셋은 network=none이므로 LLM API에 도달할 수 없습니다.
    실제 Docker 컨테이너 환경변수는 재시작 시 반영됩니다.
    """
    data = await _require_agent(agent_name)

    preset_name = data.get("permission_preset", "standard")
    if body.allow_llm_access and preset_name == "minimal":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "minimal 프리셋은 network=none으로 LLM API에 도달할 수 없습니다. "
                "먼저 프리셋을 standard 또는 trusted로 변경하세요."
            ),
        )

    data["allow_llm_access"] = body.allow_llm_access
    if body.llm_env_vars is not None:
        data["llm_env_vars"] = body.llm_env_vars
    else:
        data.setdefault("llm_env_vars", LLM_ENV_VARS)

    await ctx.redis_client.hset(
        "agents:registry", agent_name,
        json.dumps(data, ensure_ascii=False),
    )

    effective_env_vars = data["llm_env_vars"] if body.allow_llm_access else []
    return {
        "agent_name": agent_name,
        "preset": preset_name,
        "llm_access": {
            "allow_llm_access": body.allow_llm_access,
            "llm_env_vars": effective_env_vars,
        },
        "note": "컨테이너 재시작 후 실제 Docker 환경변수에 반영됩니다.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 서킷브레이커 관리
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/agents/{agent_name}/circuit", summary="서킷브레이커 상태 조회")
async def get_circuit_breaker(agent_name: str) -> dict[str, Any]:
    await _require_agent(agent_name)
    health = await ctx.redis_client.hgetall(f"agent:{agent_name}:health")
    cb = await _circuit_info(agent_name)
    return {
        "agent_name": agent_name,
        "agent_status": health.get("status", "UNKNOWN"),
        **cb,
    }


@router.post("/agents/{agent_name}/circuit/reset", summary="서킷브레이커 초기화")
async def reset_circuit_breaker(agent_name: str) -> dict[str, Any]:
    """실패 카운터를 0으로 리셋하고 에이전트 상태를 IDLE로 복구합니다."""
    await _require_agent(agent_name)
    await ctx.health_monitor.reset_circuit_breaker(agent_name)
    return {
        "agent_name": agent_name,
        "status": "reset",
        "message": "서킷브레이커가 초기화되고 에이전트가 IDLE 상태로 복구되었습니다.",
    }


@router.post("/agents/{agent_name}/circuit/trip", summary="서킷브레이커 수동 차단")
async def trip_circuit_breaker(agent_name: str) -> dict[str, Any]:
    """긴급 상황 시 에이전트를 즉시 차단합니다. /circuit/reset으로 복구 가능."""
    await _require_agent(agent_name)
    await ctx.redis_client.set(
        f"circuit:{agent_name}:failures", _CB_THRESHOLD, ex=300
    )
    await ctx.redis_client.hset(f"agent:{agent_name}:health", "status", "MAINTENANCE")
    return {
        "agent_name": agent_name,
        "status": "tripped",
        "message": "서킷브레이커가 수동으로 차단되었습니다. POST /circuit/reset 으로 복구하세요.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 큐 관리
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/queues", summary="전체 큐 현황")
async def list_queues() -> dict[str, Any]:
    """등록된 모든 에이전트의 큐 길이를 한눈에 조회합니다."""
    return await ctx.health_monitor.get_all_queues_status()


@router.get("/queues/{agent_name}", summary="특정 에이전트 큐 상세")
async def get_queue_detail(
    agent_name: str,
    peek: int = Query(10, ge=1, le=100, description="미리볼 메시지 수"),
) -> dict[str, Any]:
    queue_key = f"agent:{agent_name}:tasks"
    length = await ctx.redis_client.llen(queue_key)
    raw_items = await ctx.redis_client.lrange(queue_key, 0, peek - 1)

    items: list[dict[str, Any]] = []
    for item in raw_items:
        try:
            msg = json.loads(item)
            items.append({
                "task_id": msg.get("task_id"),
                "action": msg.get("action"),
                "priority": msg.get("priority"),
                "timestamp": msg.get("timestamp"),
                "content_preview": str(msg.get("content", ""))[:120],
            })
        except Exception:
            items.append({"raw_preview": item[:200]})

    return {
        "agent_name": agent_name,
        "queue_key": queue_key,
        "total_length": length,
        "preview_count": len(items),
        "preview": items,
    }


@router.delete("/queues/{agent_name}", summary="큐 강제 비우기")
async def flush_queue(agent_name: str) -> dict[str, Any]:
    """
    에이전트 큐를 강제로 삭제합니다.
    주의: 아직 처리되지 않은 태스크가 영구적으로 손실됩니다.
    """
    queue_key = f"agent:{agent_name}:tasks"
    count = await ctx.redis_client.llen(queue_key)
    await ctx.redis_client.delete(queue_key)
    return {
        "agent_name": agent_name,
        "flushed_count": count,
        "message": f"큐에서 {count}개의 태스크가 삭제되었습니다.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 태스크 관리
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/tasks", summary="태스크 목록 조회")
async def list_tasks(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Redis에 캐싱된 태스크 상태를 최신순으로 조회합니다."""
    task_ids = await ctx.state_manager.scan_task_ids(limit=limit + offset)
    page_ids = task_ids[offset: offset + limit]

    tasks: list[dict[str, Any]] = []
    for tid in page_ids:
        state = await ctx.state_manager.get_task_state(tid)
        tasks.append({"task_id": tid, **state})

    return {
        "total_scanned": len(task_ids),
        "limit": limit,
        "offset": offset,
        "tasks": tasks,
    }


@router.get("/tasks/{task_id}", summary="태스크 상세 조회")
async def get_task_detail(task_id: str) -> dict[str, Any]:
    state = await ctx.state_manager.get_task_state(task_id)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"태스크 '{task_id}'를 찾을 수 없습니다.",
        )
    return {"task_id": task_id, **state}


# ══════════════════════════════════════════════════════════════════════════════
# 로그 조회
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/logs", summary="에이전트 활동 로그 조회")
async def get_logs(
    agent_name: str | None = Query(None, description="필터: 에이전트 이름"),
    action: str | None = Query(None, description="필터: 액션 종류 (error, reasoning 등)"),
    task_id: str | None = Query(None, description="필터: 태스크 ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """SQLite에 저장된 에이전트 활동 로그를 필터링·페이지네이션하여 반환합니다."""
    logs = await ctx.state_manager.get_agent_logs(
        agent_name=agent_name,
        action=action,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )
    total = await ctx.state_manager.count_agent_logs(
        agent_name=agent_name, action=action, task_id=task_id
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "logs": logs,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 세션 관리
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sessions", summary="세션 목록 조회")
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    sessions, total = await ctx.state_manager.list_sessions(limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "sessions": sessions}


@router.get("/sessions/{session_id}", summary="세션 상세 + 대화 이력")
async def get_session_detail(
    session_id: str,
    history_limit: int = Query(30, ge=1, le=200, description="가져올 메시지 수"),
    history_offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    state = await ctx.redis_client.hgetall(f"session:{session_id}:state")
    history = await ctx.state_manager.get_session_history(
        session_id, limit=history_limit, offset=history_offset
    )
    if not state and not history:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"세션 '{session_id}'을 찾을 수 없습니다.",
        )
    return {
        "session_id": session_id,
        "state": state,
        "history_count": len(history),
        "history": history,
    }


@router.delete("/sessions/{session_id}", summary="세션 삭제")
async def delete_session(session_id: str) -> dict[str, Any]:
    """Redis 캐시와 SQLite의 세션 데이터를 모두 삭제합니다."""
    await ctx.state_manager.delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}


# ══════════════════════════════════════════════════════════════════════════════
# 사용자 관리
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/users", summary="사용자 목록")
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    users, total = await ctx.state_manager.list_users(limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "users": users}


@router.get("/users/{user_id}", summary="사용자 프로필 조회")
async def get_user(user_id: str) -> dict[str, Any]:
    return await ctx.state_manager.get_user_profile(user_id)


@router.put("/users/{user_id}", summary="사용자 프로필 수정")
async def update_user(user_id: str, body: UpdateUserBody) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.style_pref is not None:
        updates["style_pref"] = body.style_pref
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="수정할 필드(name, style_pref)가 없습니다.",
        )
    await ctx.state_manager.update_user_profile(user_id, updates)
    return await ctx.state_manager.get_user_profile(user_id)


# ══════════════════════════════════════════════════════════════════════════════
# 시스템
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/system/metrics", summary="시스템 전체 메트릭")
async def get_system_metrics() -> dict[str, Any]:
    """Redis 메모리, 에이전트 현황, 큐 합계, 로그 건수 등 운영 메트릭을 반환합니다."""
    redis_mem = await ctx.redis_client.info("memory")
    redis_clients = await ctx.redis_client.info("clients")

    queues = await ctx.health_monitor.get_all_queues_status()
    available = await ctx.health_monitor.get_available_agents()
    registry = await ctx.redis_client.hgetall("agents:registry")

    open_circuits: list[str] = []
    for name in registry:
        failures = int(await ctx.redis_client.get(f"circuit:{name}:failures") or 0)
        if failures >= _CB_THRESHOLD:
            open_circuits.append(name)

    log_count = await ctx.state_manager.count_agent_logs()

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "redis": {
            "used_memory_human": redis_mem.get("used_memory_human", "N/A"),
            "peak_memory_human": redis_mem.get("used_memory_peak_human", "N/A"),
            "connected_clients": redis_clients.get("connected_clients", 0),
        },
        "agents": {
            "total_registered": len(registry),
            "available": len(available),
            "unavailable": len(registry) - len(available),
            "open_circuit_breakers": open_circuits,
        },
        "queues": {
            "total_queued": sum(q["length"] for q in queues.values()),
            "per_agent": queues,
        },
        "logs": {
            "total_stored": log_count,
        },
    }


@router.post("/system/broadcast", summary="에이전트 브로드캐스트")
async def broadcast_message(body: BroadcastBody) -> dict[str, Any]:
    """
    메시지를 지정한 에이전트(또는 전체)에 브로드캐스트합니다.
    에이전트 설정 갱신 알림이나 긴급 지시에 사용합니다.
    """
    if body.target_agents:
        targets = body.target_agents
    else:
        registry = await ctx.redis_client.hgetall("agents:registry")
        targets = list(registry.keys())

    from cassiopeia_sdk.client import AgentMessage as _AgentMessage

    pushed: list[str] = []
    for agent_name in targets:
        msg = _AgentMessage(
            sender="cassiopeia",
            receiver=agent_name,
            action="broadcast",
            payload={
                "task_id": str(uuid.uuid4()),
                "content": body.message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        await ctx.redis_client.publish(f"agent:{agent_name}", msg.to_json())
        pushed.append(agent_name)

    return {
        "status": "sent",
        "target_count": len(pushed),
        "targets": pushed,
        "message_preview": body.message[:100],
    }


# ══════════════════════════════════════════════════════════════════════════════
# DLQ 관리
# ══════════════════════════════════════════════════════════════════════════════

_DLQ_KEY = "cassiopeia:dlq"
_CASSIOPEIA_TASKS_KEY = "agent:cassiopeia:tasks"


@router.get("/dlq", summary="DLQ 항목 목록 조회")
async def list_dlq(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Dead Letter Queue에 쌓인 실패 태스크 목록을 반환합니다."""
    total = await ctx.redis_client.llen(_DLQ_KEY)
    raw_items = await ctx.redis_client.lrange(_DLQ_KEY, offset, offset + limit - 1)

    items: list[dict[str, Any]] = []
    for raw in raw_items:
        try:
            items.append(json.loads(raw))
        except Exception:
            items.append({"raw": raw[:200]})

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("/dlq/replay", summary="DLQ 태스크 재처리")
async def replay_dlq_task(body: DLQReplayBody) -> dict[str, Any]:
    """DLQ에서 특정 task_id 항목을 찾아 카시오페아 큐로 재삽입합니다."""
    total = await ctx.redis_client.llen(_DLQ_KEY)
    raw_items = await ctx.redis_client.lrange(_DLQ_KEY, 0, total - 1)

    target_raw: str | None = None
    target_entry: dict[str, Any] | None = None

    for raw in raw_items:
        try:
            entry = json.loads(raw)
            if entry.get("task_id") == body.task_id:
                target_raw = raw
                target_entry = entry
                break
        except Exception:
            continue

    if target_entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DLQ에서 task_id='{body.task_id}'를 찾을 수 없습니다.",
        )

    # 재처리를 위해 카시오페아에 Pub/Sub으로 재전달
    from cassiopeia_sdk.client import AgentMessage as _AgentMessage
    replay_task = {
        "task_id": target_entry.get("task_id"),
        "session_id": target_entry.get("session_id", "dlq-replay"),
        "requester": target_entry.get("requester", {"user_id": "admin", "channel_id": "dlq"}),
        "content": target_entry.get("content", ""),
        "source": "dlq_replay",
        "replayed_at": datetime.now(timezone.utc).isoformat(),
    }
    replay_msg = _AgentMessage(
        sender="admin",
        receiver="cassiopeia",
        action="user_request",
        payload=replay_task,
    )
    await ctx.redis_client.publish("agent:cassiopeia", replay_msg.to_json())

    # DLQ에서 해당 항목 제거 (lrem: 첫 번째 일치 항목 1개 제거)
    if target_raw:
        await ctx.redis_client.lrem(_DLQ_KEY, 1, target_raw)

    return {"replayed": 1, "task_id": body.task_id}


@router.delete("/dlq", summary="DLQ 전체 비우기")
async def clear_dlq() -> dict[str, Any]:
    """Dead Letter Queue 전체를 삭제합니다."""
    count = await ctx.redis_client.llen(_DLQ_KEY)
    if count > 0:
        await ctx.redis_client.delete(_DLQ_KEY)
    return {"cleared": count, "message": f"DLQ에서 {count}개 항목이 삭제되었습니다."}


# ══════════════════════════════════════════════════════════════════════════════
# 샌드박스 키 관리
# ══════════════════════════════════════════════════════════════════════════════

_SANDBOX_KEYS_HASH = "sandbox:keys"


@router.get("/sandbox/keys", summary="샌드박스 API 키 목록 조회")
async def list_sandbox_keys() -> dict[str, Any]:
    """등록된 모든 샌드박스 API 키와 라벨 목록을 반환합니다."""
    keys = await ctx.redis_client.hgetall(_SANDBOX_KEYS_HASH)
    # 보안을 위해 키의 앞부분만 노출 (마스킹)
    masked_keys = {
        k[:8] + "..." + k[-4:]: label 
        for k, label in keys.items()
    }
    return {
        "total": len(keys),
        "keys": masked_keys,
        "raw_keys_count": len(keys)
    }


@router.post("/sandbox/keys", status_code=status.HTTP_201_CREATED, summary="샌드박스 API 키 생성")
async def generate_sandbox_key(body: SandboxKeyGenerateBody) -> dict[str, Any]:
    """새로운 무작위 샌드박스 API 키를 생성하고 저장합니다."""
    import secrets
    new_key = secrets.token_hex(32)
    
    await ctx.redis_client.hset(_SANDBOX_KEYS_HASH, new_key, body.label)
    
    return {
        "status": "created",
        "label": body.label,
        "key": new_key,
        "note": "이 키는 다시 조회할 수 없으므로 안전한 곳에 저장하세요."
    }


@router.delete("/sandbox/keys/{key_prefix}", summary="샌드박스 API 키 삭제")
async def delete_sandbox_key(key_prefix: str) -> dict[str, Any]:
    """마스킹된 키 프리픽스(앞 8자리)를 기반으로 해당 키를 삭제합니다."""
    all_keys = await ctx.redis_client.hkeys(_SANDBOX_KEYS_HASH)
    
    target_key: str | None = None
    for k in all_keys:
        if k.startswith(key_prefix):
            target_key = k
            break
            
    if not target_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"프리픽스 '{key_prefix}'로 시작하는 키를 찾을 수 없습니다."
        )
        
    await ctx.redis_client.hdel(_SANDBOX_KEYS_HASH, target_key)
    
    return {
        "status": "deleted",
        "deleted_prefix": key_prefix
    }
