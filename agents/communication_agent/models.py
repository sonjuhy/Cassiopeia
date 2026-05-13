"""
Communication Agent 데이터 모델 (Python 3.12+)
"""

import json
import os
from typing import Any, TypedDict

# Python 3.12: PEP 695 Type Aliases
type RawPayload = dict[str, Any]
type PageId = str
type ExecutionResult = tuple[bool, str]
type SlackMessage = dict[str, Any]
type AgentName = str


def _build_agent_registry() -> dict[str, str]:
    """에이전트 레지스트리를 환경변수 COMM_AGENT_REGISTRY(JSON)에서 빌드합니다.
    미설정 시 빈 딕셔너리를 반환합니다.

    예시:
        COMM_AGENT_REGISTRY='{"archive_agent": "문서/기획 처리", "file_agent": "파일 처리"}'
    """
    raw = os.environ.get("COMM_AGENT_REGISTRY", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {}


# 에이전트 레지스트리: 에이전트 이름 → 역할 설명
# 환경변수 COMM_AGENT_REGISTRY(JSON)로 재정의하세요. 코드를 수정하지 마세요.
AGENT_REGISTRY: dict[str, str] = _build_agent_registry()


class SlackEvent(TypedDict):
    """Slack Socket Mode에서 수신된 메시지 이벤트의 표준 데이터 구조"""
    user: str
    channel: str
    text: str
    ts: str
    thread_ts: str | None


class DiscordEvent(TypedDict):
    """Discord에서 수신된 메시지 이벤트의 표준 데이터 구조"""
    user_id: str            # Discord 사용자 ID (int → str 변환)
    channel_id: str         # 채널 또는 DM 채널 ID
    guild_id: str | None    # 서버 ID (DM이면 None)
    text: str
    message_id: str         # 메시지 ID (스레드 추적용)


class TelegramEvent(TypedDict):
    """Telegram에서 수신된 메시지 이벤트의 표준 데이터 구조"""
    user_id: str            # Telegram 사용자 ID (int → str 변환)
    chat_id: str            # 채팅 ID (그룹/개인)
    text: str
    message_id: str         # 메시지 ID (진행 메시지 추적용)


class ParsedTask(TypedDict):
    """파싱 완료된 노션 태스크의 표준 데이터 구조"""
    page_id: PageId
    title: str
    description: str
    status: str
    github_pr: str
    design_doc: str
    agent_assignees: list[str]
    assignees: list[str]
    skeleton_code: str
    priority: str
    last_edited_time: str
    task_type: str


# ── Redis 기반 메시지 브로커 스키마 ──────────────────────────────────────────────

class CassiopeiaTaskRequester(TypedDict):
    """카시오페아 태스크 요청자 정보"""
    user_id: str
    channel_id: str


class CassiopeiaTask(TypedDict):
    """소통 에이전트 → Redis → 카시오페아 전달 메시지 스키마"""
    task_id: str
    session_id: str        # 카시오페아 NLU 컨텍스트 주입용 (format: user_id:channel_id)
    requester: CassiopeiaTaskRequester
    content: str
    source: str            # 소스 플랫폼 식별자 (slack | discord | telegram | ...)
    thread_ts: str | None  # 스레드 루트 ts (세션 연속성용)


class CassiopeiaResult(TypedDict):
    """카시오페아 → Redis → 소통 에이전트 결과 메시지 스키마"""
    task_id: str
    content: str
    requires_user_approval: bool
    agent_name: str
    progress_percent: int | None  # None이면 완료, 0~99면 진행 중


class ApprovalFeedback(TypedDict):
    """사용자 [승인/수정 요청/취소] 버튼 클릭 피드백 스키마"""
    task_id: str
    action: str           # "approve" | "request_revision" | "cancel"
    user_id: str
    channel_id: str
    comment: str | None   # 수정 요청 시 입력된 텍스트
