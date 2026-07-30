"""
카시오페아 매니저 (CassiopeiaManager)
- NLU → Plan → Dispatch → Monitor 전체 파이프라인
- SDK v0.3.0의 AgentBrain을 사용하여 중앙 라우팅 및 의도 분석을 수행합니다.
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
from cassiopeia_sdk.brain import AgentBrain, AgentBrainConfig
from cassiopeia_sdk.tools import Tool

from .auth import CLIENT_API_KEY
from .health_monitor import HealthMonitor
from shared_core.dispatch_auth import DispatchAuthError, verify_task, sign_dispatch
from .sandbox_tool import SandboxTool
from .scheduler import ScheduledTaskRunner
from .models import (
    AGENT_TIMEOUT_OVERRIDES_MAP,
    DEFAULT_AGENT_TIMEOUT,
    RETRYABLE_ERROR_CODES,
    AgentResult,
    CommAgentMessage,
    DispatchMessage,
    CassiopeiaTask,
    PlanStep,
    RetryInfo,
)
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
    """서버사이드 승인 여부 결정."""
    return action in APPROVAL_REQUIRED_ACTIONS or llm_flag


# LLM 라우팅 시 에이전트가 params_schema 를 선언하지 않은 경우 사용하는 일반 가이드.
_GENERIC_PARAMS_GUIDE: dict[str, str] = {
    "action": "수행할 작업 명칭",
    "params": "작업 파라미터 (dict)",
}


def _tool_parameters_for(reg_data: dict[str, Any]) -> dict[str, Any]:
    """레지스트리에 선언된 params_schema 를 라우팅 도구 파라미터로 반환합니다.

    지휘자는 에이전트 이름을 비교하지 않습니다. 에이전트가 register() 로 선언한
    params_schema 가 있으면 그것을 쓰고, 없으면 일반 가이드를 복사해 돌려줍니다.
    """
    schema = reg_data.get("params_schema")
    if isinstance(schema, dict) and schema:
        return schema
    return dict(_GENERIC_PARAMS_GUIDE)


def _resolve_timeout(
    agent_name: str,
    reg_data: dict[str, Any],
    overrides: dict[str, int] = AGENT_TIMEOUT_OVERRIDES_MAP,
) -> int:
    """에이전트 작업 타임아웃(초)을 결정합니다.

    우선순위: 운영자 오버라이드(env) > 에이전트 선언(default_timeout) > 전역 기본값.
    어느 단계에서도 에이전트 이름을 코드에 하드코딩하지 않습니다.
    """
    override = overrides.get(agent_name)
    if isinstance(override, int) and override > 0:
        return override
    declared = reg_data.get("default_timeout")
    if isinstance(declared, int) and declared > 0:
        return declared
    return DEFAULT_AGENT_TIMEOUT


# 레지스트리에 적합한 커뮤니케이션 에이전트가 없을 때 사용하는 기본 수신자.
_DEFAULT_COMM_RECEIVER: str = os.environ.get("DEFAULT_COMM_RECEIVER", "communication_agent")


def _resolve_comm_receiver(
    source: str,
    registry: dict[str, dict[str, Any]],
    default: str = _DEFAULT_COMM_RECEIVER,
) -> str:
    """사용자 응답을 전달할 커뮤니케이션 에이전트 이름을 레지스트리에서 찾습니다.

    routing.role == "communication" 인 에이전트 중 routing.platforms 에 source 가
    포함된 에이전트를 선택합니다. platforms 에 "*" 가 있으면 모든 플랫폼을 받는
    catch-all 로 간주하되, 특정 플랫폼을 명시한 에이전트가 우선합니다. 적합한
    에이전트가 없으면 default 를 반환합니다. (에이전트 이름 하드코딩 없음)
    """
    wildcard_match: str | None = None
    for name, data in registry.items():
        routing = data.get("routing") or {}
        if routing.get("role") != "communication":
            continue
        platforms = routing.get("platforms") or []
        if source in platforms:
            return name
        if "*" in platforms and wildcard_match is None:
            wildcard_match = name
    return wildcard_match or default


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


# 지휘자 NLU 시스템 프롬프트.
# 특정 내장 에이전트 이름을 박지 않는다 — 라우팅 후보는 런타임에 레지스트리에서
# 동적으로 주입되는 도구 목록에서만 온다. 예시도 자리표시자로만 기술한다.
_COORDINATOR_CAPABILITIES = """당신은 카시오페아 시스템의 통합 지휘자입니다.
당신의 주 임무는 사용자의 요청을 분석하여, 이를 처리할 수 있는 가장 적절한 '에이전트(도구)'를 선택하는 것입니다.

