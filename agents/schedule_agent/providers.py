"""
캘린더 공급자 구체 구현체

- GoogleCalendarProvider: Google Calendar API v3 (서비스 계정 인증)
"""

import asyncio
import json
from datetime import datetime, timezone

from shared_core.calendar.interfaces import CalendarEvent, CalendarEventId

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarProvider:
    """
    Google Calendar API v3를 사용하는 캘린더 공급자입니다.

    인증 방식: 서비스 계정 (JSON 키 파일 또는 JSON 문자열)

    필요 패키지:
        google-api-python-client >= 2.0
        google-auth >= 2.0
    """

    def __init__(
        self,
        calendar_id: str = "primary",
        service_account_key_file: str = "",
        service_account_key_json: str = "",
        scopes: list[str] | None = None,
    ) -> None:
        self._calendar_id = calendar_id
        self._scopes = scopes or _SCOPES
        self._service_account_key_file = service_account_key_file
        self._service_account_key_json = service_account_key_json
        self._service = None  # 지연 초기화

    # ------------------------------------------------------------------ #
    # 캘린더 CRUD 구현                                                     #
    # ------------------------------------------------------------------ #

    async def get_events(
        self, start_min: datetime, start_max: datetime
    ) -> list[CalendarEvent]:
        """주어진 시간 범위 내의 일정 목록을 조회합니다."""
        service = await self._get_service()
        items = await asyncio.to_thread(
            self._sync_list_events, service, start_min, start_max
        )
        return [self._to_calendar_event(item) for item in items]

    async def create_event(self, event: CalendarEvent) -> CalendarEventId:
        """새로운 일정을 생성하고 생성된 이벤트 ID를 반환합니다."""
        service = await self._get_service()
        body = self._to_api_body(event)
        created = await asyncio.to_thread(
            self._sync_create_event, service, body
        )
        return created.get("id", "")

    async def update_event(
        self, event_id: CalendarEventId, event: CalendarEvent
    ) -> bool:
        """기존 일정을 수정합니다."""
        service = await self._get_service()
        body = self._to_api_body(event)
        return await asyncio.to_thread(
            self._sync_update_event, service, event_id, body
        )

    async def delete_event(self, event_id: CalendarEventId) -> bool:
        """일정을 삭제합니다."""
        service = await self._get_service()
        return await asyncio.to_thread(
            self._sync_delete_event, service, event_id
        )

    # ------------------------------------------------------------------ #
    # 동기 API 호출 (asyncio.to_thread 에서 실행)                          #
    # ------------------------------------------------------------------ #

    def _sync_list_events(
        self, service, start_min: datetime, start_max: datetime
    ) -> list[dict]:
        result = (
            service.events()
            .list(
                calendarId=self._calendar_id,
                timeMin=_to_rfc3339(start_min),
                timeMax=_to_rfc3339(start_max),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return result.get("items", [])

    def _sync_create_event(self, service, body: dict) -> dict:
        return (
            service.events()
            .insert(calendarId=self._calendar_id, body=body)
            .execute()
        )

    def _sync_update_event(
        self, service, event_id: CalendarEventId, body: dict
    ) -> bool:
        try:
            service.events().update(
                calendarId=self._calendar_id, eventId=event_id, body=body
            ).execute()
            return True
        except Exception:
            return False

    def _sync_delete_event(self, service, event_id: CalendarEventId) -> bool:
        try:
            service.events().delete(
                calendarId=self._calendar_id, eventId=event_id
            ).execute()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # 서비스 초기화 (지연 로드)                                            #
    # ------------------------------------------------------------------ #

    async def _get_service(self):
        if self._service is None:
            self._service = await asyncio.to_thread(self._build_service)
        return self._service

    def _build_service(self):
        try:
            from google.oauth2 import service_account  # type: ignore[import]
            from googleapiclient.discovery import build  # type: ignore[import]
        except ImportError as e:
            raise ImportError(
                "google-api-python-client 및 google-auth 패키지가 필요합니다:\n"
                "pip install google-api-python-client google-auth"
            ) from e

        if self._service_account_key_json:
            info = json.loads(self._service_account_key_json)
            credentials = service_account.Credentials.from_service_account_info(
                info, scopes=self._scopes
            )
        elif self._service_account_key_file:
            credentials = service_account.Credentials.from_service_account_file(
                self._service_account_key_file, scopes=self._scopes
            )
        else:
            raise ValueError(
                "서비스 계정 키가 설정되지 않았습니다. "
                "GOOGLE_SERVICE_ACCOUNT_KEY_FILE 또는 GOOGLE_SERVICE_ACCOUNT_JSON 을 설정하세요."
            )

        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    # ------------------------------------------------------------------ #
    # 데이터 변환                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _to_calendar_event(item: dict) -> CalendarEvent:
        """Google Calendar API 응답 dict를 CalendarEvent 모델로 변환합니다."""
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})

        start_time = _parse_datetime(
            start_raw.get("dateTime") or start_raw.get("date", "")
        )
        end_time = _parse_datetime(
            end_raw.get("dateTime") or end_raw.get("date", "")
        )

        attendees = [
            a.get("email", "") for a in item.get("attendees", []) if a.get("email")
        ]

        raw_status = item.get("status", "confirmed")
        status = raw_status if raw_status in ("confirmed", "tentative", "cancelled") else "confirmed"

        return CalendarEvent(
            event_id=item.get("id"),
            title=item.get("summary", "(제목 없음)"),
            start_time=start_time,
            end_time=end_time,
            description=item.get("description"),
            location=item.get("location"),
            attendees=attendees,
            status=status,  # type: ignore[arg-type]
        )

    @staticmethod
    def _to_api_body(event: CalendarEvent) -> dict:
        """CalendarEvent 모델을 Google Calendar API 요청 body dict로 변환합니다."""
        body: dict = {
            "summary": event.title,
            "start": {"dateTime": _to_rfc3339(event.start_time)},
            "end": {"dateTime": _to_rfc3339(event.end_time)},
            "status": event.status,
        }
        if event.description:
            body["description"] = event.description
        if event.location:
            body["location"] = event.location
        if event.attendees:
            body["attendees"] = [{"email": email} for email in event.attendees]
        return body


# ------------------------------------------------------------------ #
# 유틸리티                                                            #
# ------------------------------------------------------------------ #

def _to_rfc3339(dt: datetime) -> str:
    """datetime을 RFC 3339 문자열로 변환합니다. timezone-naive 이면 UTC로 간주합니다."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_datetime(value: str) -> datetime:
    """ISO 8601 / RFC 3339 문자열을 datetime으로 변환합니다."""
    if not value:
        return datetime.now(tz=timezone.utc)
    # 날짜만 있는 경우 (종일 이벤트)
    if "T" not in value:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value)
