"""
SystemExecutor TDD 테스트

커버 대상:
  - Redis 큐(system:ops:pending, system:repair:pending)에서 명령 소비
  - Docker API 실제 호출 (restart / stop / stats)
  - 컨테이너 맵 env-var 오버라이드
  - 성공 시 Redis 상태 업데이트 (completed)
  - 실패 시 Redis 상태 업데이트 (failed)
  - Docker 소켓 없을 때 우아한 저하(graceful degradation)
  - optimize: 메모리 85% 초과 시 재시작, 이하 시 생략
  - full_reinstall: 이미지 pull → 재시작
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── 픽스처 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_docker_client():
    """
    docker.from_env() 가 반환하는 DockerClient 모의 객체.
    같은 이름으로 get() 을 여러 번 호출해도 동일 Mock 인스턴스를 반환합니다.
    (캐시 없으면 호출마다 새 Mock 이 생성되어 assert 가 깨짐)
    """
    client = MagicMock()
    _cache: dict[str, MagicMock] = {}

    def _get_container(name: str) -> MagicMock:
        if name not in _cache:
            c = MagicMock()
            c.name = name
            c.attrs = {
                "Config": {"Image": f"ghcr.io/test/{name}:latest"},
                "Id": f"fake-id-{name}",
            }
            c.restart = MagicMock()
            c.stop = MagicMock()
            c.stats = MagicMock(return_value={
                "memory_stats": {
                    "usage": 100 * 1024 * 1024,
                    "limit": 512 * 1024 * 1024,
                },
            })
            _cache[name] = c
        return _cache[name]

    client.containers.get = MagicMock(side_effect=_get_container)
    client.images.pull = MagicMock()
    # 테스트에서 캐시에 직접 접근할 수 있도록 노출
    client._cache = _cache
    return client


@pytest.fixture
def executor(fake_redis):
    from agents.cassiopeia_agent.system_executor import SystemExecutor
    return SystemExecutor(redis_client=fake_redis)


@pytest.fixture
def executor_with_docker(fake_redis, mock_docker_client):
    from agents.cassiopeia_agent.system_executor import SystemExecutor
    ex = SystemExecutor(redis_client=fake_redis)
    ex._docker_client = mock_docker_client   # 직접 주입 (socket 없이 테스트)
    return ex


# ══════════════════════════════════════════════════════════════════════════════
# 1. 컨테이너 맵 설정
# ══════════════════════════════════════════════════════════════════════════════

class TestContainerMap:
    def test_default_map_contains_core_engine_and_network_mesh(self, executor):
        cmap = executor._container_map
        assert "core_engine" in cmap
        assert "network_mesh" in cmap

    def test_env_override_core_engine(self, monkeypatch, fake_redis):
        monkeypatch.setenv("CONTAINER_CORE_ENGINE", "my_custom_core")
        # 모듈 레벨 함수를 직접 호출해 새 맵 생성
        from agents.cassiopeia_agent.system_executor import _build_container_map
        cmap = _build_container_map()
        assert cmap["core_engine"] == "my_custom_core"

    def test_env_override_network_mesh(self, monkeypatch):
        monkeypatch.setenv("CONTAINER_NETWORK_MESH", "my_network")
        from agents.cassiopeia_agent.system_executor import _build_container_map
        cmap = _build_container_map()
        assert cmap["network_mesh"] == "my_network"


# ══════════════════════════════════════════════════════════════════════════════
# 2. 제어 명령 실행 (_execute_control)
# ══════════════════════════════════════════════════════════════════════════════

class TestExecuteControl:
    async def test_restart_core_engine_calls_container_restart(
        self, executor_with_docker, mock_docker_client
    ):
        container_name = executor_with_docker._container_map["core_engine"]
        await executor_with_docker._execute_control("restart", "core_engine")
        # 캐시에서 동일 Mock 인스턴스 가져오기
        container = mock_docker_client._cache[container_name]
        container.restart.assert_called_once()

    async def test_restart_all_calls_restart_for_every_container(
        self, executor_with_docker, mock_docker_client
    ):
        await executor_with_docker._execute_control("restart", "all")
        for name in executor_with_docker._container_map.values():
            assert mock_docker_client._cache[name].restart.called, \
                f"{name}.restart 가 호출되지 않았습니다"

    async def test_terminate_stops_container(
        self, executor_with_docker, mock_docker_client
    ):
        container_name = executor_with_docker._container_map["core_engine"]
        await executor_with_docker._execute_control("terminate", "core_engine")
        container = mock_docker_client._cache[container_name]
        container.stop.assert_called_once()

    async def test_optimize_restarts_when_memory_over_85pct(
        self, executor_with_docker, mock_docker_client
    ):
        """usage = 86% → 재시작 발생."""
        container_name = executor_with_docker._container_map["core_engine"]
        # 먼저 get() 으로 캐시에 등록
        mock_docker_client.containers.get(container_name)
        container = mock_docker_client._cache[container_name]
        container.stats = MagicMock(return_value={
            "memory_stats": {
                "usage": int(512 * 1024 * 1024 * 0.86),
                "limit": 512 * 1024 * 1024,
            }
        })

        await executor_with_docker._execute_control("optimize", "core_engine")
        container.restart.assert_called_once()

    async def test_optimize_skips_restart_when_memory_below_85pct(
        self, executor_with_docker, mock_docker_client
    ):
        """usage = 39% → 재시작 없음."""
        container_name = executor_with_docker._container_map["core_engine"]
        mock_docker_client.containers.get(container_name)
        container = mock_docker_client._cache[container_name]
        container.stats = MagicMock(return_value={
            "memory_stats": {
                "usage": 200 * 1024 * 1024,
                "limit": 512 * 1024 * 1024,
            }
        })

        await executor_with_docker._execute_control("optimize", "core_engine")
        container.restart.assert_not_called()

    async def test_no_docker_client_raises_runtime_error(self, executor):
        """Docker 소켓 없을 때 RuntimeError 를 raise 해야 함."""
        # _docker_client = None, get_docker_client 도 None 반환하도록 패치
        with patch.object(executor, "_get_docker_client", return_value=None):
            with pytest.raises(RuntimeError, match="docker.sock"):
                await executor._execute_control("restart", "core_engine")


# ══════════════════════════════════════════════════════════════════════════════
# 3. 복구 명령 실행 (_execute_repair)
# ══════════════════════════════════════════════════════════════════════════════

class TestExecuteRepair:
    async def test_hotfix_restarts_container(
        self, executor_with_docker, mock_docker_client
    ):
        container_name = executor_with_docker._container_map["core_engine"]
        await executor_with_docker._execute_repair("core_engine", "hotfix")
        container = mock_docker_client._cache[container_name]
        container.restart.assert_called_once()

    async def test_full_reinstall_pulls_image_then_restarts(
        self, executor_with_docker, mock_docker_client
    ):
        container_name = executor_with_docker._container_map["network_mesh"]
        # 캐시 사전 등록 + image name 확보
        mock_docker_client.containers.get(container_name)
        container = mock_docker_client._cache[container_name]
        image_name = container.attrs["Config"]["Image"]

        await executor_with_docker._execute_repair("network_mesh", "full_reinstall")

        mock_docker_client.images.pull.assert_called_once_with(image_name)
        container.restart.assert_called_once()

    async def test_no_docker_client_raises_runtime_error(self, executor):
        with patch.object(executor, "_get_docker_client", return_value=None):
            with pytest.raises(RuntimeError):
                await executor._execute_repair("core_engine", "hotfix")


# ══════════════════════════════════════════════════════════════════════════════
# 4. _handle_control — Redis 상태 업데이트
# ══════════════════════════════════════════════════════════════════════════════

class TestHandleControl:
    async def test_success_stores_completed_status(
        self, executor_with_docker, fake_redis
    ):
        cmd = {
            "command_id": "cmd-test-001",
            "action": "restart",
            "target": "core_engine",
        }
        await executor_with_docker._handle_control(cmd)

        status = await fake_redis.hget("system:control:cmd-test-001", "status")
        assert status == "completed"

    async def test_failure_stores_failed_status_and_error(
        self, executor_with_docker, fake_redis, mock_docker_client
    ):
        mock_docker_client.containers.get.side_effect = Exception("container not found")
        cmd = {
            "command_id": "cmd-test-002",
            "action": "restart",
            "target": "core_engine",
        }
        await executor_with_docker._handle_control(cmd)

        status = await fake_redis.hget("system:control:cmd-test-002", "status")
        error = await fake_redis.hget("system:control:cmd-test-002", "error")
        assert status == "failed"
        assert "container not found" in error


# ══════════════════════════════════════════════════════════════════════════════
# 5. _handle_repair — Redis 상태 업데이트
# ══════════════════════════════════════════════════════════════════════════════

class TestHandleRepair:
    async def _seed_repair(self, fake_redis, repair_id: str, module_id: str) -> None:
        await fake_redis.hset(f"system:repair:{repair_id}", mapping={
            "status": "in_progress",
            "module_id": module_id,
            "repair_type": "hotfix",
        })

    async def test_success_updates_status_to_completed(
        self, executor_with_docker, fake_redis
    ):
        repair_id = "rep-aabbcc"
        await self._seed_repair(fake_redis, repair_id, "core_engine")

        cmd = {"repair_id": repair_id, "module_id": "core_engine", "repair_type": "hotfix"}
        await executor_with_docker._handle_repair(cmd)

        status = await fake_redis.hget(f"system:repair:{repair_id}", "status")
        assert status == "completed"

    async def test_failure_updates_status_to_failed(
        self, executor_with_docker, fake_redis, mock_docker_client
    ):
        repair_id = "rep-xxyyzz"
        await self._seed_repair(fake_redis, repair_id, "core_engine")
        mock_docker_client.containers.get.side_effect = Exception("no such container")

        cmd = {"repair_id": repair_id, "module_id": "core_engine", "repair_type": "hotfix"}
        await executor_with_docker._handle_repair(cmd)

        status = await fake_redis.hget(f"system:repair:{repair_id}", "status")
        assert status == "failed"

    async def test_completed_has_timestamp(
        self, executor_with_docker, fake_redis
    ):
        repair_id = "rep-ts-001"
        await self._seed_repair(fake_redis, repair_id, "network_mesh")

        cmd = {"repair_id": repair_id, "module_id": "network_mesh", "repair_type": "hotfix"}
        await executor_with_docker._handle_repair(cmd)

        completed_at = await fake_redis.hget(f"system:repair:{repair_id}", "completed_at")
        assert completed_at is not None


# ══════════════════════════════════════════════════════════════════════════════
# 6. run() — BLPOP 큐 소비 통합
# ══════════════════════════════════════════════════════════════════════════════

class TestRunLoop:
    """
    _process_once() 를 직접 호출하여 큐 소비 동작을 검증합니다.
    asyncio.wait_for + asyncio.to_thread 의 타이밍 불안정성을 피합니다.
    """

    async def test_process_once_consumes_control_command(
        self, executor_with_docker, fake_redis
    ):
        """_process_once() 호출 후 control 큐가 비어야 함."""
        cmd = json.dumps({
            "command_id": "cmd-run-001",
            "action": "restart",
            "target": "core_engine",
        })
        await fake_redis.rpush("system:ops:pending", cmd)

        processed = await executor_with_docker._process_once(timeout=1)

        assert processed is True
        remaining = await fake_redis.llen("system:ops:pending")
        assert remaining == 0

    async def test_process_once_consumes_repair_command(
        self, executor_with_docker, fake_redis
    ):
        """_process_once() 호출 후 repair 큐가 비어야 함."""
        repair_id = "rep-run-001"
        await fake_redis.hset(f"system:repair:{repair_id}", mapping={
            "status": "in_progress",
            "module_id": "core_engine",
        })
        cmd = json.dumps({
            "repair_id": repair_id,
            "module_id": "core_engine",
            "repair_type": "hotfix",
        })
        await fake_redis.rpush("system:repair:pending", cmd)

        processed = await executor_with_docker._process_once(timeout=1)

        assert processed is True
        remaining = await fake_redis.llen("system:repair:pending")
        assert remaining == 0

    async def test_process_once_returns_false_when_queue_empty(
        self, executor_with_docker
    ):
        """빈 큐에서 _process_once() 는 False 를 반환해야 함."""
        processed = await executor_with_docker._process_once(timeout=0)
        assert processed is False

    async def test_run_loop_exits_on_cancelled_error(self, executor_with_docker):
        """run() 루프가 CancelledError 를 받으면 깔끔하게 종료되어야 함."""
        # _process_once 를 asyncio.sleep(0) 으로 대체해 즉시 반환
        async def _noop_process(timeout=5):
            await asyncio.sleep(0)
            return False

        executor_with_docker._process_once = _noop_process

        task = asyncio.create_task(executor_with_docker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        assert task.done()


# ══════════════════════════════════════════════════════════════════════════════
# 7. API → 큐 연결 통합 (admin_router → Redis pending 큐)
# ══════════════════════════════════════════════════════════════════════════════

class TestApiPushesToPendingQueue:
    """POST /admin/system/control 과 /repair 가 pending 큐에 명령을 넣는지 확인."""

    async def test_system_control_pushes_to_pending_queue(
        self, async_client, fake_redis
    ):
        await async_client.post(
            "/admin/system/control",
            json={"action": "restart", "target": "core_engine"},
        )
        length = await fake_redis.llen("system:ops:pending")
        assert length >= 1
        raw = await fake_redis.lindex("system:ops:pending", 0)
        cmd = json.loads(raw)
        assert cmd["action"] == "restart"
        assert cmd["target"] == "core_engine"
        assert "command_id" in cmd

    async def test_system_repair_pushes_to_pending_queue(
        self, async_client, fake_redis
    ):
        resp = await async_client.post(
            "/admin/system/repair",
            json={"module_id": "network_mesh", "repair_type": "full_reinstall"},
        )
        repair_id = resp.json()["repair_id"]
        length = await fake_redis.llen("system:repair:pending")
        assert length >= 1
        raw = await fake_redis.lindex("system:repair:pending", 0)
        cmd = json.loads(raw)
        assert cmd["repair_id"] == repair_id
        assert cmd["module_id"] == "network_mesh"
        assert cmd["repair_type"] == "full_reinstall"
