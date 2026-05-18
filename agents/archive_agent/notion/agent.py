"""
Archive Agent (Notion 구현체) - Autonomous Edition
- 상황에 따라 스스로 도구를 선택하고 자가 치유(Self-healing)를 수행하는 자율형 에이전트
"""

import os
import traceback
import json
import re
from typing import Any

import httpx

from ..models import (
    ArchiveTaskParams,
    ArchiveTaskResult,
    ExecutionResult,
    ParsedTask,
    RawPayload,
)
from .notion_parser import parse_notion_task
from shared_core.agent_logger import AgentLogger
from shared_core.storage.sqlite_manager import SqliteStorageManager
from cassiopeia_sdk.brain import AgentBrain, AgentBrainConfig

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# UUID 형식 정규식 (Notion ID 검증용)
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.I)

def is_uuid(val: str) -> bool:
    """문자열이 유효한 UUID 형식인지 확인합니다."""
    return bool(UUID_PATTERN.match(val))

class ArchiveAgent:
    agent_name: str = "archive_agent"

    def __init__(self, storage = None) -> None:
        self._token: str = os.environ.get("NOTION_TOKEN", "")
        self._database_id: str = os.environ.get("NOTION_DATABASE_ID", "")
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        
        # SDK AgentBrain 초기화
        # 기획서 작성 및 태스크 분석용 브레인
        self.brain = AgentBrain(
            agent_name=f"{self.agent_name}_brain",
            capabilities="""당신은 소프트웨어 기획 전문가입니다. 
주어진 태스크를 분석하여 목표, 과정, 기능을 포함하는 상세한 마크다운 기획 문서를 작성합니다.""",
            backend="gateway",
            llm_caller=self._direct_llm_caller,
            config=AgentBrainConfig(max_retries=2)
        )
            
        self.logger = AgentLogger(self.agent_name)
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

    # ── 기본 도구 (Notion API Wrappers) ──────────────────────────────────────────

    async def fetch_page(self, page_id: str) -> dict[str, Any]:
        url = f"{NOTION_API_BASE}/pages/{page_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json()

    async def query_database(self, database_id: str, query_filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        url = f"{NOTION_API_BASE}/databases/{database_id}/query"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers, json=query_filter or {})
            resp.raise_for_status()
            return resp.json().get("results", [])

    async def search_notion(self, query: str = "", filter_obj: dict[str, str] | None = None) -> list[dict[str, Any]]:
        url = f"{NOTION_API_BASE}/search"
        body = {"query": query}
        if filter_obj: body["filter"] = filter_obj
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers, json=body)
            resp.raise_for_status()
            return resp.json().get("results", [])

    async def create_page(self, parent_id: str, title: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        """지정한 데이터베이스 아래에 새 페이지를 생성합니다. 제목 필드 이름을 자동 감지합니다."""
        url = f"{NOTION_API_BASE}/pages"
        
        # 제목 필드 이름 찾기 (기본값 "제목" 또는 "Name" 등)
        title_prop = "title"
        try:
            db_url = f"{NOTION_API_BASE}/databases/{parent_id}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(db_url, headers=self._headers)
                if resp.status_code == 200:
                    db_data = resp.json()
                    for p_name, p_info in db_data.get("properties", {}).items():
                        if p_info["type"] == "title":
                            title_prop = p_name
                            break
        except Exception:
            pass # 실패 시 기본값 "title" 사용

        payload = {
            "parent": {"database_id": parent_id},
            "properties": properties or {
                title_prop: {"title": [{"text": {"content": title}}]}
            }
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    # ── 자율 처리 로직 (Autonomous Logic) ─────────────────────────────────────────

    async def handle_dispatch(self, dispatch_msg: dict[str, Any]) -> dict[str, Any]:
        task_id = dispatch_msg.get("task_id", "unknown")
        # params가 None으로 넘어올 경우를 대비해 {}로 강제 초기화
        params = dispatch_msg.get("params") or {}
        # action은 DispatchMessage 최상위 레벨에서 읽고, 없을 때만 params 내부를 fallback으로 사용
        action = dispatch_msg.get("action") or params.get("action", "get_page")
        # user_text가 None일 경우 빈 문자열로 처리
        user_text = str(dispatch_msg.get("content") or "")

        try:
            res_data: ArchiveTaskResult = {
                "status": "success", "source": "notion", "action": action,
                "raw_data": None, "content": None, "summary": "", "metadata": {},
            }

            # [Discovery Actions] 검색/조회용 액션은 ID 보정 생략
            if action == "list_databases":
                db_list = await self.search_notion(filter_obj={"property": "object", "value": "database"})
                res_data["raw_data"] = {"databases": db_list}
                res_data["summary"] = f"연결된 {len(db_list)}개의 데이터베이스 목록을 확인했습니다."
                return await self._finalize_result(task_id, action, res_data)

            elif action == "search_objects":
                query = params.get("query") or user_text
                search_results = await self.search_notion(query=query)
                res_data["raw_data"] = {"search_results": search_results}
                res_data["summary"] = f"'{query}' 검색 결과 {len(search_results)}개의 항목을 발견했습니다."
                return await self._finalize_result(task_id, action, res_data)

            # [ID 유효성 검사 및 보정]
            target_id = params.get("page_id") or params.get("database_id")
            
            # 1. ID가 제목(비-UUID)으로 들어온 경우 검색 시도
            if target_id and not is_uuid(target_id):
                await self.logger.log_action("reasoning", f"제목 형태의 ID 감지, 검색 수행: {target_id}", task_id=task_id)
                search_res = await self.search_notion(query=target_id)
                if search_res:
                    best_match = search_res[0]
                    target_id = best_match["id"]
                    obj_type = best_match["object"]
                    params["page_id" if obj_type == "page" else "database_id"] = target_id
                    await self.logger.log_action("fallback", f"제목 '{params.get('page_id') or params.get('database_id')}'를 ID '{target_id}'로 변환", task_id=task_id)
                else:
                    # 검색 결과가 없는데 '저장' 요청인 경우 생성을 고려
                    combined_text = (user_text + " " + str(params.get("content", "")) + " " + str(params.get("text", ""))).lower()
                    if "저장" in combined_text or "생성" in combined_text or "write" in combined_text or action == "create_page":
                        action = "create_page"
                        await self.logger.log_action("fallback", f"대상을 찾을 수 없어 생성을 시도합니다.", task_id=task_id)
                    else:
                        raise ValueError(f"'{target_id}'라는 제목의 페이지나 데이터베이스를 찾을 수 없습니다.")

            # 2. 특정 ID가 아예 없는 경우 -> 사용자 질문으로 검색 시도
            if not params.get("page_id") and not params.get("database_id"):
                search_q = params.get("query") or user_text
                if search_q:
                    await self.logger.log_action("reasoning", f"ID 누락으로 검색 시도: {search_q}", task_id=task_id)
                    search_res = await self.search_notion(query=search_q)
                    
                    if search_res:
                        best_match = search_res[0]
                        target_id = best_match["id"]
                        obj_type = best_match["object"]
                        params["page_id" if obj_type == "page" else "database_id"] = target_id
                        await self.logger.log_action("fallback", f"검색 결과 '{obj_type}' 발견 (ID: {target_id})", task_id=task_id)
                    else:
                        if self._database_id:
                            params["database_id"] = self._database_id
                            if action not in ["create_page"]: action = "query_database"
                            await self.logger.log_action("fallback", "검색 결과 없음, 기본 DB 사용", task_id=task_id)
                        else:
                            raise ValueError("조회할 대상(ID)을 찾을 수 없으며 검색 결과도 없습니다.")
                else:
                    # 검색어도 없고 ID도 없으면 기본 DB 사용
                    if self._database_id:
                        params["database_id"] = self._database_id
                    else:
                        raise ValueError("조회할 대상(ID)이나 검색어가 없습니다.")

            # [실행 및 자율 판단 2] 실행 중 오류 발생 시 자가 치유
            if action == "create_page":
                parent_id = params.get("database_id") or self._database_id
                title = params.get("title") or params.get("page_id") or "새 페이지"
                data = await self.create_page(parent_id, title)
                res_data["raw_data"] = data
                res_data["summary"] = f"새 페이지 '{title}'을(를) 생성했습니다."
                res_data["action"] = "create_page"

            elif action == "get_page":
                # 만약 database_id만 있고 page_id가 없다면 target_id로 사용
                target_id = params.get("page_id") or params.get("database_id")
                try:
                    data = await self.fetch_page(target_id)
                    res_data["raw_data"] = data
                    res_data["summary"] = "페이지 상세 정보를 가져왔습니다."
                except httpx.HTTPStatusError as e:
                    # 400 에러는 주로 페이지 ID 자리에 데이터베이스 ID가 들어갔을 때 발생
                    if e.response.status_code == 400:
                        await self.logger.log_action("self_healing", "페이지 조회 실패 -> DB 쿼리로 자동 전환", task_id=task_id)
                        db_res = await self.query_database(target_id)
                        res_data["raw_data"] = {"results": db_res}
                        res_data["action"] = "query_database"
                        res_data["metadata"]["db_id"] = target_id
                        res_data["summary"] = "데이터베이스의 모든 항목을 조회했습니다."
                    elif e.response.status_code == 404:
                        # 404 에러는 ID가 아예 존재하지 않는 경우 (할루시네이션 등)
                        await self.logger.log_action("self_healing", f"ID '{target_id}'를 찾을 수 없음 -> 이름으로 검색 시도", task_id=task_id)
                        # ID를 비우고 검색 로직을 다시 타게 유도 (재귀 호출 대신 파라미터 초기화 후 재시도 가능하나 여기선 직접 검색)
                        search_q = params.get("query") or params.get("title") or user_text
                        search_res = await self.search_notion(query=search_q)
                        if search_res:
                            best_match = search_res[0]
                            new_id = best_match["id"]
                            data = await self.fetch_page(new_id) # 새로 찾은 ID로 재시도
                            res_data["raw_data"] = data
                            res_data["summary"] = f"기존 ID를 찾을 수 없어 '{search_q}' 검색 결과로 정보를 가져왔습니다."
                        else: raise e
                    else: raise e

            elif action == "query_database":
                # 데이터베이스 쿼리 요청이지만, 검색 결과가 페이지인 경우 대응
                target_id = params.get("database_id") or params.get("page_id") or self._database_id
                
                # 정렬 및 필터링 처리 (사용자 요청: 오래된 순 등)
                query_filter = {}
                if "sorts" in params:
                    query_filter["sorts"] = params["sorts"]
                elif "오래된" in user_text:
                    query_filter["sorts"] = [{"timestamp": "created_time", "direction": "ascending"}]
                elif "최신" in user_text:
                    query_filter["sorts"] = [{"timestamp": "created_time", "direction": "descending"}]
                
                # 개수 제한
                page_size = params.get("page_size") or 100
                if "3개" in user_text: page_size = 3
                
                try:
                    db_res = await self.query_database(target_id, query_filter=query_filter)
                    # 결과 개수 제한 적용
                    db_res = db_res[:page_size]
                    res_data["raw_data"] = {"results": db_res}
                    res_data["metadata"]["db_id"] = target_id
                    res_data["summary"] = f"데이터베이스에서 {len(db_res)}개의 항목을 가져왔습니다."
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 400:
                        await self.logger.log_action("self_healing", "DB 쿼리 실패 -> 페이지 조회로 자동 전환", task_id=task_id)
                        data = await self.fetch_page(target_id)
                        res_data["raw_data"] = data
                        res_data["action"] = "get_page"
                        res_data["summary"] = f"'{target_id}'는 페이지로 확인되어 상세 정보를 가져왔습니다."
                    elif e.response.status_code == 404:
                        await self.logger.log_action("self_healing", f"DB ID '{target_id}'를 찾을 수 없음 -> 이름으로 검색 시도", task_id=task_id)
                        search_q = params.get("query") or params.get("title") or user_text
                        search_res = await self.search_notion(query=search_q, filter_obj={"property": "object", "value": "database"})
                        if search_res:
                            new_id = search_res[0]["id"]
                            db_res = await self.query_database(new_id, query_filter=query_filter)
                            res_data["raw_data"] = {"results": db_res[:page_size]}
                            res_data["metadata"]["db_id"] = new_id
                            res_data["summary"] = f"기존 ID를 찾을 수 없어 '{search_q}' 검색 결과({new_id})에서 데이터를 가져왔습니다."
                        else: raise e
                    else: raise e

            return await self._finalize_result(task_id, action, res_data)

        except Exception as exc:
            await self.logger.log_action("error", str(exc), task_id=task_id)
            return {
                "task_id": task_id, "status": "FAILED", "result_data": {},
                "error": {"code": "ARCHIVE_ERROR", "message": str(exc), "traceback": traceback.format_exc()}
            }

    async def _finalize_result(self, task_id: str, action: str, res_data: ArchiveTaskResult) -> dict[str, Any]:
        """결과 데이터를 정리하고 저장소에 보관한 뒤 최종 응답 형식을 반환합니다."""
        # [지능형 요약] 결과 데이터를 인간이 읽기 좋은 마크다운으로 변환
        res_data["content"] = self._generate_human_friendly_content(res_data)

        # 하이브리드 아키텍처: 대용량 메타데이터(JSON) 분산 저장
        ref_id = None
        if res_data["raw_data"]:
            ref_id = await self._storage.save_data(
                data=res_data["raw_data"],
                metadata={"action": action, "task_id": task_id, "source": "notion"}
            )
        
        res_data["reference_id"] = ref_id
        res_data["payload_summary"] = res_data["summary"]
        
        # 카시오페아 큐 오버헤드를 줄이기 위해 raw_data 삭제
        res_data.pop("raw_data", None)

        return {"task_id": task_id, "status": "COMPLETED", "result_data": res_data, "error": None, "usage_stats": {}}

    def _get_property_value(self, p_val: dict[str, Any]) -> str:
        """노션 속성 객체에서 실제 값을 문자열로 추출합니다."""
        if p_val is None:
            return ""
        p_type = p_val.get("type")
        if p_type == "title":
            t_list = p_val.get("title") or []
            return t_list[0].get("plain_text", "") if t_list else ""
        elif p_type == "rich_text":
            t_list = p_val.get("rich_text") or []
            return "".join([t.get("plain_text", "") for t in t_list])
        elif p_type == "status":
            return (p_val.get("status") or {}).get("name", "")
        elif p_type == "select":
            return (p_val.get("select") or {}).get("name", "")
        elif p_type == "multi_select":
            return ", ".join([m.get("name", "") for m in (p_val.get("multi_select") or [])])
        elif p_type == "date" and p_val.get("date"):
            d = p_val.get("date") or {}
            return f"{d.get('start', '')} ~ {d.get('end', '')}" if d.get('end') else d.get('start', '')
        elif p_type == "number":
            return str(p_val.get("number") or "")
        elif p_type == "url":
            return p_val.get("url") or ""
        elif p_type == "checkbox":
            return "✅" if p_val.get("checkbox") else "❌"
        elif p_type == "people":
            return ", ".join([p.get("name", "Unknown") for p in (p_val.get("people") or [])])
        elif p_type == "last_edited_time":
            return p_val.get("last_edited_time") or ""
        return f"({p_type})"

    def _generate_human_friendly_content(self, res: ArchiveTaskResult) -> str:
        """데이터를 기반으로 가독성 높은 마크다운 텍스트를 생성합니다."""
        raw = res["raw_data"]
        if not raw: return "조회된 데이터가 없습니다."
        
        action = res["action"]

        # 0. 페이지 생성 결과
        if action == "create_page":
            title = "제목 없음"
            props = raw.get("properties", {})
            for p_name, p_val in props.items():
                if p_val.get("type") == "title":
                    title = self._get_property_value(p_val)
            url = raw.get("url", "#")
            return f"✅ **새 페이지가 성공적으로 생성되었습니다!**\n\n- **제목**: {title}\n- **링크**: [Notion에서 열기]({url})"

        # 1. 데이터베이스 쿼리 결과 (항목 목록)
        if action == "query_database" or "results" in raw:
            results = raw.get("results", [])
            db_id = res.get("metadata", {}).get("db_id") or "알 수 없음"
            if not results: return f"데이터베이스가 비어있습니다. (ID: `{db_id}`)"
            
            lines = [f"### 📊 데이터 조회 결과 (총 {len(results)}건)", f"*대상 ID: `{db_id}`*", ""]
            for i, item in enumerate(results[:20]):
                props = item.get("properties", {})
                
                # 제목 찾기
                title = "제목 없음"
                info_parts = []
                for p_name, p_val in props.items():
                    val_str = self._get_property_value(p_val)
                    if p_val.get("type") == "title":
                        title = val_str or "제목 없음"
                    elif val_str and val_str != "None":
                        info_parts.append(f"**{p_name}**: {val_str}")
                
                detail = f" | {', '.join(info_parts)}" if info_parts else ""
                lines.append(f"{i+1}. **{title}**{detail}")
            
            if len(results) > 20:
                lines.append(f"\n*... 외 {len(results)-20}개의 항목이 더 있습니다.*")
            return "\n".join(lines)

        # 2. 데이터베이스 목록 조회
        elif action == "list_databases":
            databases = raw.get("databases", [])
            if not databases: return "연결된 데이터베이스가 없습니다."
            lines = ["### 📂 연결된 데이터베이스 목록", ""]
            for db in databases:
                title_list = db.get("title", [])
                title = title_list[0].get("plain_text", "제목 없음") if title_list else "제목 없음"
                lines.append(f"- **{title}** (ID: `{db.get('id')}`)")
            return "\n".join(lines)

        # 3. 통합 검색 결과
        elif action == "search_objects":
            items = raw.get("search_results", [])
            if not items: return "검색 결과가 없습니다."
            lines = ["### 🔍 노션 통합 검색 결과", ""]
            for item in items:
                obj_type = "📄 페이지" if item["object"] == "page" else "📊 DB"
                # 제목 추출
                title = "제목 없음"
                if item["object"] == "page":
                    props = item.get("properties", {})
                    for p in props.values():
                        if p.get("type") == "title":
                            title = self._get_property_value(p)
                else:
                    t_list = item.get("title") or []
                    title = t_list[0].get("plain_text", "제목 없음") if t_list else "제목 없음"
                
                lines.append(f"- {obj_type}: **{title}** ([이동]({item.get('url', '#')}))")
            return "\n".join(lines)

        # 4. 단일 페이지 상세 조회
        elif action == "get_page":
            props = raw.get("properties", {})
            page_id = raw.get("id", "알 수 없음")
            lines = [f"### 📄 페이지 상세 정보", f"*페이지 ID: `{page_id}`*", ""]
            for p_name, p_val in props.items():
                val_str = self._get_property_value(p_val)
                lines.append(f"- **{p_name}**: {val_str}")
            return "\n".join(lines)
        
        return f"```json\n{json.dumps(raw, indent=2, ensure_ascii=False)[:1000]}\n```"


    async def run(self) -> None:
        """Legacy 실행용"""
        pass
