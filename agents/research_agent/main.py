"""
Research Agent 진입점
- cassiopeia-sdk Pub/Sub으로 orchestra 디스패치 수신
"""

import asyncio
from shared_core.agent_logger import setup_logging

from agents.research_agent.cassiopeia_listener import ResearchCassiopeiaListener

# 보안 마스킹 필터가 적용된 로깅 설정 활성화
setup_logging()


def main() -> None:
    listener = ResearchCassiopeiaListener()
    asyncio.run(listener.run())


if __name__ == "__main__":
    main()