[반드시 지켜야 할 규칙]
1. 'action' 필드에는 반드시 '주어진 도구 목록'에 있는 '도구 이름(에이전트 ID)' 중 하나를 정확히 기입해야 합니다. 목록에 없는 이름을 지어내지 마십시오.
2. 각 도구 설명에 나열된 '세부 액션'을 'action' 필드에 직접 적지 마십시오. 세부 액션은 'params' 내부의 'action' 필드에 담아야 합니다.
3. 어떤 작업을 처리할 전용 도구가 목록에 있다면, 코드를 직접 짜서 해결하려 하지 말고 반드시 그 전용 도구를 선택하십시오.
4. 단순 인사나 일상 대화는 'direct_response'를 사용하십시오.
5. 절대로 존재하지 않는 ID(UUID 형식)를 지어내거나 추측하지 마십시오. ID를 모를 때는 해당 필드를 생략하십시오. 하위 에이전트가 이름으로 검색할 것입니다.

[응답 형식 예시] (도구 이름과 세부 액션은 실제 주어진 도구 목록에서 가져오십시오)
{
  "action": "<도구 목록에 있는 에이전트 ID>",
  "params": {"action": "<그 에이전트의 세부 액션>", "params": {}},
  "reasoning": "<이 도구를 고른 이유>"
}"""


def _build_dispatch_message(
    task_id: str,
    session_id: str,
    agent_name: str,
    action: str,
    params: dict[str, Any],
    requester: dict[str, str],
    timeout: int,
    content: str = "",
    context: list[dict[str, Any]] | None = None,
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
        "context": context or [],
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


class CassiopeiaManager:
    """
    카시오페아 에이전트 메인 관제 클래스.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
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

        self._state = state_manager or StateManager(redis_client=self._redis)
        self._health = health_monitor or HealthMonitor(redis_client=self._redis)
        self._sandbox_tool: SandboxTool | None = sandbox_tool
        self.scheduler = ScheduledTaskRunner(redis_client=self._redis)
        self._cassiopeia = cassiopeia or CassiopeiaClient(agent_id="cassiopeia", redis_url=redis_url)
        self._llm_gateway = None

        # 인프로세스 내부 툴 레지스트리: {에이전트 이름 -> async (task_id, dispatch) -> result}.
        # 이름 비교를 코드 곳곳에 흩뿌리는 대신 한 곳에 모은다. 외부 SDK 에이전트는
        # 여기에 등록되지 않으므로 일반 Redis 디스패치 경로를 그대로 탄다.
        self._internal_tools: dict[str, Any] = {
            "cassiopeia_agent": lambda task_id, dispatch: self._run_cassiopeia_internal_task(
                task_id, dispatch["action"], dispatch["params"]
            ),
        }
        if self._sandbox_tool is not None:
            self._internal_tools["sandbox_agent"] = (
                lambda task_id, dispatch: self._run_sandbox_task(task_id, dispatch["params"])
            )

        # SDK AgentBrain 초기화
        self.brain = AgentBrain(
            agent_name="cassiopeia_coordinator",
            capabilities=_COORDINATOR_CAPABILITIES,
            backend="gateway",
            llm_caller=self._direct_llm_caller,
            config=AgentBrainConfig(max_retries=2, confidence_threshold=0.7)
        )

    async def _direct_llm_caller(self, messages: list[dict], max_tokens: int = 500, temperature: float = 0.7, model: str | None = None, **kwargs) -> Any:
        """SDK v0.3.0의 GatewayProvider가 요구하는 llm_caller 서명을 만족시키는 어댑터 메서드입니다."""
        from shared_core.llm.factory import build_llm_provider_from_config
        from shared_core.llm.llm_config import LLMConfig
        from cassiopeia_sdk.brain._models import LLMResponse
        
        # 시스템 프롬프트 추출
        system_msgs = [m["content"] for m in messages if m["role"] == "system"]
        system_instruction = "\n".join(system_msgs) if system_msgs else None
        
        # 유저 프롬프트 추출
        user_msgs = [m["content"] for m in messages if m["role"] != "system"]
        prompt = "\n".join(user_msgs)
        
        llm = build_llm_provider_from_config(LLMConfig(backend="gemini", model=model))
        response_text, usage = await llm.generate_response(prompt=prompt, system_instruction=system_instruction)
        
        return LLMResponse(
            task_id="direct",
            status="completed",
            content=response_text,
            usage={"total_tokens": usage.total_tokens} if usage else {}
        )

    async def start_background_tasks(self) -> list[asyncio.Task]:
        """헬스 모니터와 스케줄러를 백그라운드 태스크로 시작합니다."""
        tasks = [
            asyncio.create_task(self._health.monitor_loop(), name="health_monitor"),
            asyncio.create_task(self.scheduler.run_loop(), name="scheduler"),
        ]
        logger.info("[CassiopeiaManager] 백그라운드 태스크 시작 (health_monitor, scheduler)")
        return tasks

    async def listen_tasks(self) -> None:
        """메인 루프: cassiopeia 및 orchestra Pub/Sub에서 작업을 수신합니다."""
        logger.info("[CassiopeiaManager] 메인 루프 시작")
        await self._cassiopeia.connect()

        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379").replace(
            "localhost", "127.0.0.1"
        )
        self._orchestra = CassiopeiaClient(agent_id="orchestra", redis_url=redis_url)
        await self._orchestra.connect()

        async def _listen_client(client: CassiopeiaClient, name: str):
            logger.info(f"[CassiopeiaManager] {name} 채널 리스너 시작")
            async for msg in client.listen():
                task: CassiopeiaTask = dict(msg.payload)
                action: str = getattr(msg, "action", "user_request")

                if action == "llm_call":
                    asyncio.create_task(self._route_message(action, task))
                    continue

                if action == "agent_result":
                    await self.receive_agent_result(task)
                    continue

                try:
                    verify_task(task)
                except DispatchAuthError as exc:
                    logger.error("[CassiopeiaManager] 서명 검증 실패 — DLQ로 이동: %s", exc)
                    await self._push_to_dlq("INVALID_SIGNATURE", task.get("task_id", "unknown"), {"message": str(exc)})
                    continue
                asyncio.create_task(self._safe_process_task(task))

        try:
            await asyncio.gather(
                _listen_client(self._cassiopeia, "cassiopeia"),
                _listen_client(self._orchestra, "orchestra")
            )
        except asyncio.CancelledError:
            pass
        finally:
            await self._cassiopeia.disconnect()
            await self._orchestra.disconnect()

    async def _route_message(self, action: str, payload: dict) -> None:
        if action == "llm_call":
            if self._llm_gateway is None: return
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
        """NLU → Dispatch → Monitor 파이프라인 (SDK v0.3.0 AgentBrain 적용)"""
        user_text = task.get("content", "")
        requester = task.get("requester", {})
        user_id = requester.get("user_id", "unknown")
        channel_id = requester.get("channel_id", "unknown")
        thread_id = task.get("thread_ts") or requester.get("thread_ts")
        session_id = thread_id if thread_id else task.get("session_id", str(uuid.uuid4()))
        task_id = task.get("task_id", str(uuid.uuid4()))

        await self._state.init_session(session_id, user_id, channel_id)
        await self._state.add_message(session_id, user_id, "user", user_text, provider="slack", thread_id=thread_id)
        await self._state.update_task_state(task_id, {"status": "PROCESSING", "session_id": session_id, "user_id": user_id})

        context = await self._state.build_context_for_llm(session_id, user_id)
        
        # 1. 활성 에이전트 목록을 도구(Tools)로 변환
        agents_health = await self._health.get_system_health()
        tools = []
        for agent_id, info in agents_health.items():
            if info["activity"] == "OFFLINE": continue

            reg_raw = await self._redis.hget("agents:registry", agent_id)
            reg_data = json.loads(reg_raw) if reg_raw else {}
            desc = reg_data.get("nlu_description") or reg_data.get("capabilities") or "전문 에이전트"

            # 파라미터 가이드는 에이전트가 선언한 params_schema 에서만 나온다.
            # (에이전트 이름에 따른 특수 분기 없음 — 완전 독립)
            tools.append(Tool(
                name=agent_id,
                description=desc,
                parameters=_tool_parameters_for(reg_data),
            ))

        # 2. SDK AgentBrain을 이용한 라우팅 결정
        decision = await self.brain.analyze_task(user_request=user_text, tools=tools, history=context)

        # 3. 분석 결과 처리
        if decision.action == "ask_clarification":
            await self._send_to_comm_agent(task, decision.suggested_reply or "구체적으로 말씀해 주세요.", False, "cassiopeia")
            return

        if decision.action == "direct_response":
            await self._send_to_comm_agent(task, decision.suggested_reply or "알겠습니다.", False, "cassiopeia")
            return

        selected_agent = decision.action
        agent_action = decision.params.get("action", "process_request")
        agent_params = decision.params.get("params", {})

        await self._route_single_with_decision(task, selected_agent, agent_action, agent_params, decision.reasoning, context)

    async def _route_single_with_decision(self, task, agent_name, action, params, reasoning, context=None):
        dispatch_task_id = str(uuid.uuid4())
        ready, reason = await self._health.is_agent_ready(agent_name)
        if not ready and not self._is_internal_tool(agent_name):
            await self._send_agent_unavailable_error(task, agent_name, reason)
            return

        reg_raw = await self._redis.hget("agents:registry", agent_name)
        reg_data = json.loads(reg_raw) if reg_raw else {}
        timeout = _resolve_timeout(agent_name, reg_data)
        # 중요 파괴적 작업은 승인 요구 (기존 로직 유지)
        needs_approval = _requires_approval(action, False)
        
        dispatch = _build_dispatch_message(
            dispatch_task_id, task["session_id"], agent_name, action,
            params, task["requester"], timeout, content=task.get("content", ""),
            context=context,
            requires_approval=needs_approval
        )

        if needs_approval:
            approval_msg = f"다음 작업을 실행하시겠습니까?\n- 에이전트: {agent_name}\n- 액션: {action}\n- 파라미터: {json.dumps(params, ensure_ascii=False)}"
            fake_result = {"agent": "cassiopeia", "result_data": {"summary": approval_msg}}
            if not await self.request_user_approval(fake_result, task): return

        logger.info(f"[CassiopeiaManager] 라우팅: {agent_name} -> {action} ({reasoning})")
        result = await self._execute_agent_task(agent_name, dispatch_task_id, dispatch, timeout)
        await self._handle_agent_result(result, task, False)

    async def wait_for_result(self, task_id: str, timeout: int = 600, agent_name: str | None = None) -> dict[str, Any]:
        key = f"{_RESULTS_KEY_PREFIX}{task_id}"
        remaining = timeout
        while remaining > 0:
            res = await self._redis.blpop(key, timeout=min(_BLPOP_TIMEOUT, remaining))
            if res: return json.loads(res[1])
            remaining -= _BLPOP_TIMEOUT
        failed = {"status": "FAILED", "task_id": task_id, "agent": agent_name, "error": {"code": "TIMEOUT", "message": "응답 없음", "traceback": None}}
        await self._push_to_dlq("timeout", task_id, failed["error"])
        return failed

    async def cancel_task(self, task_id: str, user_id: str) -> bool:
        state = await self._state.get_task_state(task_id)
        if not state or state.get("user_id") != user_id: return False
        await self._state.update_task_state(task_id, {"status": "CANCELLED"})
        await self.receive_agent_result({"task_id": task_id, "status": "FAILED", "agent": "cassiopeia", "result_data": {}, "error": {"code": "CANCELLED", "message": "취소됨"}})
        return True

    async def _push_to_dlq(self, reason: str, task_id: str, error: dict[str, Any]) -> None:
        entry = {"reason": reason, "task_id": task_id, "error": error, "ts": datetime.now(timezone.utc).isoformat()}
        try: await self._redis.rpush(_DLQ_KEY, json.dumps(entry, ensure_ascii=False))
        except: pass

    async def receive_agent_result(self, result: AgentResult) -> None:
        task_id = result["task_id"]
        await self._redis.rpush(f"{_RESULTS_KEY_PREFIX}{task_id}", json.dumps(result, ensure_ascii=False))

    async def _handle_agent_result(self, result: dict[str, Any], task: CassiopeiaTask, requires_approval: bool) -> None:
        agent_name = result.get("agent")
        if agent_name and not self._is_internal_tool(agent_name):
            if result.get("status") == "FAILED":
                await self._health.record_failure(agent_name)
            else:
                await self._health.record_success(agent_name)

        if result.get("status") == "FAILED":
            await self._send_error_to_user(task, result.get("error", {}).get("message", "오류"), result.get("agent"))
            return
        res_data = result.get("result_data", {})
        full_message = f"{res_data.get('summary', '작업 완료')}\n\n{res_data.get('content', '')}".strip()
        if requires_approval and not await self.request_user_approval(result, task): return
        await self._send_to_comm_agent(task, full_message, False, result.get("agent", "agent"))

    async def request_user_approval(self, result: dict[str, Any], task: CassiopeiaTask) -> bool:
        approval_id = str(uuid.uuid4())
        await self._redis.setex(f"slack:task:{approval_id}:context", 3600, json.dumps(task["requester"], ensure_ascii=False))
        msg: CommAgentMessage = {"task_id": approval_id, "content": f"승인 필요: {result.get('result_data', {}).get('summary')}", "requires_user_approval": True, "agent_name": result.get("agent")}
        source = task.get("source", "slack")
        await self._cassiopeia.send_message(action="request_approval", payload={**msg, "platform": source}, receiver=await self._get_comm_receiver(source))
        res = await self._redis.blpop(f"{_APPROVAL_KEY_PREFIX}{approval_id}", timeout=_APPROVAL_TIMEOUT_SEC)
        return json.loads(res[1]).get("action") == "approve" if res else False

    def _is_internal_tool(self, agent_name: str) -> bool:
        """인프로세스 내부 툴 여부를 레지스트리 멤버십으로 판단합니다 (이름 비교 없음)."""
        return agent_name in self._internal_tools

    async def _execute_agent_task(self, agent_name: str, task_id: str, dispatch: DispatchMessage, timeout: int) -> dict[str, Any]:
        handler = self._internal_tools.get(agent_name)
        if handler is not None:
            return await handler(task_id, dispatch)
        dispatch = await self._enrich_dispatch_with_secrets(agent_name, dispatch)
        signed_dispatch = sign_dispatch(dict(dispatch))
        await self._redis.hset(f"agent:{agent_name}:current_task", mapping={"task_id": dispatch["task_id"], "action": dispatch["action"], "started_at": datetime.now(timezone.utc).isoformat()})
        await self._cassiopeia.send_message(action=dispatch["action"], payload=signed_dispatch, receiver=agent_name)
        try: return await self.wait_for_result(task_id, timeout=timeout, agent_name=agent_name)
        finally: await self._redis.delete(f"agent:{agent_name}:current_task")

    async def _enrich_dispatch_with_secrets(self, agent_name: str, dispatch: DispatchMessage) -> DispatchMessage:
        secrets = await self._state.get_agent_secrets(agent_name)
        if secrets:
            new_dispatch = dict(dispatch)
            new_params = dict(new_dispatch.get("params", {}))
            new_params["credentials"] = secrets
            new_dispatch["params"] = new_params
            return new_dispatch # type: ignore
        return dispatch

    async def _run_cassiopeia_internal_task(self, task_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if action == "get_agent_list":
                agents = await self._health.get_available_agents()
                return {"task_id": task_id, "status": "COMPLETED", "agent": "cassiopeia_agent", "result_data": {"summary": "조회 완료", "content": f"연결된 에이전트: {', '.join(agents)}"}, "error": None}
            # ... (필요한 내부 액션들만 유지하거나 추가)
            raise ValueError(f"지원하지 않는 내부 액션: {action}")
        except Exception as exc:
            return {"task_id": task_id, "status": "FAILED", "agent": "cassiopeia_agent", "result_data": {}, "error": {"code": "ERROR", "message": str(exc)}}

    async def _run_sandbox_task(self, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._sandbox_tool is None: 
            return {"task_id": task_id, "status": "FAILED", "agent": "sandbox_agent", "error": {"code": "DISABLED", "message": "비활성"}}
        try:
            result = await self._sandbox_tool.execute_code(params)
            return {"task_id": task_id, "status": "COMPLETED", "agent": "sandbox_agent", "result_data": {**result, "summary": "실행 완료", "content": result.get("stdout", "")}, "error": None}
        except Exception as exc:
            return {"task_id": task_id, "status": "FAILED", "agent": "sandbox_agent", "result_data": {}, "error": {"code": "ERROR", "message": str(exc)}}

    async def _get_comm_receiver(self, source: str) -> str:
        """레지스트리의 routing 메타데이터로 source 플랫폼의 커뮤니케이션 수신자를 해석합니다."""
        registry_raw = await self._redis.hgetall("agents:registry")
        registry: dict[str, dict[str, Any]] = {}
        for name, data_raw in registry_raw.items():
            try:
                registry[name] = json.loads(data_raw)
            except (ValueError, TypeError):
                continue
        return _resolve_comm_receiver(source, registry)

    async def _send_to_comm_agent(self, task: CassiopeiaTask, content: str, requires_approval: bool, agent_name: str) -> None:
        req = task.get("requester", {})
        thread_id = task.get("thread_ts") or req.get("thread_ts")
        session_id = thread_id if thread_id else task.get("session_id")
        if session_id: await self._state.add_message(session_id, req.get("user_id", "unknown"), "assistant", content, provider="cassiopeia", thread_id=thread_id)
        msg: CommAgentMessage = {"task_id": task.get("task_id", str(uuid.uuid4())), "content": content, "requires_user_approval": requires_approval, "agent_name": agent_name}
        source = task.get("source", "slack")
        await self._cassiopeia.send_message(action="send_message", payload={**msg, "platform": source}, receiver=await self._get_comm_receiver(source))

    async def _send_error_to_user(self, task: CassiopeiaTask, error_message: str, agent_name: str = "cassiopeia") -> None:
        await self._send_to_comm_agent(task, f"[{agent_name}] 오류: {error_message}", False, agent_name)

    async def _send_agent_unavailable_error(self, task: CassiopeiaTask, agent_name: str, reason: str) -> None:
        await self._send_error_to_user(task, f"에이전트 {agent_name} 사용 불가 ({reason})")
