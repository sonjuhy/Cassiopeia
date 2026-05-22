"""
공유 애플리케이션 컨텍스트
- main.py 와 admin_router.py 등 여러 모듈이 참조하는 싱글톤 상태 객체
- FastAPI lifespan에서 초기화, 이후 모든 라우터에서 임포트하여 사용
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from cassiopeia_sdk.client import CassiopeiaClient
    from .health_monitor import HealthMonitor
    from .manager import CassiopeiaManager
    from .state_manager import StateManager
    from .agent_builder_handler import AgentBuilderHandler
    from .registry import AgentRegistry
    from .marketplace_handler import MarketplaceHandler
    from .sandbox_tool import SandboxTool
    from .llm_gateway import LLMGatewayHandler
    from .system_executor import SystemExecutor


class _AppContext:
    manager: CassiopeiaManager
    state_manager: StateManager
    health_monitor: HealthMonitor
    builder_handler: AgentBuilderHandler
    registry: AgentRegistry
    marketplace: MarketplaceHandler
    sandbox_tool: SandboxTool | None = None
    redis_client: aioredis.Redis
    cassiopeia_client: CassiopeiaClient
    llm_gateway: LLMGatewayHandler | None = None
    system_executor: SystemExecutor | None = None
    listen_task: asyncio.Task | None = None
    monitor_task: asyncio.Task | None = None
    executor_task: asyncio.Task | None = None


# 모듈 수준 싱글톤 — lifespan에서 각 필드를 채운다
ctx = _AppContext()
