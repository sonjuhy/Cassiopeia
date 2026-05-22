"""
SystemExecutor — Redis 큐에서 시스템 제어/복구 명령을 소비하여
Docker API를 통해 실제로 실행하는 백그라운드 실행자.

아키텍처 (Intent Queue 패턴):
  [cassiopeia API]                   [SystemExecutor (백그라운드 루프)]
    POST /admin/system/control    →  BLPOP system:ops:pending
      └─ Redis LIST에 명령 기록          └─ Docker API 실행
                                         └─ system:control:{id} 상태 업데이트
    POST /admin/system/repair     →  BLPOP system:repair:pending
      └─ Redis HASH 초기화 + LIST        └─ Docker API 실행
                                         └─ system:repair:{id} 상태 업데이트

컨테이너 이름 매핑:
  기본값:
    core_engine  → cassiopeia_agent  (env: CONTAINER_CORE_ENGINE)
    network_mesh → communication_agent (env: CONTAINER_NETWORK_MESH)
  "all" target 은 맵의 모든 컨테이너에 작업을 적용합니다.

Docker 소켓:
  /var/run/docker.sock 이 컨테이너에 마운트되어 있어야 합니다.
  (docker-compose.yml 에 volumes 항목 확인)
  소켓 없을 때는 RuntimeError 로 실패 처리되고 해당 명령 상태가 'failed' 로 기록됩니다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("cassiopeia_agent.system_executor")

# Redis 큐 키
_CONTROL_QUEUE = "system:ops:pending"
_REPAIR_QUEUE = "system:repair:pending"

# BLPOP 타임아웃 (초) — CancelledError 처리를 위해 짧게 유지
_BLPOP_TIMEOUT = 5


def _build_container_map() -> dict[str, str]:
    """
    target 이름 → 실제 Docker 컨테이너 이름 매핑.
    환경변수로 오버라이드 가능하므로 하드코딩 없음.
    """
    return {
        "core_engine": os.environ.get("CONTAINER_CORE_ENGINE", "cassiopeia_agent"),
        "network_mesh": os.environ.get("CONTAINER_NETWORK_MESH", "communication_agent"),
    }


class SystemExecutor:
    """
    Redis 큐를 구독하고 Docker API로 시스템 명령을 실행하는 실행자.

    사용법:
        executor = SystemExecutor(redis_client=redis)
        task = asyncio.create_task(executor.run())   # lifespan 에서 시작
        task.cancel()                                 # lifespan 종료 시
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client
        self._container_map: dict[str, str] = _build_container_map()
        self._docker_client: Any = None  # 지연 초기화

    # ── Docker 클라이언트 ────────────────────────────────────────────────────

    def _get_docker_client(self) -> Any | None:
        """
        Docker SDK 클라이언트 지연 초기화.
        docker 패키지 또는 소켓이 없으면 None 을 반환합니다.
        """
        if self._docker_client is not None:
            return self._docker_client
        try:
            import docker  # type: ignore[import-untyped]
            self._docker_client = docker.from_env()
            logger.info("[SystemExecutor] Docker 클라이언트 초기화 완료")
            return self._docker_client
        except Exception as exc:
            logger.warning(
                "[SystemExecutor] Docker 클라이언트 초기화 실패 "
                "(docker.sock 마운트 또는 docker SDK 확인): %s", exc
            )
            return None

    # ── 메인 루프 ────────────────────────────────────────────────────────────

    async def _process_once(self, timeout: int = _BLPOP_TIMEOUT) -> bool:
        """
        큐에서 명령 1건을 꺼내 처리합니다.
        - 명령을 처리하면 True 반환
        - timeout 초 내에 명령이 없으면 False 반환
        테스트에서 루프 없이 단일 처리를 검증할 때 사용합니다.

        timeout=0: Redis BLPOP 의 '무한 대기' 시맨틱을 피하기 위해
                   비블로킹 LPOP 으로 즉시 폴링합니다.
        timeout>0: BLPOP 으로 최대 timeout 초 대기합니다.
        """
        if timeout == 0:
            # 비블로킹 즉시 폴링 — BLPOP(timeout=0) 은 무한 대기이므로 LPOP 으로 대체
            for queue_name in (_CONTROL_QUEUE, _REPAIR_QUEUE):
                raw = await self._redis.lpop(queue_name)
                if raw is not None:
                    command: dict[str, Any] = json.loads(raw)
                    if queue_name == _CONTROL_QUEUE:
                        await self._handle_control(command)
                    else:
                        await self._handle_repair(command)
                    return True
            return False

        result = await self._redis.blpop(
            [_CONTROL_QUEUE, _REPAIR_QUEUE],
            timeout=timeout,
        )
        if result is None:
            return False

        queue_name, raw = result
        command = json.loads(raw)

        if queue_name == _CONTROL_QUEUE:
            await self._handle_control(command)
        elif queue_name == _REPAIR_QUEUE:
            await self._handle_repair(command)
        return True

    async def run(self) -> None:
        """
        두 큐를 동시에 감시하는 메인 루프.
        asyncio.CancelledError 를 받으면 깔끔하게 종료합니다.
        """
        logger.info(
            "[SystemExecutor] 시작 — 큐 감시: %s, %s",
            _CONTROL_QUEUE, _REPAIR_QUEUE,
        )
        while True:
            try:
                await self._process_once()
            except asyncio.CancelledError:
                logger.info("[SystemExecutor] 종료 신호 수신 — 루프 중단")
                break
            except Exception as exc:
                logger.error("[SystemExecutor] 처리 중 예외 발생: %s", exc, exc_info=True)
                await asyncio.sleep(1)

    # ── 제어 명령 처리 ────────────────────────────────────────────────────────

    async def _handle_control(self, command: dict[str, Any]) -> None:
        action = command.get("action", "")
        target = command.get("target", "")
        command_id = command.get("command_id", "unknown")
        status_key = f"system:control:{command_id}"

        logger.info(
            "[SystemExecutor] 제어 명령 실행: action=%s target=%s id=%s",
            action, target, command_id,
        )
        try:
            await self._execute_control(action, target)
            await self._redis.hset(status_key, mapping={
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("[SystemExecutor] 제어 완료: %s", command_id)
        except Exception as exc:
            logger.error(
                "[SystemExecutor] 제어 실패 (id=%s): %s", command_id, exc
            )
            await self._redis.hset(status_key, mapping={
                "status": "failed",
                "error": str(exc),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            })

    async def _execute_control(self, action: str, target: str) -> None:
        """실제 Docker API 를 호출하여 컨테이너를 제어합니다."""
        client = self._get_docker_client()
        if client is None:
            raise RuntimeError(
                "Docker 클라이언트를 초기화할 수 없습니다. "
                "/var/run/docker.sock 마운트 및 docker SDK 설치를 확인하세요."
            )

        # target="all" → 맵의 모든 컨테이너
        targets = (
            list(self._container_map.values())
            if target == "all"
            else [self._container_map.get(target, target)]
        )

        for container_name in targets:
            container = client.containers.get(container_name)
            if action == "restart":
                await asyncio.to_thread(container.restart, timeout=30)
                logger.info("[SystemExecutor] 재시작 완료: %s", container_name)

            elif action == "terminate":
                await asyncio.to_thread(container.stop, timeout=30)
                logger.info("[SystemExecutor] 종료 완료: %s", container_name)

            elif action == "optimize":
                # 메모리 사용률 확인 → 85% 초과 시 재시작
                stats = await asyncio.to_thread(container.stats, stream=False)
                mem_stats = stats.get("memory_stats", {})
                mem_usage = mem_stats.get("usage", 0)
                mem_limit = mem_stats.get("limit", 1)
                usage_pct = (mem_usage / mem_limit) * 100 if mem_limit else 0
                logger.info(
                    "[SystemExecutor] optimize 체크 %s: 메모리 %.1f%%",
                    container_name, usage_pct,
                )
                if usage_pct > 85.0:
                    await asyncio.to_thread(container.restart, timeout=30)
                    logger.info(
                        "[SystemExecutor] 메모리 초과(%.1f%%)로 재시작: %s",
                        usage_pct, container_name,
                    )
                else:
                    logger.info(
                        "[SystemExecutor] 메모리 정상(%.1f%%) — 재시작 불필요: %s",
                        usage_pct, container_name,
                    )

    # ── 복구 명령 처리 ────────────────────────────────────────────────────────

    async def _handle_repair(self, command: dict[str, Any]) -> None:
        repair_id = command.get("repair_id", "unknown")
        module_id = command.get("module_id", "")
        repair_type = command.get("repair_type", "")
        status_key = f"system:repair:{repair_id}"

        logger.info(
            "[SystemExecutor] 복구 실행: id=%s module=%s type=%s",
            repair_id, module_id, repair_type,
        )
        try:
            await self._execute_repair(module_id, repair_type)
            await self._redis.hset(status_key, mapping={
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("[SystemExecutor] 복구 완료: %s", repair_id)
        except Exception as exc:
            logger.error(
                "[SystemExecutor] 복구 실패 (id=%s): %s", repair_id, exc
            )
            await self._redis.hset(status_key, mapping={
                "status": "failed",
                "error": str(exc),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            })

    async def _execute_repair(self, module_id: str, repair_type: str) -> None:
        """실제 Docker API 를 호출하여 모듈을 복구합니다."""
        client = self._get_docker_client()
        if client is None:
            raise RuntimeError(
                "Docker 클라이언트를 초기화할 수 없습니다. "
                "/var/run/docker.sock 마운트를 확인하세요."
            )

        container_name = self._container_map.get(module_id, module_id)
        container = client.containers.get(container_name)

        if repair_type == "hotfix":
            # hotfix: 컨테이너 재시작만
            await asyncio.to_thread(container.restart, timeout=30)
            logger.info("[SystemExecutor] Hotfix(재시작) 완료: %s", container_name)

        elif repair_type == "full_reinstall":
            # full_reinstall: 최신 이미지 pull → 재시작
            image_name: str = container.attrs["Config"]["Image"]
            logger.info("[SystemExecutor] 이미지 pull 시작: %s", image_name)
            await asyncio.to_thread(client.images.pull, image_name)
            logger.info("[SystemExecutor] 이미지 pull 완료: %s", image_name)
            await asyncio.to_thread(container.restart, timeout=60)
            logger.info("[SystemExecutor] Full reinstall 완료: %s", container_name)
