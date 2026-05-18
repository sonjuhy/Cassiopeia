"""
File Agent 구체 구현체
- FileAgentProtocol 구현: read / write / update / delete
- cassiopeia-sdk CassiopeiaClient.listen()으로 카시오페아 디스패치 수신
- 처리 결과를 HTTP POST /results 로 카시오페아에 전송
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as aioredis
from cassiopeia_sdk.client import AgentMessage as SdkAgentMessage, CassiopeiaClient
from cassiopeia_sdk.brain import AgentBrain, AgentBrainConfig
from cassiopeia_sdk.tools import Tool

from .config import FileAgentConfig, load_config_from_env
from .interfaces import FileOperationResult
from .validator import PathValidator, PathValidatorProtocol

logger = logging.getLogger("file_agent.agent")

_HEARTBEAT_INTERVAL: int = int(os.environ.get("HEARTBEAT_INTERVAL", "15"))
_HTTP_REPORT_TIMEOUT: float = float(os.environ.get("HTTP_REPORT_TIMEOUT", "10.0"))
_DISPATCH_TIMEOUT: float = float(os.environ.get("DISPATCH_TIMEOUT", "60.0"))
_DLQ_KEY = "cassiopeia:dlq"


class FileAgent:
    """
    FileAgentProtocol의 구체 구현체.
    cassiopeia-sdk를 사용해 카시오페아로부터 태스크 메시지를 수신합니다.
    """

    agent_name: str = "file-agent"

    def __init__(
        self,
        config: FileAgentConfig | None = None,
        validator: PathValidatorProtocol | None = None,
    ) -> None:
        self._config = config or load_config_from_env()
        self._validator = validator or PathValidator()
        
        # SDK AgentBrain 초기화
        self.brain = AgentBrain(
            agent_name=self.agent_name,
            capabilities="""당신은 파일 시스템 관리 전문가입니다. 
