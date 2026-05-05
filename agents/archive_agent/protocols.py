"""
Archive Agent 추상 인터페이스 (Protocol)
- python-strict-typing 전략: 엄격한 정적 타입 선언 및 추상 인터페이스
- ephemeral 모드: cron 스케줄러 또는 직접 실행용 단발성 계약
"""

from typing import Protocol

from .models import ExecutionResult, ParsedTask, RawPayload


class ArchiveAgentProtocol(Protocol):
    """
    Archive Agent의 동작을 정의하는 추상 인터페이스입니다.
    이 에이전트는 무한 루프나 데몬 없이, 스케줄링된 1회 실행 주기를 갖습니다.
    (ephemeral 모드: cron 스케줄러 또는 직접 실행용)
    """

    agent_name: str

    async def fetch_pending_tasks(self) -> list[RawPayload]:
        """
        Notion 데이터베이스에서 기획 단계에 있는 작업 목록을 가져옵니다.
        요청 시 반드시 헤더에 "Notion-Version": "2022-06-28"를 포함해야 합니다.
        """
        ...

    async def process_task(self, task_data: ParsedTask) -> ExecutionResult:
        """개별 작업에 대하여 아카이브 에이전트의 구체적 로직을 단발성으로 수행합니다."""
        ...

    async def run(self) -> None:
        """
        에이전트 사이클의 진입점입니다.
        작업을 가져오고 파싱하여 처리한 후 곧바로 프로세스를 종료(자연 종료)해야 합니다.
        (ephemeral-docker-ops 전략 준수: while True 혹은 asyncio.sleep 반복 금지)
        """
        ...
