import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScheduleAgentConfig:
    """
    일정 관리 에이전트의 설정 정보를 관리합니다.

    Attributes:
        calendar_id: 대상 구글 캘린더 ID. 기본값 "primary".
        service_account_key_file: 서비스 계정 JSON 키 파일 경로.
        service_account_key_json: 서비스 계정 JSON 키 문자열 (파일 대신 직접 주입 시 사용).
        scopes: 요청할 OAuth 스코프 목록.
    """

    calendar_id: str = "primary"
    service_account_key_file: str = ""
    service_account_key_json: str = ""
    scopes: list[str] = field(
        default_factory=lambda: ["https://www.googleapis.com/auth/calendar"]
    )
    cassiopeia_api_key: str = ""


def load_config_from_env() -> ScheduleAgentConfig:
    """
    환경 변수로부터 ScheduleAgentConfig를 로드합니다.

    환경 변수:
        GOOGLE_CALENDAR_ID              : 대상 캘린더 ID. 기본값 "primary".
        GOOGLE_SERVICE_ACCOUNT_KEY_FILE : 서비스 계정 JSON 키 파일 경로.
        GOOGLE_SERVICE_ACCOUNT_JSON     : 서비스 계정 JSON 키 문자열 (파일 경로보다 우선).
        CASSIOPEIA_API_KEY              : 오케스트라 API 키.
        CLIENT_API_KEY                  : 오케스트라 API 키 (별칭).
    """
    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY_FILE", "")
    key_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    
    if not key_file and not key_json:
        error_msg = (
            "\n" + "="*70 + "\n"
            "🚨 [설정 오류] 일정 에이전트 실행을 위한 필수 환경 변수가 누락되었습니다.\n"
            "구글 캘린더 연동을 위해 다음 단계에 따라 .env.schedule 파일을 생성하세요.\n"
            "\n"
            "1. 프로젝트 루트 폴더에 '.env.schedule' 파일을 생성합니다.\n"
            "2. 구글 서비스 계정 키 내용을 아래와 같이 입력합니다:\n"
            "   GOOGLE_CALENDAR_ID=your_email@gmail.com\n"
            "   GOOGLE_SERVICE_ACCOUNT_JSON={\"type\": \"service_account\", ...}\n"
            "\n"
            "3. 실행 스크립트(start_schedule.bat/sh)의 docker run 명령어에 다음 줄을 추가합니다:\n"
            "   --env-file .env.schedule ^  (또는 \\)\n"
            "="*70 + "\n"
        )
        # 즉각적인 실패를 위해 예외 발생
        raise ValueError(error_msg)

    return ScheduleAgentConfig(
        calendar_id=os.environ.get("GOOGLE_CALENDAR_ID", "primary"),
        service_account_key_file=key_file,
        service_account_key_json=key_json,
        cassiopeia_api_key=(os.environ.get("CASSIOPEIA_API_KEY") or os.environ.get("CLIENT_API_KEY", "")).strip('"\''),
    )