로컬 파일의 읽기, 쓰기, 업데이트, 삭제 작업을 수행합니다. 
보안을 위해 허용된 경로 내에서만 작업하며, 파일 경로와 내용을 정확히 추출합니다.""",
            backend="gateway",
            llm_caller=self._direct_llm_caller,
            config=AgentBrainConfig(max_retries=2)
        )

    async def _direct_llm_caller(self, messages: list[dict], max_tokens: int = 500, temperature: float = 0.7, model: str | None = None, **kwargs) -> Any:
        from shared_core.llm.factory import build_llm_provider_from_config
        from shared_core.llm.llm_config import LLMConfig
        from cassiopeia_sdk.brain._models import LLMResponse
        
        system_msgs = [m["content"] for m in messages if m["role"] == "system"]
        system_instruction = "\n".join(system_msgs) if system_msgs else None
        
        user_msgs = [m["content"] for m in messages if m["role"] != "system"]
        prompt = "\n".join(user_msgs)
        
        llm = build_llm_provider_from_config(LLMConfig(backend="gemini", model=model))
        response_text, usage = await llm.generate_response(prompt=prompt, system_instruction=system_instruction)
        
        return LLMResponse(task_id="direct", status="completed", content=response_text, usage={"total_tokens": usage.total_tokens} if usage else {})

    async def _dispatch(self, action: str, payload: dict, user_text: str = "", history: list = None) -> FileOperationResult:
        """AgentBrain을 통해 의도를 정제한 후 실제 작업을 수행합니다."""
        
        tools = [
            Tool(name="read_file", description="파일 내용을 읽습니다.", parameters={"file_path": "대상 파일 경로"}),
            Tool(name="write_file", description="새 파일을 작성하거나 덮어씁니다.", parameters={"file_path": "대상 파일 경로", "content": "파일 내용", "overwrite": "덮어쓰기 여부 (bool)"}),
            Tool(name="update_file", description="기존 파일 내용을 수정합니다.", parameters={"file_path": "대상 파일 경로", "content": "추가/변경할 내용", "append": "이어쓰기 여부 (bool)"}),
            Tool(name="delete_file", description="파일을 삭제합니다.", parameters={"file_path": "대상 파일 경로"})
        ]

        try:
            decision = await self.brain.analyze_task(
                user_request=user_text or str(payload),
                tools=tools,
                history=history or []
            )

            if decision.action == "ask_clarification":
                return FileOperationResult(status="error", message=decision.suggested_reply or "추가 정보가 필요합니다.")

            final_action = decision.action
            final_params = decision.params

            match final_action:
                case "read_file": return await self.read_file(final_params["file_path"])
                case "write_file": return await self.write_file(final_params["file_path"], final_params["content"], final_params.get("overwrite", False))
                case "update_file": return await self.update_file(final_params["file_path"], final_params["content"], final_params.get("append", True))
                case "delete_file": return await self.delete_file(final_params["file_path"])
                case _: return FileOperationResult(status="error", message=f"지원하지 않는 작업: {final_action}")

        except Exception as e:
            logger.warning(f"[FileAgent] Brain 분석 실패, 기존 action 기반 실행 시도: {e}")
            match action:
                case "read_file": return await self.read_file(payload["file_path"])
                case "write_file": return await self.write_file(payload["file_path"], payload["content"], payload.get("overwrite", False))
                case "update_file": return await self.update_file(payload["file_path"], payload["content"], payload.get("append", True))
                case "delete_file": return await self.delete_file(payload["file_path"])
                case _: return FileOperationResult(status="error", message=f"알 수 없는 액션: {action}")

    async def _report_result(
        self,
        cassiopeia_url: str,
        task_id: str,
        status: str,
        result_data: dict[str, Any],
        error: dict[str, Any] | None,
        redis: aioredis.Redis | None = None,
    ) -> None:
        """처리 결과를 카시오페아 /results 엔드포인트로 전송합니다. 최대 3회 재시도 후 DLQ 저장."""
        payload = {
            "task_id": task_id,
            "agent": self.agent_name,
            "status": status,
            "result_data": result_data,
            "error": error,
            "usage_stats": {},
        }
        url = f"{cassiopeia_url}/results"
        
        # 환경변수에서 인증 키 로드 (따옴표 제거 필수)
        api_key = (os.environ.get("ADMIN_API_KEY") or os.environ.get("CLIENT_API_KEY", "")).strip("\"'")
        headers = {"X-API-Key": api_key}

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=_HTTP_REPORT_TIMEOUT) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                logger.info("[FileAgent] 결과 보고 완료: task_id=%s status=%s", task_id, status)
                return
            except Exception as exc:
                wait = 2 ** attempt
                logger.warning("[FileAgent] 결과 보고 실패 (attempt %d/3): %s — %ds 후 재시도", attempt + 1, exc, wait)
                if attempt < 2:
                    await asyncio.sleep(wait)
        logger.error("[FileAgent] 결과 보고 최종 실패: task_id=%s", task_id)
        if redis:
            try:
                dlq_entry = {**payload, "failed_at": datetime.now(timezone.utc).isoformat(), "reason": "http_report_failed"}
                await redis.rpush(_DLQ_KEY, json.dumps(dlq_entry, ensure_ascii=False))
                logger.warning("[FileAgent] 결과 DLQ 저장: task_id=%s", task_id)
            except Exception as dlq_exc:
                logger.error("[FileAgent] DLQ 저장 실패: %s", dlq_exc)

    async def _handle_task(
        self,
        msg: SdkAgentMessage,
        cassiopeia_url: str,
        redis: aioredis.Redis | None = None,
    ) -> None:
        """cassiopeia AgentMessage를 처리하고 결과를 카시오페아로 전송합니다.

        payload 구조:
            {
                "task_id": "...",
                "params": { ... }  # 각 액션에 필요한 파라미터
            }
        """
        task_id = msg.payload.get("task_id", "unknown")
        agent_result: dict[str, Any] = {
            "status": "FAILED",
            "result_data": {},
            "error": {"code": "INTERNAL_ERROR", "message": "처리 중 알 수 없는 오류", "traceback": None},
        }
        try:
            action = msg.action
            params = msg.payload.get("params", {})
            logger.info("[FileAgent] 태스크 수신: task_id=%s action=%s", task_id, action)

            # SDK v0.3.0 리팩토링: content와 history를 함께 전달하여 Brain 분석 유도
            try:
                op_result = await asyncio.wait_for(
                    self._dispatch(
                        action=action, 
                        payload=params, 
                        user_text=msg.payload.get("content", ""), 
                        history=msg.payload.get("context", [])
                    ),
                    timeout=_DISPATCH_TIMEOUT
                )
            except (asyncio.TimeoutError, TimeoutError):
                logger.error("[FileAgent] 태스크 처리 시간 초과 task_id=%s", task_id)
                op_result = FileOperationResult(status="error", message=f"태스크 처리가 시간 초과되었습니다 ({_DISPATCH_TIMEOUT}초).")

            if op_result.status == "error":
                agent_result = {
                    "status": "FAILED",
                    "result_data": {},
                    "error": {"code": "EXECUTION_ERROR", "message": op_result.message, "traceback": None},
                }
            else:
                agent_result = {
                    "status": "COMPLETED",
                    "result_data": {
                        "summary": op_result.message,
                        "raw_text": op_result.data or "",
                    },
                    "error": None,
                }

        except asyncio.CancelledError:
            logger.warning("[FileAgent] 태스크 취소됨: task_id=%s", task_id)
            agent_result["error"] = {"code": "CANCELLED", "message": "태스크가 취소되었습니다.", "traceback": None}
            raise
        except Exception as exc:
            logger.error("[FileAgent] 태스크 처리 실패 task_id=%s: %s", task_id, exc)
            agent_result["error"] = {"code": "INTERNAL_ERROR", "message": str(exc), "traceback": None}
        finally:
            try:
                await self._report_result(
                    cassiopeia_url=cassiopeia_url,
                    task_id=task_id,
                    status=agent_result.get("status", "FAILED"),
                    result_data=agent_result.get("result_data", {}),
                    error=agent_result.get("error"),
                    redis=redis,
                )
            except Exception as exc:
                logger.error("[FileAgent] 결과 보고 실패 task_id=%s: %s", task_id, exc)

    async def run(self) -> None:
        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
        if "localhost" in redis_url:
            redis_url = redis_url.replace("localhost", "127.0.0.1")
        cassiopeia_url = os.environ.get("CASSIOPEIA_URL", "http://cassiopeia-agent:8001")
        health_key = f"agent:{self.agent_name}:health"

        import re
        safe_redis_url = re.sub(r":([^:@]+)@", ":***MASKED***@", redis_url)
        logger.info("[FileAgent] 실행 시작 (Redis: %s, agent: %s)", safe_redis_url, self.agent_name)

        # 하트비트와 DLQ는 직접 Redis 클라이언트 사용
        redis = aioredis.from_url(redis_url, decode_responses=True)

        # 메시지 수신은 cassiopeia-sdk 사용
        cassiopeia = CassiopeiaClient(agent_id=self.agent_name, redis_url=redis_url)
        await cassiopeia.connect()

        nlu_desc = (
            "- file-agent: 로컬 파일 시스템의 읽기, 쓰기, 업데이트, 삭제 작업을 수행하는 파일 관리 전용 에이전트입니다. "
            "보안을 위해 허용된 경로 내에서만 작업하며, 파일의 내용을 조회하거나 수정할 때 사용합니다. "
            "(actions: read_file, write_file, update_file, delete_file)"
        )

        async def heartbeat_loop():
            while True:
                try:
                    # 1. 헬스 상태 업데이트
                    await redis.hset(health_key, mapping={
                        "status": "IDLE",
                        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                        "version": "1.0.0"
                    })
                    await redis.expire(health_key, 60)

                    # 2. 중앙 레지스트리에 능력치 등록 (동적 라우팅용)
                    await redis.hset("agents:registry", self.agent_name, json.dumps({
                        "name": self.agent_name,
                        "lifecycle_type": "long_running",
                        "nlu_description": nlu_desc,
                        "capabilities": ["file", "filesystem"],
                        "registered_at": datetime.now(timezone.utc).isoformat(),
                    }, ensure_ascii=False))

                    await asyncio.sleep(_HEARTBEAT_INTERVAL)
                except asyncio.CancelledError:
                    break
                except Exception:
                    await asyncio.sleep(5)

        hb_task = asyncio.create_task(heartbeat_loop())

        try:
            async for msg in cassiopeia.listen():
                asyncio.create_task(self._handle_task(msg, cassiopeia_url, redis))
        except asyncio.CancelledError:
            logger.info("[FileAgent] 종료")
        finally:
            hb_task.cancel()
            await cassiopeia.disconnect()
            await redis.aclose()
            logger.info("[FileAgent] 실행 종료")
