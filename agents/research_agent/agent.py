"""
Research Agent 구체 구현체
- 웹 검색 및 정보 수집
- 처리 결과를 HTTP POST /results 로 카시오페아에 전송
- SDK v0.3.0의 AgentBrain을 사용하여 지능형 검색 의도 분석을 수행합니다.
"""

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from .config import ResearchAgentConfig, load_config_from_env
from .providers import build_search_provider
from .pipeline import SearchExecutor, ReportSynthesizer
from shared_core.search.interfaces import SearchProviderProtocol
from shared_core.storage.sqlite_manager import SqliteStorageManager
from cassiopeia_sdk.brain import AgentBrain, AgentBrainConfig
from cassiopeia_sdk.tools import Tool

logger = logging.getLogger("research_agent.agent")


class ResearchAgent:
    agent_name: str = "research-agent"

    def __init__(self, config: ResearchAgentConfig | None = None, provider: SearchProviderProtocol | None = None, storage=None) -> None:
        self._config = config or load_config_from_env()

        if provider is None:
            primary_provider = build_search_provider(
                provider_name=self._config.search_provider,
                api_key=self._config.search_api_key,
                gemini_model=self._config.gemini_model,
                perplexity_model=self._config.perplexity_model,
            )

            if self._config.fallback_provider:
                from .providers import FallbackSearchProvider
                secondary_provider = build_search_provider(
                    provider_name=self._config.fallback_provider,
                    api_key=self._config.fallback_api_key,
                    gemini_model=self._config.gemini_model,
                    perplexity_model=self._config.perplexity_model,
                )
                provider = FallbackSearchProvider(primary_provider, secondary_provider)
            else:
                provider = primary_provider

        self._provider = provider

        # SDK AgentBrain 초기화
        self.brain = AgentBrain(
            agent_name=self.agent_name,
            capabilities="""당신은 웹 리서치 전문가입니다. 
사용자의 질문을 분석하여 웹 검색 엔진에 최적화된 다수의 검색 쿼리를 생성하고, 
검색 결과를 종합하여 전문적인 보고서를 작성합니다.""",
            backend="gateway",
            llm_caller=self._direct_llm_caller,
            config=AgentBrainConfig(max_retries=2)
        )

        logger.info("[ResearchAgent] SDK AgentBrain 기반 초기화 완료")

        # Initialize Pipeline Components
        self._search_executor = SearchExecutor(provider=self._provider)
        # ReportSynthesizer는 직접 LLM을 사용하므로, 동일한 방식으로 우회 지원
        from shared_core.llm.factory import build_llm_provider_from_config
        from shared_core.llm.llm_config import LLMConfig
        self._report_synthesizer = ReportSynthesizer(llm=build_llm_provider_from_config(LLMConfig(backend="gemini")))

        # Initialize Storage
        self._storage = storage or SqliteStorageManager()

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

    async def investigate(self, query: str, dispatch_msg: dict | None = None) -> str:
        try:
            # 1. SDK AgentBrain을 사용하여 검색 키워드(쿼리) 추출
            # 검색 에이전트는 'search'라는 도구 하나를 전문적으로 사용한다고 가정
            search_tool = Tool(
                name="search", 
                description="웹 검색을 수행하기 위한 최적의 키워드 리스트를 생성합니다.",
                parameters={"queries": "웹 검색 엔진에 입력할 구체적인 검색어 리스트 (List of strings, 예: ['삼성전자 주가 전망', '반도체 시장 현황'])"}
            )

            decision = await self.brain.analyze_task(
                user_request=query,
                tools=[search_tool],
                history=dispatch_msg.get("context", []) if dispatch_msg else []
            )

            if decision.action == "ask_clarification":
                return decision.suggested_reply or "요청이 모호합니다. 좀 더 자세히 말씀해 주세요."

            # 추출된 쿼리 목록 (없으면 원본 쿼리 사용)
            queries = decision.params.get("queries")
            if not queries or not isinstance(queries, list):
                queries = [query]

            logger.info(f"[ResearchAgent] 생성된 검색 쿼리: {queries}")

            # 2. 검색 실행
            results = await self._search_executor.execute(queries)
            
            # 3. 결과 요약 (보고서 생성)
            report, citations = await self._report_synthesizer.synthesize(query, results)

            final_report = report
            if citations:
                final_report += "\n\n### 출처\n" + "\n".join(f"- {c}" for c in citations)
            return final_report

        except Exception as e:
            logger.error(f"[ResearchAgent] 조사 중 오류 발생: {e}")
            return f"조사 과정에서 오류가 발생했습니다: {e}"

    async def _dispatch(self, action: str, payload: dict, dispatch_msg: dict | None = None) -> dict[str, Any]:
        query = payload.get("query") or payload.get("topic") or payload.get("keyword") or str(payload)
        result_text = await self.investigate(query, dispatch_msg=dispatch_msg)
        return {"status": "success", "data": result_text}

    async def _report_result(
        self,
        cassiopeia_url: str,
        task_id: str,
        status: str,
        result_data: dict[str, Any],
        error: dict[str, Any] | None,
        reference_id: str | None = None,
        payload_summary: str | None = None,
    ) -> None:
        """처리 결과를 카시오페아 /results 엔드포인트로 전송합니다. 최대 3회 재시도."""
        payload = {
            "task_id": task_id,
            "agent": self.agent_name,
            "status": status,
            "result_data": result_data,
            "error": error,
            "usage_stats": {},
        }
        if reference_id:
            payload["reference_id"] = reference_id
        if payload_summary:
            payload["payload_summary"] = payload_summary

        url = f"{cassiopeia_url}/results"
        headers = {}
        
        # 환경변수 또는 설정에서 인증 키 로드 (따옴표 제거 필수)
        api_key = self._config.cassiopeia_api_key or os.environ.get("ADMIN_API_KEY") or os.environ.get("CLIENT_API_KEY", "")
        api_key = api_key.strip("\"'")
        if api_key:
            headers["X-API-Key"] = api_key

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                logger.info("[ResearchAgent] 결과 보고 완료: task_id=%s status=%s", task_id, status)
                return
            except Exception as exc:
                wait = 2 ** attempt
                logger.warning("[ResearchAgent] 결과 보고 실패 (attempt %d/3): %s — %ds 후 재시도", attempt + 1, exc, wait)
                if attempt < 2:
                    await asyncio.sleep(wait)
        logger.error("[ResearchAgent] 결과 보고 최종 실패: task_id=%s", task_id)

    async def _handle_task(self, raw: str, cassiopeia_url: str) -> None:
        """BLPOP으로 수신한 DispatchMessage를 처리하고 결과를 카시오페아로 전송합니다."""
        task_id = "unknown"
        agent_result: dict[str, Any] = {
            "status": "FAILED",
            "result_data": {},
            "error": {"code": "INTERNAL_ERROR", "message": "처리 중 알 수 없는 오류", "traceback": None},
        }
        try:
            dispatch_msg: dict[str, Any] = json.loads(raw)
            task_id = dispatch_msg.get("task_id", "unknown")
            action = dispatch_msg.get("action", "")
            params = dispatch_msg.get("params", {})
            logger.info("[ResearchAgent] 태스크 수신: task_id=%s action=%s", task_id, action)

            result = await self._dispatch(action, params, dispatch_msg=dispatch_msg)

            if result.get("status") == "error":
                agent_result = {
                    "status": "FAILED",
                    "result_data": {},
                    "error": {"code": "EXECUTION_ERROR", "message": result.get("message", "실행 오류"), "traceback": None},
                }
            else:
                raw_text = result.get("data", "")
                ref_id = await self._storage.save_data(
                    data={"raw_text": raw_text},
                    metadata={"action": action, "task_id": task_id}
                )
                summary = f"{action} 완료 (길이: {len(raw_text)}자)"

                agent_result = {
                    "status": "COMPLETED",
                    "result_data": {
                        "summary": summary,
                        "content": raw_text
                    },
                    "reference_id": ref_id,
                    "payload_summary": summary,
                    "error": None,
                }

        except asyncio.CancelledError:
            logger.warning("[ResearchAgent] 태스크 취소됨: task_id=%s", task_id)
            agent_result["error"] = {"code": "CANCELLED", "message": "태스크가 취소되었습니다.", "traceback": None}
            raise
        except Exception as exc:
            logger.error("[ResearchAgent] 태스크 처리 실패 task_id=%s: %s", task_id, exc)
            agent_result["error"] = {"code": "INTERNAL_ERROR", "message": str(exc), "traceback": None}
        finally:
            try:
                await self._report_result(
                    cassiopeia_url=cassiopeia_url,
                    task_id=task_id,
                    status=agent_result.get("status", "FAILED"),
                    result_data=agent_result.get("result_data", {}),
                    error=agent_result.get("error"),
                    reference_id=agent_result.get("reference_id"),
                    payload_summary=agent_result.get("payload_summary"),
                )
            except Exception as exc:
                logger.error("[ResearchAgent] 결과 보고 실패 task_id=%s: %s", task_id, exc)

