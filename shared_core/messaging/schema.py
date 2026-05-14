from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Protocol
from pydantic import BaseModel, Field, ConfigDict

# AgentName 은 열거 타입이 아닌 str 별칭입니다.
# 에이전트 이름을 Literal 로 고정하면 새 에이전트 추가 시 shared_core 를 수정해야 하는
# 강한 결합이 발생합니다. str 을 사용해 런타임 레지스트리(Redis) 기반으로 유효성을 검사하세요.
type AgentName = str
type ActionName = str

class AgentMessage(BaseModel):
    """에이전트 간 통신을 위한 표준 메시지 규격입니다. (Redis Pub/Sub 사용)

    Attributes:
        sender: 메시지를 보내는 에이전트의 식별자.
        receiver: 메시지를 받을 에이전트의 식별자.
        action: 수신 에이전트가 실행할 작업 이름.
        payload: 작업에 필요한 상세 데이터.
        reference_id: 대용량 데이터(도메인 데이터 등) 분산 저장을 위한 참조 식별자 (Hybrid Architecture 용).
        payload_summary: 대용량 데이터의 요약본 (Hybrid Architecture 용).
        timestamp: 메시지 생성 시각 (UTC).
    """

    model_config = ConfigDict(frozen=True)

    sender: AgentName
    receiver: AgentName
    action: ActionName
    payload: dict[str, Any] = Field(default_factory=dict)
    reference_id: str | None = None
    payload_summary: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)

    def to_json(self) -> str:
        """메시지를 JSON 문자열로 직렬화합니다."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, json_str: str) -> "AgentMessage":
        """JSON 문자열로부터 AgentMessage 인스턴스를 생성합니다."""
        return cls.model_validate_json(json_str)


class MessageBrokerProtocol(Protocol):
    """cassiopeia-sdk 기반 에이전트 메시지 브로커 인터페이스.

    모든 에이전트 메시지 브로커 구현체는 이 Protocol을 따릅니다.
    채널 이름 규칙: ``agent:{agent_name}``
    """

    async def publish(self, message: AgentMessage) -> bool:
        """메시지를 수신 에이전트의 채널에 발행합니다.

        Args:
            message: 발행할 AgentMessage 객체. ``message.receiver`` 채널로 전송됩니다.

        Returns:
            발행 성공 시 ``True``, 실패 시 ``False``.
        """
        ...

    def subscribe(self) -> AsyncIterator[AgentMessage]:
        """이 브로커의 agent_id를 대상으로 하는 메시지를 비동기로 수신 대기합니다.

        Returns:
            수신된 AgentMessage를 순차적으로 yield 하는 AsyncIterator.
        """
        ...
