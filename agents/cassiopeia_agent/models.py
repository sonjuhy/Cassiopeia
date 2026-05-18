"""
카시오페아 에이전트 데이터 모델 (Python 3.12+)
- NLU 결과 스키마 (Pydantic v2: 구조 검증용)
- Redis 통신 메시지 스키마 (TypedDict: 내부 타입 힌트용)
"""

from __future__ import annotations

import os
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


# ── NLU 출력 스키마 (Pydantic - 검증 필수) ────────────────────────────────────

class NLUMetadata(BaseModel):
    """NLU 결과 메타데이터"""
    reason: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    requires_user_approval: bool = False


class PlanStepMetadata(BaseModel):
    """복합 작업 단계 메타데이터"""
    reason: str
    requires_user_approval: bool = False


class PlanStep(BaseModel):
    """복합 작업 단일 실행 단계"""
    step: int
    selected_agent: str
    action: str
    params: dict[str, Any]
    depends_on: list[int] = Field(default_factory=list)
    metadata: PlanStepMetadata


# 신뢰도 임계값 (환경변수 NLU_CONFIDENCE_THRESHOLD로 재정의 가능)
NLU_CONFIDENCE_THRESHOLD: float = float(os.environ.get("NLU_CONFIDENCE_THRESHOLD", "0.7"))


# ── Redis 통신 메시지 스키마 (TypedDict) ──────────────────────────────────────

class TaskRequester(TypedDict):
    """작업 요청자 정보"""
    user_id: str
    channel_id: str


class CassiopeiaTask(TypedDict):
    """소통 에이전트 → 카시오페아 작업 요청 (agent:cassiopeia:tasks 큐)"""
    task_id: str
    session_id: str        # NLU 컨텍스트 주입용 (format: user_id:channel_id)
    requester: TaskRequester
    content: str
    source: str            # 소스 플랫폼 식별자 (slack | discord | telegram | ...)
    thread_ts: str | None


class RetryInfo(TypedDict):
    """재시도 정보"""
    count: int
    max_retries: int
    reason: str | None


class DispatchMessage(TypedDict):
    """카시오페아 → 하위 에이전트 작업 지시서 (agent:{name}:tasks 큐)"""
    version: str
    task_id: str
    session_id: str
    timestamp: str
    requester: TaskRequester
    agent: str
    action: str
    content: str           # 사용자의 원본 요청 텍스트
    context: list[dict[str, Any]] # 이전 대화 이력 (LLM 컨텍스트 형식)
    params: dict[str, Any]
    retry_info: RetryInfo
    priority: str          # LOW | MEDIUM | HIGH | CRITICAL
    timeout: int           # 초 단위
    metadata: dict[str, Any]


class AgentResultError(TypedDict):
    """에이전트 실행 오류"""
    code: str
    message: str
    traceback: str | None


class AgentResult(TypedDict):
    """하위 에이전트 → 카시오페아 결과 (cassiopeia:results:{task_id} 큐)"""
    task_id: str
    agent: str             # 결과를 보낸 에이전트 이름
    status: str            # COMPLETED | FAILED | WAITING_USER | PROCESSING
    result_data: dict[str, Any]
    reference_id: str | None
    payload_summary: str | None
    error: AgentResultError | None
    usage_stats: dict[str, Any]


class AgentHealth(TypedDict):
    """에이전트 헬스 정보 (agent:{name}:health Redis Hash)"""
    agent_id: str
    status: str            # IDLE | BUSY | MAINTENANCE | ERROR
    lifecycle_type: str    # long_running | ephemeral
    last_heartbeat: str    # ISO 8601
    version: str
    capabilities: list[str]
    current_tasks: int
    max_concurrency: int


class CommAgentMessage(TypedDict):
    """카시오페아 → 소통 에이전트 전달 메시지 (agent:communication:tasks 큐)"""
    task_id: str                    # 승인 task_id (cassiopeia:approval:{task_id} 응답용)
    content: str                    # 마크다운 본문
    requires_user_approval: bool
    agent_name: str
    progress_percent: int | None    # None = 최종 결과, 0~99 = 진행 중


def _build_timeout_map() -> dict[str, int]:
    """에이전트별 기본 타임아웃 맵을 반환합니다.
    AGENT_TIMEOUT_OVERRIDES 환경변수로 개별 재정의 가능 (예: "archive_agent:900,file_agent:180").
    """
    base: dict[str, int] = {
        "archive_agent": 300,
        "research_agent": 300,
        "calendar_agent": 60,
        "file_agent": 120,
        "communication_agent": 30,
        "sandbox_agent": 60,
        "agent_builder": 120,
    }
    for entry in os.environ.get("AGENT_TIMEOUT_OVERRIDES", "").split(","):
        entry = entry.strip()
        if ":" in entry:
            name, val = entry.split(":", 1)
            try:
                base[name.strip()] = int(val.strip())
            except ValueError:
                pass
    return base

# 에이전트 레지스트리: 에이전트 이름 → 기본 timeout(초)
# 환경변수 AGENT_TIMEOUT_OVERRIDES="agent_name:seconds,..." 로 개별 재정의 가능
AGENT_TIMEOUT_MAP: dict[str, int] = _build_timeout_map()

# 재시도 가능한 에러 코드
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset({
    "TIMEOUT", "RATE_LIMIT", "EXTERNAL_API_ERROR", "INTERNAL_ERROR", "EXECUTION_ERROR"
})
