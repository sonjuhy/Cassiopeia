from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict

type CalendarEventId = str
type EventStatus = Literal["confirmed", "tentative", "cancelled"]

class CalendarEvent(BaseModel):
    """
    일정 정보를 담는 데이터 모델입니다. (구글 캘린더 규격 기반)
    """
    model_config = ConfigDict(frozen=True)

    event_id: CalendarEventId | None = None
    title: str
    start_time: datetime
    end_time: datetime
    description: str | None = None
    location: str | None = None
    attendees: list[str] = []
    status: EventStatus = "confirmed"
