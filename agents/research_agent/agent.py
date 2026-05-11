"""
Research Agent 구체 구현체
- 웹 검색 및 정보 수집
- 처리 결과를 HTTP POST /results 로 카시오페아에 전송
- 태스크 수신은 ResearchCassiopeiaListener (cassiopeia-sdk Pub/Sub)가 담당
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from .config import ResearchAgentConfig, load_config_from_env
from .providers import build_search_provider
from .pipeline import IntentAnalyzer, SearchExecutor, ReportSynthesizer
from shared_core.search.interfaces import SearchProviderProtocol
from shared_core.llm.factory import build_llm_provider_from_config
from shared_core.llm.llm_config import LLMConfig, load_llm_config_for_agent, llm_config_from_dispatch
from shared_core.storage.sqlite_manager import SqliteStorageManager

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

        # 에이전트별 LLM 설정 (환경변수 RESEARCH_AGENT_LLM_BACKEND 우선)
        self._llm_config: LLMConfig = load_llm_config_for_agent(self.agent_name)
        llm = build_llm_provider_from_config(self._llm_config)

        logger.info(
            "[ResearchAgent] 초기화 완료 (LLM backend=%s, model=%s)",
            self._llm_config.backend,
            self._llm_config.model,
        )

        # Initialize Pipeline Components
        self._intent_analyzer = IntentAnalyzer(llm=llm)
        self._search_executor = SearchExecutor(provider=self._provider)
        self._report_synthesizer = ReportSynthesizer(llm=llm)

        # Initialize Storage
        self._storage = storage or SqliteStorageManager()

    def _build_pipeline_for_dispatch(self, dispatch_msg: dict) -> tuple[IntentAnalyzer, ReportSynthesizer]:
        """dispatch별 per-call llm_config가 있으면 해당 LLM으로 파이프라인 컴포넌트를 생성합니다."""
        per_call = llm_config_from_dispatch(dispatch_msg)
        if per_call is None:
            return self._intent_analyzer, self._report_synthesizer

        logger.info(
            "[ResearchAgent] per-call LLM 설정 적용 (backend=%s, model=%s)",
            per_call.backend,
            per_call.model,
        )
        llm = build_llm_provider_from_config(per_call)
        return IntentAnalyzer(llm=llm), ReportSynthesizer(llm=llm)

    async def investigate(self, query: str, dispatch_msg: dict | None = None) -> str:
        intent_analyzer, report_synthesizer = (
            self._build_pipeline_for_dispatch(dispatch_msg)
            if dispatch_msg is not None
            else (self._intent_analyzer, self._report_synthesizer)
        )
        try:
            queries = await intent_analyzer.analyze(query)
            results = await self._search_executor.execute(queries)
            report, citations = await report_synthesizer.synthesize(query, results)

            final_report = report
            if citations:
                final_report += "\n\n### 출처\n" + "\n".join(f"- {c}" for c in citations)
            return final_report
        except Exception as e:
            return f"검색 중 오류 발생: {e}"

    async def _dispatch(self, action: str, payload: dict, dispatch_msg: dict | None = None) -> dict[str, Any]:
        # NLU가 임의의 액션명(search_stock_market 등)이나 파라미터(topic, query 등)를 생성할 수 있으므로 유연하게 처리
        query = payload.get("query") or payload.get("topic") or payload.get("keyword") or str(payload)
        
        # 조사 에이전트는 본질적으로 검색/조사 역할 하나만 수행하므로 액션명에 구애받지 않고 investigate를 실행
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
        if self._config.cassiopeia_api_key:
            headers["X-API-Key"] = self._config.cassiopeia_api_key

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

