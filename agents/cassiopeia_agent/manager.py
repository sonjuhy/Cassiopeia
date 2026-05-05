"""
카시오페아 매니저 (CassiopeiaManager)
- NLU → Plan → Dispatch → Monitor 전체 파이프라인
- Redis agent:cassiopeia:tasks 큐 수신 및 세션/스레드 기반 컨텍스트 관리
- SQLite 영구 저장소 연동 (StateManager)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
from cassiopeia_sdk.client import CassiopeiaClient

from .auth import CLIENT_API_KEY
from .health_monitor import HealthMonitor
from shared_core.dispatch_auth import DispatchAuthError, verify_task
from .sandbox_tool import SandboxTool
from .scheduler import ScheduledTaskRunner
from .models import (
    AGENT_TIMEOUT_MAP,
    RETRYABLE_ERROR_CODES,
    AgentResult,
    CommAgentMessage,
    DispatchMessage,
    MultiStepNLUResult,
    NLUResult,
    CassiopeiaTask,
    PlanStep,
    RetryInfo,
    SingleNLUResult,
)
from .nlu_engine import GeminiNLUEngine, build_nlu_engine
from .state_manager import StateManager

logger = logging.getLogger("cassiopeia_agent.manager")

# 액션 화이트리스트: LLM 판단과 무관하게 서버가 강제로 사용자 승인을 요구하는 액션 목록.
# 되돌리기 어려운(파괴적) 작업만 포함한다.
APPROVAL_REQUIRED_ACTIONS: frozenset[str] = frozenset({
    # 파일 시스템
    "delete_file",
    "write_file",
    "overwrite_file",
    # 코드 실행
    "execute_code",
    "run_code",
    "execute_tdd_cycle",
    # 캘린더 변경
    "add_schedule",
    "modify_schedule",
    "remove_schedule",
    # 커뮤니케이션 발송
    "send_message",
    "send_email",
})


def _requires_approval(action: str, llm_flag: bool) -> bool:
    """서버사이드 승인 여부 결정.

    action 이 APPROVAL_REQUIRED_ACTIONS 에 있으면 LLM 판단과 무관하게 True 를 반환한다.
    그렇지 않으면 LLM 의 llm_flag 를 그대로 따른다.
    """
    return action in APPROVAL_REQUIRED_ACTIONS or llm_flag


# Redis 설정
_CASSIOPEIA_TASKS_KEY = "agent:cassiopeia:tasks"
_RESULTS_KEY_PREFIX = "cassiopeia:results:"
_APPROVAL_KEY_PREFIX = "cassiopeia:approval:"
_DLQ_KEY = "cassiopeia:dlq"
_MSG_VERSION = "1.1"
_APPROVAL_TIMEOUT_SEC: int = int(os.environ.get("APPROVAL_TIMEOUT_SEC", "300"))
_BLPOP_TIMEOUT: int = int(os.environ.get("BLPOP_TIMEOUT", "5"))
_LLM_MODEL: str = os.environ.get("NLU_LLM_MODEL", "gemini-2.5-flash")
_LLM_TEMPERATURE: float = float(os.environ.get("NLU_LLM_TEMPERATURE", "0.2"))

# 플랫폼별 통신 큐 (source → queue key)
_PLATFORM_COMM_QUEUE: dict[str, str] = {
    "slack": "agent:communication:tasks",
    "discord": "agent:communication:discord:tasks",
    "telegram": "agent:communication:telegram:tasks",
}
_DEFAULT_COMM_QUEUE = "agent:communication:tasks"


def _build_dispatch_message(
    task_id: str,
    session_id: str,
    agent_name: str,
    action: str,
    params: dict[str, Any],
    requester: dict[str, str],
    timeout: int,
    content: str = "",
    step_info: dict[str, int] | None = None,
    retry_info: RetryInfo | None = None,
    requires_approval: bool = False,
    user_llm_keys: dict[str, str] | None = None,
) -> DispatchMessage:
    """에이전트에 전달할 작업 지시서를 생성합니다."""
    return {
        "version": _MSG_VERSION,
        "task_id": task_id,
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "requester": requester,
        "content": content,
        "agent": agent_name,
        "action": action,
        "params": params,
        "retry_info": retry_info or {"count": 0, "max_retries": 3, "reason": None},
        "priority": "MEDIUM",
        "timeout": timeout,
        "metadata": {
            "llm_config": {"model": _LLM_MODEL, "temperature": _LLM_TEMPERATURE},
            "step_info": step_info or {},
            "requires_user_approval": requires_approval,
            "user_llm_keys": user_llm_keys or {},
            "callback_api_key": CLIENT_API_KEY,
        },
    }


def resolve_placeholders(params: dict[str, Any], results: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """{{step_N.result.field}} 형식의 플레이스홀더를 실제 결과로 치환합니다."""
    params_str = json.dumps(params, ensure_ascii=False)

    def replacer(match: re.Match) -> str:
        step_num = int(match.group(1))
        field_path = match.group(2).split(".")
        value: Any = results.get(step_num, {})
        for key in field_path:
            value = value.get(key, "") if isinstance(value, dict) else ""
        # json.dumps로 직렬화하여 JSON 특수문자(", \, 개행 등)를 이스케이프합니다.
        # 문자열 값은 따옴표를 포함한 JSON 리터럴이 되므로, 플레이스홀더가
        # 이미 따옴표 안에 있는 경우(예: "{{step_1.result.x}}")를 위해
        # 문자열이면 앞뒤 따옴표를 제거하고 이스케이프된 내용만 반환합니다.
        serialized = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            # "escaped content" → escaped content (따옴표 제거)
            return serialized[1:-1]
        return serialized

    resolved = re.sub(r"\{\{step_(\d+)\.result\.([\w.]+)\}\}", replacer, params_str)
    try:
        return json.loads(resolved)
    except json.JSONDecodeError:
        return params


class CassiopeiaManager:
    """
    카시오페아 에이전트 메인 관제 클래스.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        nlu_engine: GeminiNLUEngine | None = None,
        state_manager: StateManager | None = None,
        health_monitor: HealthMonitor | None = None,
        sandbox_tool: SandboxTool | None = None,
        cassiopeia: CassiopeiaClient | None = None,
    ) -> None:
        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
        if redis_client is not None:
            self._redis = redis_client
        else:
            self._redis = aioredis.from_url(redis_url, decode_responses=True, socket_timeout=5.0)

        self._nlu = nlu_engine or build_nlu_engine()
        self._state = state_manager or StateManager(redis_client=self._redis)
        self._health = health_monitor or HealthMonitor(redis_client=self._redis)
        self._sandbox_tool: SandboxTool | None = sandbox_tool
        self.scheduler = ScheduledTaskRunner(redis_client=self._redis)
        self._cassiopeia = cassiopeia or CassiopeiaClient(agent_id="cassiopeia", redis_url=redis_url)
        self._llm_gateway = None  # LLMGatewayHandler — lifespan에서 주입

    async def start_background_tasks(self) -> list[asyncio.Task]:
        """헬스 모니터와 스케줄러를 백그라운드 태스크로 시작합니다."""
        tasks = [
            asyncio.create_task(self._health.monitor_loop(), name="health_monitor"),
            asyncio.create_task(self.scheduler.run_loop(), name="scheduler"),
        ]
        logger.info("[CassiopeiaManager] 백그라운드 태스크 시작 (health_monitor, scheduler)")
        return tasks

    async def listen_tasks(self) -> None:
        """메인 루프: cassiopeia Pub/Sub에서 작업을 수신합니다."""
        logger.info("[CassiopeiaManager] 메인 루프 시작")
        await self._cassiopeia.connect()
        try:
            async for msg in self._cassiopeia.listen():
                task: CassiopeiaTask = dict(msg.payload)
                action: str = getattr(msg, "action", "user_request")

                if action == "llm_call":
                    asyncio.create_task(self._route_message(action, task))
                    continue

                try:
                    verify_task(task)
                except DispatchAuthError as exc:
                    logger.error("[CassiopeiaManager] 서명 검증 실패 — DLQ로 이동: %s", exc)
                    await self._redis.rpush(
                        "cassiopeia:dlq",
                        json.dumps({
                            "id": str(uuid.uuid4()),
                            "reason": "INVALID_SIGNATURE",
                            "task_id": task.get("task_id", "unknown"),
                            "error": {"code": "INVALID_SIGNATURE", "message": str(exc)},
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }, ensure_ascii=False),
                    )
                    continue
                asyncio.create_task(self._safe_process_task(task))
        except asyncio.CancelledError:
            pass
        finally:
            await self._cassiopeia.disconnect()

    async def _route_message(self, action: str, payload: dict) -> None:
        """액션 유형에 따라 메시지를 적절한 핸들러로 라우팅합니다."""
        if action == "llm_call":
            if self._llm_gateway is None:
                logger.warning("[CassiopeiaManager] LLM Gateway 미초기화 — llm_call 무시")
                return
            await self._llm_gateway.handle(payload)
        else:
            await self.process_task(payload)

    async def _safe_process_task(self, task: CassiopeiaTask) -> None:
        try:
            await self.process_task(task)
        except Exception as exc:
            logger.exception("[CassiopeiaManager] 태스크 처리 실패: %s", exc)
            await self._send_error_to_user(task, str(exc))

    async def process_task(self, task: CassiopeiaTask) -> None:
        """NLU → Plan → Dispatch → Monitor 파이프라인"""
        user_text = task.get("content", "")
        requester = task.get("requester", {})
        user_id = requester.get("user_id", "unknown")
        channel_id = requester.get("channel_id", "unknown")
        thread_id = requester.get("thread_ts")
        session_id = thread_id if thread_id else task.get("session_id", str(uuid.uuid4()))
        task_id = task.get("task_id", str(uuid.uuid4()))

        await self._state.init_session(session_id, user_id, channel_id)
        await self._state.add_message(session_id, user_id, "user", user_text, provider="slack", thread_id=thread_id)

        # user_id 를 태스크 상태에 기록해 소유권 검증에 사용
        await self._state.update_task_state(task_id, {
            "status": "PROCESSING",
            "session_id": session_id,
            "user_id": user_id,
        })

        context = await self._state.build_context_for_llm(session_id, user_id)
        summary_data = await self._state.get_session_context_summary(session_id)

        # 활성 에이전트 캐퍼빌리티를 Redis에서 동적으로 로드 (미등록 시 NLU 엔진 내부 폴백 사용)
        agent_capabilities = await self._health.get_nlu_capabilities() or None

        nlu_result: NLUResult = await self._nlu.analyze(
            user_text, session_id, context,
            style_guide=summary_data.get("style"),
            agent_capabilities=agent_capabilities,
            user_llm_keys=summary_data.get("llm_keys"),
        )

        if nlu_result.type == "direct_response":
            await self._send_to_comm_agent(task, nlu_result.params["answer"], False, "cassiopeia")
        elif nlu_result.type == "clarification":
            await self._route_clarification(nlu_result, task)
        elif nlu_result.type == "multi_step":
            await self.run_plan(nlu_result, task)
        else:
            await self._route_single(nlu_result, task)

    async def _route_clarification(self, nlu_result: Any, task: CassiopeiaTask) -> None:
        content = nlu_result.params.question
        if nlu_result.params.options:
            content += "\n\n" + "\n".join(f"• {opt}" for opt in nlu_result.params.options)
        await self._send_to_comm_agent(task, content, False, "communication_agent")

    async def _route_single(self, nlu_result: SingleNLUResult, task: CassiopeiaTask) -> None:
        agent_name = nlu_result.selected_agent
        dispatch_task_id = str(uuid.uuid4())

        # 내부 도구(sandbox)는 헬스체크 없이 직접 실행
        if not self._is_internal_tool(agent_name):
            ready, reason = await self._health.is_agent_ready(agent_name)
            if not ready:
                await self._send_agent_unavailable_error(task, agent_name, reason)
                return

        timeout = AGENT_TIMEOUT_MAP.get(agent_name, 300)
        needs_approval = _requires_approval(nlu_result.action, nlu_result.metadata.requires_user_approval)
        dispatch = _build_dispatch_message(
            dispatch_task_id, task["session_id"], agent_name, nlu_result.action,
            nlu_result.params, task["requester"], timeout, content=task.get("content", ""),
            requires_approval=needs_approval
        )

        if needs_approval:
            approval_msg = f"다음 작업을 실행하시겠습니까?\n- 에이전트: {agent_name}\n- 액션: {nlu_result.action}\n- 파라미터: {json.dumps(nlu_result.params, ensure_ascii=False)}"
            # 임시 Result 모방 객체 (request_user_approval의 호환성 유지)
            fake_result = {"agent": "cassiopeia", "result_data": {"summary": approval_msg}}
            if not await self.request_user_approval(fake_result, task):
                await self._send_to_comm_agent(task, "사용자에 의해 작업이 취소되었습니다.", False, "cassiopeia")
                return

        result = await self._execute_agent_task(agent_name, dispatch_task_id, dispatch, timeout)
        await self._handle_agent_result(result, task, False)  # 실행 후에는 승인 요청 안함

    async def run_plan(self, nlu_result: MultiStepNLUResult, original_task: CassiopeiaTask) -> None:
        plan = nlu_result.plan
        results: dict[int, dict[str, Any]] = {}
        total_steps = len(plan)

        for step in sorted(plan, key=lambda s: s.step):
            params = resolve_placeholders(step.params, results)
            dispatch_task_id = str(uuid.uuid4())
            timeout = AGENT_TIMEOUT_MAP.get(step.selected_agent, 300)
            
            step_needs_approval = _requires_approval(step.action, step.metadata.requires_user_approval)
            dispatch = _build_dispatch_message(
                dispatch_task_id, original_task["session_id"], step.selected_agent,
                step.action, params, original_task["requester"], timeout,
                content=original_task.get("content", ""),
                step_info={"current": step.step, "total": total_steps},
                requires_approval=step_needs_approval
            )

            if step_needs_approval:
                approval_msg = f"다음 작업을 실행하시겠습니까?\n- 에이전트: {step.selected_agent}\n- 액션: {step.action}\n- 파라미터: {json.dumps(params, ensure_ascii=False)}"
                fake_result = {"agent": "cassiopeia", "result_data": {"summary": approval_msg}}
                if not await self.request_user_approval(fake_result, original_task):
                    await self._send_to_comm_agent(original_task, "사용자에 의해 작업이 취소되었습니다.", False, "cassiopeia")
                    return

            await self._send_progress_to_comm(original_task, int((step.step-1)/total_steps*100), f"[{step.step}/{total_steps}] {step.selected_agent} 작업 중...")
            result = await self._execute_agent_task(step.selected_agent, dispatch_task_id, dispatch, timeout)
            
            if result.get("status") == "FAILED":
                await self._send_error_to_user(original_task, result.get("error", {}).get("message", "오류"), step.selected_agent)
                return

            results[step.step] = result.get("result_data", {})

        final_res = results.get(plan[-1].step, {})
        summary = final_res.get("summary", "모든 단계가 완료되었습니다.")
        content = final_res.get("content", "")
        full_message = f"{summary}\n\n{content}".strip() if content else summary
        await self._send_to_comm_agent(original_task, full_message, False, "cassiopeia")

    async def wait_for_result(self, task_id: str, timeout: int = 600) -> dict[str, Any]:
        key = f"{_RESULTS_KEY_PREFIX}{task_id}"
        remaining = timeout
        while remaining > 0:
            res = await self._redis.blpop(key, timeout=min(_BLPOP_TIMEOUT, remaining))
            if res: return json.loads(res[1])
            remaining -= _BLPOP_TIMEOUT
        failed: dict[str, Any] = {
            "status": "FAILED",
            "task_id": task_id,
            "error": {"code": "TIMEOUT", "message": f"에이전트 응답 없음 ({timeout}초 초과)", "traceback": None},
        }
        await self._push_to_dlq("timeout", task_id, failed["error"])
        return failed

    async def cancel_task(self, task_id: str, user_id: str) -> bool:
        """진행 중인 태스크를 취소합니다. wait_for_result 대기를 해제하기 위해 가짜 결과를 푸시합니다."""
        state = await self._state.get_task_state(task_id)
        if not state:
            return False

        # 소유권 검증: 태스크를 제출한 user_id 와 취소 요청자가 일치해야 한다
        task_owner = state.get("user_id")
        if task_owner and task_owner != user_id:
            raise PermissionError(
                f"이 태스크를 취소할 권한이 없습니다. "
                f"(요청자: {user_id}, 소유자: {task_owner})"
            )

        current_status = state.get("status", "UNKNOWN")
        if current_status in ("COMPLETED", "FAILED", "CANCELLED"):
            return False # 이미 종료된 상태
            
        # 1. 상태 업데이트
        await self._state.update_task_state(task_id, {"status": "CANCELLED"})
        await self._state.update_task_history_status(task_id, "CANCELLED")
        
        # 2. wait_for_result 블로킹 해제를 위한 가짜 결과(CANCELLED) 주입
        cancel_result: AgentResult = {
            "task_id": task_id,
            "status": "FAILED",
            "agent": "cassiopeia",
            "result_data": {},
            "error": {
                "code": "CANCELLED_BY_USER",
                "message": "사용자에 의해 작업이 취소되었습니다.",
                "traceback": None
            },
            "usage_stats": {}
        }
        await self.receive_agent_result(cancel_result)
        
        # 3. 진행 로그 기록
        session_id = state.get("session_id")
        await self._state.add_agent_log(
            "cassiopeia", "cancel_task", "사용자가 태스크를 취소했습니다.", task_id, session_id
        )
        return True

    async def _push_to_dlq(self, reason: str, task_id: str, error: dict[str, Any]) -> None:
        entry = {
            "reason": reason,
            "task_id": task_id,
            "error": error,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self._redis.rpush(_DLQ_KEY, json.dumps(entry, ensure_ascii=False))
            logger.warning("[CassiopeiaManager] DLQ 저장: task_id=%s reason=%s", task_id, reason)
        except Exception as exc:
            logger.error("[CassiopeiaManager] DLQ 저장 실패: %s", exc)

    async def receive_agent_result(self, result: AgentResult) -> None:
        task_id = result["task_id"]
        await self._redis.rpush(f"{_RESULTS_KEY_PREFIX}{task_id}", json.dumps(result, ensure_ascii=False))

    async def _handle_agent_result(self, result: dict[str, Any], task: CassiopeiaTask, requires_approval: bool) -> None:
        if result.get("status") == "FAILED":
            await self._send_error_to_user(task, result.get("error", {}).get("message", "오류"), result.get("agent"))
            return
        
        res_data = result.get("result_data", {})
        summary = res_data.get("summary", "작업 완료")
        content = res_data.get("content", "")
        full_message = f"{summary}\n\n{content}".strip() if content else summary

        if requires_approval:
            if not await self.request_user_approval(result, task): return
        await self._send_to_comm_agent(task, full_message, False, result.get("agent", "agent"))

    def _get_comm_queue(self, task: CassiopeiaTask) -> str:
        """태스크의 source 필드를 기반으로 플랫폼별 통신 큐 키를 반환합니다."""
        source = task.get("source", "slack")
        return _PLATFORM_COMM_QUEUE.get(source, _DEFAULT_COMM_QUEUE)

    async def request_user_approval(self, result: dict[str, Any], task: CassiopeiaTask) -> bool:
        approval_id = str(uuid.uuid4())
        msg: CommAgentMessage = {
            "task_id": approval_id,
            "content": f"승인 필요: {result.get('result_data', {}).get('summary')}",
            "requires_user_approval": True,
            "agent_name": result.get("agent"),
        }
        source = task.get("source", "slack")
        await self._cassiopeia.send_message(
            action="request_approval",
            payload={**msg, "platform": source},
            receiver="communication",
        )
        res = await self._redis.blpop(f"{_APPROVAL_KEY_PREFIX}{approval_id}", timeout=_APPROVAL_TIMEOUT_SEC)
        return json.loads(res[1]).get("action") == "approve" if res else False

    def _is_internal_tool(self, agent_name: str) -> bool:
        """에이전트가 인프로세스 내부 도구인지 확인합니다."""
        return agent_name == "sandbox_agent" and self._sandbox_tool is not None

    async def _execute_agent_task(
        self,
        agent_name: str,
        task_id: str,
        dispatch: DispatchMessage,
        timeout: int,
    ) -> dict[str, Any]:
        """에이전트 작업 실행: sandbox는 인프로세스 직접 실행, 나머지는 Redis 큐 경유."""
        if self._is_internal_tool(agent_name):
            return await self._run_sandbox_task(task_id, dispatch["params"])
        await self._dispatch_to_agent(agent_name, dispatch)
        return await self.wait_for_result(task_id, timeout=timeout)

    async def _run_sandbox_task(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """SandboxTool을 통해 코드를 직접 실행하고 AgentResult 형식으로 반환합니다."""
        if self._sandbox_tool is None:
            return {
                "task_id": task_id, "status": "FAILED",
                "result_data": {},
                "error": {"code": "SANDBOX_DISABLED", "message": "샌드박스 기능이 비활성화되어 있습니다.", "traceback": None},
                "usage_stats": {},
            }
        try:
            result = await self._sandbox_tool.execute_code(params)  # type: ignore[union-attr]
            return {
                "task_id": task_id,
                "status": "COMPLETED",
                "result_data": {
                    **result,
                    "summary": f"코드 실행 완료 (exit_code={result['exit_code']})",
                    "content": result.get("stdout", ""),
                },
                "error": None,
                "usage_stats": {
                    "runtime": result.get("runtime_used"),
                    "elapsed_ms": result.get("execution_time_ms"),
                },
            }
        except ValueError as exc:
            return {
                "task_id": task_id, "status": "FAILED",
                "result_data": {},
                "error": {"code": "INVALID_PARAMS", "message": str(exc), "traceback": None},
                "usage_stats": {},
            }
        except Exception as exc:
            logger.error("[CassiopeiaManager] 샌드박스 실행 실패: task_id=%s %s", task_id, exc)
            return {
                "task_id": task_id, "status": "FAILED",
                "result_data": {},
                "error": {"code": "EXECUTION_ERROR", "message": str(exc), "traceback": None},
                "usage_stats": {},
            }

    async def _dispatch_to_agent(self, agent_name: str, dispatch: DispatchMessage) -> None:
        await self._cassiopeia.send_message(
            action=dispatch["action"],
            payload=dict(dispatch),
            receiver=agent_name,
        )

    async def _send_to_comm_agent(self, task: CassiopeiaTask, content: str, requires_approval: bool, agent_name: str) -> None:
        req = task.get("requester", {})
        session_id = req.get("thread_ts") or task.get("session_id")
        if session_id:
            await self._state.add_message(session_id, req.get("user_id", "unknown"), "assistant", content, provider="cassiopeia")

        msg: CommAgentMessage = {
            "task_id": task.get("task_id", str(uuid.uuid4())),
            "content": content,
            "requires_user_approval": requires_approval,
            "agent_name": agent_name,
        }
        source = task.get("source", "slack")
        await self._cassiopeia.send_message(
            action="send_message",
            payload={**msg, "platform": source},
            receiver="communication",
        )

    async def _send_progress_to_comm(self, task: CassiopeiaTask, percent: int, message: str) -> None:
        msg: CommAgentMessage = {
            "task_id": task.get("task_id", str(uuid.uuid4())),
            "content": message,
            "requires_user_approval": False,
            "agent_name": "cassiopeia",
            "progress_percent": percent,
        }
        source = task.get("source", "slack")
        await self._cassiopeia.send_message(
            action="send_progress",
            payload={**msg, "platform": source},
            receiver="communication",
        )

    async def _send_error_to_user(self, task: CassiopeiaTask, error_message: str, agent_name: str = "cassiopeia") -> None:
        content = f"[{agent_name}] 오류: {error_message}"
        await self._send_to_comm_agent(task, content, False, agent_name)

    async def _send_agent_unavailable_error(self, task: CassiopeiaTask, agent_name: str, reason: str) -> None:
        await self._send_error_to_user(task, f"에이전트 {agent_name} 사용 불가 ({reason})")
