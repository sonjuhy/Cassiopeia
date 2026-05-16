"""
에이전트 활동 로깅 유틸리티 (Shared Core)
- 모든 에이전트가 공통으로 사용하여 카시오페아에 로그를 보고합니다.
"""

import os
import logging
import httpx
import re
from typing import Any

# 민감 정보 패턴 (API 키, 토큰 등)
_SENSITIVE_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{32,}"),             # OpenAI, Anthropic 등
    re.compile(r"AIzaSy[a-zA-Z0-9_-]{30,40}"),      # Gemini / Google Cloud (약 39자)
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),             # GitHub Personal Access Token
    re.compile(r"xox[bap]-[a-zA-Z0-9-]+"),          # Slack Tokens
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]+"),        # JWT / Bearer Token
]

class SensitiveDataFilter(logging.Filter):
    """
    로그 메시지에서 API 키와 같은 민감한 정보를 자동으로 감지하여 ***MASKED***로 치환하는 필터.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._mask_text(record.msg)
        
        # 로그의 args (인자)에 문자열이 포함된 경우도 처리
        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    new_args.append(self._mask_text(arg))
                else:
                    new_args.append(arg)
            record.args = tuple(new_args)
            
        return True

    def _mask_text(self, text: str) -> str:
        masked = text
        for pattern in _SENSITIVE_PATTERNS:
            masked = pattern.sub("***MASKED***", masked)
        return masked

def setup_logging(level: int = logging.INFO):
    """
    보안 마스킹 필터가 적용된 표준 로깅 설정을 구성합니다.
    모든 에이전트 시작 시 호출하는 것이 권장됩니다.
    """
    # 기본 핸들러 및 포맷 설정
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # 루트 핸들러에 보안 필터 추가
    mask_filter = SensitiveDataFilter()
    for handler in logging.root.handlers:
        handler.addFilter(mask_filter)
        
    logging.getLogger("shared_core.agent_logger").info("보안 로깅 필터가 활성화되었습니다.")

logger = logging.getLogger("shared_core.agent_logger")

class AgentLogger:
    def __init__(self, agent_name: str, cassiopeia_url: str | None = None):
        self.agent_name = agent_name
        self.cassiopeia_url = cassiopeia_url or os.environ.get("CASSIOPEIA_URL", "http://127.0.0.1:8001")

    async def log_action(
        self, 
        action: str, 
        message: str, 
        task_id: str | None = None, 
        session_id: str | None = None, 
        payload: dict[str, Any] | None = None
    ):
        """카시오페아의 /logs 엔드포인트로 활동 로그를 전송합니다."""
        url = f"{self.cassiopeia_url}/logs"
        data = {
            "agent_name": self.agent_name,
            "action": action,
            "message": message,
            "task_id": task_id,
            "session_id": session_id,
            "payload": payload
        }
        
        # 환경변수에서 인증 키 로드 (관리자 키 또는 클라이언트 키, 따옴표 제거 필수)
        api_key = (os.environ.get("ADMIN_API_KEY") or os.environ.get("CLIENT_API_KEY", "")).strip("\"'")

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    url, 
                    json=data,
                    headers={"X-API-Key": api_key}
                )
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"[{self.agent_name}] 로그 전송 실패: {e}")

# 각 에이전트에서 사용 예시:
# logger = AgentLogger("archive_agent")
# await logger.log_action("query_database", "Notion DB 조회 성공", task_id="...", payload={"db_id": "..."})
