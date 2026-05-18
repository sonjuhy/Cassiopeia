"""
카시오페아 에이전트 추상 인터페이스 (Protocol)
- python-strict-typing 전략: Protocol 기반 다형성
"""

from __future__ import annotations

from typing import Any, Protocol

from .models import AgentResult, CassiopeiaTask

class StateManagerProtocol(Protocol):
    """세션 상태 및 대화 이력 관리 인터페이스"""

    async def get_session_state(self, session_id: str) -> dict[str, Any]:
        """Redis에서 세션 상태를 조회합니다."""
        ...

    async def update_session_state(self, session_id: str, fields: dict[str, Any]) -> None:
        """세션 상태를 업데이트하고 TTL을 갱신합니다."""
        ...

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        provider: str = "system",
        tokens: int = 0,
    ) -> None:
        """대화 이력에 메시지를 추가합니다."""
        ...

    async def build_context_for_llm(
        self,
        session_id: str,
        provider: str = "gemini",
    ) -> list[dict[str, Any]]:
        """현재 세션의 컨텍스트를 LLM 형식으로 재구성합니다."""
        ...

    async def maybe_summarize(self, session_id: str) -> None:
        """필요 시 오래된 메시지를 요약하고 Redis를 정리합니다."""
        ...

    async def update_task_state(self, task_id: str, fields: dict[str, Any]) -> None:
        """태스크 상태를 업데이트합니다."""
        ...


class HealthMonitorProtocol(Protocol):
    """에이전트 헬스 모니터링 인터페이스"""

    async def get_available_agents(self) -> list[str]:
        """하트비트가 30초 이내이고 IDLE 상태인 에이전트 목록을 반환합니다."""
        ...

    async def check_circuit_breaker(self, agent_name: str) -> bool:
        """Circuit Breaker 열림 여부를 반환합니다 (True = 차단됨)."""
        ...

    async def record_failure(self, agent_name: str) -> None:
        """에이전트 실패를 기록하고 Circuit Breaker 임계값 초과 시 차단합니다."""
        ...

    async def record_success(self, agent_name: str) -> None:
        """에이전트 성공 시 실패 카운터를 초기화합니다."""
        ...


class CassiopeiaManagerProtocol(Protocol):
    """카시오페아 매니저 메인 인터페이스"""

    async def listen_tasks(self) -> None:
        """
        agent:cassiopeia:tasks 큐를 BLPOP으로 감시하는 메인 루프입니다.
        각 태스크를 비동기 Task로 처리합니다.
        FastAPI lifespan에서 백그라운드 태스크로 실행됩니다.
        """
        ...

    async def process_task(self, task: CassiopeiaTask) -> None:
        """
        단일 태스크 처리 파이프라인 (NLU → Plan → Dispatch → Monitor).

        Args:
            task: 소통 에이전트로부터 수신된 작업 요청.
        """
        ...

    async def receive_agent_result(self, result: AgentResult) -> None:
        """
        하위 에이전트로부터 결과를 수신하여 cassiopeia:results:{task_id} 큐에 push합니다.
        FastAPI POST /results 엔드포인트에서 호출됩니다.

        Args:
            result: 에이전트 실행 결과.
        """
        ...
