"""
Unified Archive Agent
- 사용자의 요청을 분석하여 Notion 또는 Obsidian으로 작업을 라우팅합니다.
- SDK v0.3.0의 AgentBrain을 사용하여 지능형 라우팅을 수행합니다.
"""

import logging
from typing import Any

from cassiopeia_sdk.brain import AgentBrain, AgentBrainConfig
from cassiopeia_sdk.tools import Tool

from .notion.agent import ArchiveAgent
from .obsidian.agent import ObsidianAgent

logger = logging.getLogger("archive_agent.unified_agent")

class UnifiedArchiveAgent:
    agent_name: str = "archive_agent"

    def __init__(self) -> None:
        self.notion_agent = ArchiveAgent()
        self.obsidian_agent = ObsidianAgent()
        
        # SDK AgentBrain 초기화
        # 통합 아카이브 에이전트는 Notion과 Obsidian을 아우르는 관제 역할을 수행합니다.
        self.brain = AgentBrain(
            agent_name=self.agent_name,
            capabilities="""당신은 문서 아카이빙 전문가입니다. 
사용자의 요청을 분석하여 정보를 Notion(클라우드 DB)에 저장/조회할지, 
또는 Obsidian(로컬 마크다운 파일)에 저장/조회할지 결정하고 적절한 파라미터를 추출합니다.

[주의사항]
1. 절대로 존재하지 않는 ID(UUID 형식)를 지어내거나 추측하지 마십시오.
2. 사용자가 문서의 제목이나 데이터베이스 이름을 말했지만, 이전 대화 맥락에서 해당 ID를 찾을 수 없는 경우에는 'database_id'나 'page_id'를 비워두고 'query' 필드에 해당 이름을 넣으십시오.
3. 하위 에이전트가 'query'에 담긴 이름을 바탕으로 실제 ID를 찾아낼 것입니다.""",
            backend="gateway",
            llm_caller=self._direct_llm_caller,
            config=AgentBrainConfig(max_retries=2, confidence_threshold=0.7)
        )
        
        logger.info("[UnifiedArchiveAgent] AgentBrain 기반 지능형 라우터 초기화 완료")

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

    async def handle_dispatch(self, dispatch_msg: dict[str, Any]) -> dict[str, Any]:
        task_id = dispatch_msg.get("task_id", "unknown")
        user_text = str(dispatch_msg.get("content") or "").strip()
        params = dispatch_msg.get("params") or {}

        # 완전 빈 요청에 대한 예외 처리
        if not user_text and not params:
            return {
                "task_id": task_id,
                "status": "FAILED",
                "result_data": {},
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "요청 내용이 비어있습니다.",
                    "traceback": ""
                }
            }

        # 1. SDK AgentBrain을 이용한 의도 분석 및 라우팅
        # Notion과 Obsidian의 도구 세트를 정의하여 LLM이 선택하게 함
        tools = [
            Tool(name="notion_task", description="Notion(클라우드 데이터베이스) 관련 작업 수행", 
                 parameters={
                     "action": "search, get_page, query_database, create_page, list_databases, search_objects 중 하나",
                     "query": "검색어 또는 통합 검색 키워드",
                     "title": "문서 제목 (생성인 경우)",
                     "content": "문서 내용 (생성인 경우)",
                     "database_id": "데이터베이스 ID (알고 있는 경우)"
                 }),
            Tool(name="obsidian_task", description="Obsidian(로컬 마크다운 파일) 관련 작업 수행", 
                 parameters={
                     "action": "read_file, write_file, append_file, list_files 중 하나",
                     "title": "파일명 또는 제목",
                     "content": "파일 내용",
                     "query": "검색어"
                 })
        ]

        try:
            decision = await self.brain.analyze_task(
                user_request=user_text,
                tools=tools,
                history=dispatch_msg.get("context", [])
            )

            if decision.action == "ask_clarification":
                return {
                    "task_id": task_id,
                    "status": "COMPLETED",
                    "result_data": {
                        "summary": "추가 정보가 필요합니다.",
                        "content": decision.suggested_reply
                    }
                }

            # 분석 결과 적용
            target = "notion" if decision.action == "notion_task" else "obsidian"
            
            # 파라미터 병합 (기존에 유효한 ID가 있으면 보호하여 할루시네이션 방어)
            from .notion.agent import is_uuid
            extracted_params = decision.params
            for k, v in extracted_params.items():
                # 기존에 이미 유효한 UUID 형태의 ID가 있다면 LLM이 새로 추출한 값으로 덮어쓰지 않음
                if k in ["database_id", "page_id"] and is_uuid(str(params.get(k, ""))):
                    continue
                if v: # 유효한 값만 업데이트
                    params[k] = v
            
            dispatch_msg["params"] = params
            
            # 개별 에이전트가 기대하는 'action' 필드 설정
            if "action" in extracted_params:
                dispatch_msg["action"] = extracted_params["action"]
            elif "action" in params:
                dispatch_msg["action"] = params["action"]

            logger.info(f"[UnifiedArchiveAgent] 라우팅 결정: {target} (action: {dispatch_msg.get('action')}, reason: {decision.reasoning})")

            # 2. 결정된 에이전트에게 위임
            if target == "obsidian":
                return await self.obsidian_agent.handle_dispatch(dispatch_msg)
            else:
                return await self.notion_agent.handle_dispatch(dispatch_msg)

        except Exception as e:
            logger.error(f"[UnifiedArchiveAgent] NLU 분석 중 오류 발생: {e}")
            # 룰백: 간단한 키워드 기반 매칭
            if any(kw in user_text.lower() for kw in ["옵시디언", "obsidian", "로컬", "파일", "메모장", ".md"]):
                return await self.obsidian_agent.handle_dispatch(dispatch_msg)
            return await self.notion_agent.handle_dispatch(dispatch_msg)
