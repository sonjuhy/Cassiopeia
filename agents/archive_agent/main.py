"""
Archive Agent 진입점

MODE 환경변수로 동작 모드를 선택합니다:
    ephemeral (기본): Notion에서 '검토중' 태스크를 가져와 처리 후 자연 종료
                      ephemeral-docker-ops 전략 준수 (cron/스케줄러 실행용)
    server:           FastAPI + Redis 리스너 서버 실행
                      CassiopeiaManager로부터 태스크를 수신하여 처리
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv(encoding="utf-8", override=True)

from shared_core.agent_logger import setup_logging

# 보안 마스킹 필터가 적용된 로깅 설정 활성화
setup_logging()

logger = logging.getLogger("archive_agent.main")


def _run_ephemeral() -> None:
    """Notion 기반 단발성 실행 (기본 ephemeral 모드)."""
    from .notion.agent import ArchiveAgent

    agent = ArchiveAgent()
    asyncio.run(agent.run())


def _run_server() -> None:
    """FastAPI 서버 모드 — CassiopeiaManager Redis 큐 수신."""
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8002"))
    logger.info("Archive Agent 서버 시작: %s:%d", host, port)
    uvicorn.run(
        "agents.archive_agent.fastapi_app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


def main() -> None:
    vault_path: str | None = os.getenv("OBSIDIAN_VAULT_PATH")
    logger.info(f"로드된 경로: {vault_path}")  # 여기서 깨져서 나오는지 확인 필수

    mode = (os.environ.get("NOTION_MODE") or os.environ.get("MODE") or "ephemeral").lower()
    if mode == "server":
        _run_server()
    else:
        _run_ephemeral()


if __name__ == "__main__":
    main()
