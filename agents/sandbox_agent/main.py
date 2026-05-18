from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends, status

# 에이전트 내부 패키지 임포트 (python -m agents.sandbox_agent.main 실행 시 작동)
from .sandbox.pool import VMPool
from .sandbox.models import ExecuteRequest, SandboxRuntime

load_dotenv(encoding="utf-8", override=True)

from shared_core.agent_logger import setup_logging
setup_logging()

logger = logging.getLogger("sandbox_agent.main")

# ── Authentication ─────────────────────────────────────────────────────────────

async def verify_sandbox_key(x_sandbox_api_key: str = Header(None)):
    """X-Sandbox-API-Key 헤더를 통해 요청을 인증합니다. Redis Hash 'sandbox:keys'를 확인합니다."""
    if not x_sandbox_api_key:
        raise HTTPException(status_code=401, detail="Missing API Key")

    # 1. 정적 키 확인 (환경변수)
    raw_key = os.environ.get("SANDBOX_API_KEY")
    expected_key = raw_key.strip("\"'") if raw_key else None
    
    if expected_key and x_sandbox_api_key == expected_key:
        return
    
    # 2. Redis 동적 키 확인
    if _ctx.redis:
        is_valid = await _ctx.redis.hexists("sandbox:keys", x_sandbox_api_key)
        if is_valid:
            return

    logger.warning("인증 실패: 잘못된 API Key 수신")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid Sandbox API Key"
    )

# ── Pydantic 요청/응답 ────────────────────────────────────────────────────────

from pydantic import BaseModel

class DispatchExecuteRequest(BaseModel):
    """SandboxClient로부터 수신하는 표준 요청 구조."""
    task_id: str
    params: ExecuteRequest

# ── Application Context ────────────────────────────────────────────────────────

class _AppContext:
    pool: VMPool | None = None
    runtime: str = "docker"
    redis: aioredis.Redis | None = None

_ctx = _AppContext()

def _detect_runtime() -> SandboxRuntime:
    forced = os.environ.get("SANDBOX_RUNTIME")
    if forced in ("firecracker", "gvisor", "docker"):
        return forced  # type: ignore

    # 1. Firecracker 시도 (KVM 필요)
    if os.path.exists("/dev/kvm"):
        return "firecracker"

    # 2. gVisor 시도 (Docker 런타임에 runsc가 등록되어 있는지 환경변수로 힌트 확인 가능)
    # 실제 운영환경에서는 'docker info' 등으로 확인해야 하나, 여기서는 자동 선택 로직으로 gvisor를 우선 고려
    if os.environ.get("PREFER_GVISOR", "true").lower() == "true":
        # 사용자가 gVisor 설치를 마쳤다고 가정하고 보안을 위해 우선 선택
        return "gvisor"

    return "docker"

# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _ctx.runtime = _detect_runtime()
    logger.info("[Lifespan] Sandbox Agent 시작 (runtime=%s)", _ctx.runtime)
    
    # Redis 연결
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    _ctx.redis = aioredis.from_url(redis_url, decode_responses=True)
    
    _ctx.pool = VMPool(_ctx.runtime)
    await _ctx.pool.start()
    
    yield
    
    logger.info("[Lifespan] Sandbox Agent 종료")
    if _ctx.pool:
        await _ctx.pool.shutdown()
    if _ctx.redis:
        await _ctx.redis.aclose()

app = FastAPI(title="Sandbox Agent", lifespan=lifespan)

# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    if not _ctx.pool:
        return {"status": "initializing"}
    return {
        "status": "ok",
        "runtime": _ctx.runtime,
        "pool": _ctx.pool.stats()
    }

@app.post("/execute", dependencies=[Depends(verify_sandbox_key)])
async def execute(req: DispatchExecuteRequest):
    if not _ctx.pool:
        raise HTTPException(status_code=503, detail="Sandbox pool not ready")

    params = req.params
    start_ms = time.monotonic()
    
    logger.info("[Execute] 태스크 수신: task_id=%s, language=%s", req.task_id, params.language)
    
    try:
        vm = await _ctx.pool.acquire()
    except Exception as exc:
        logger.error("[Execute] VM 획득 실패: %s", exc)
        return {
            "status": "FAILED",
            "error": {"code": "VM_ACQUIRE_FAILED", "message": str(exc)}
        }

    try:
        result = await vm.execute(params)
        elapsed_ms = int((time.monotonic() - start_ms) * 1000)
        
        return {
            "status": "SUCCESS",
            "result_data": {
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "exit_code": result["exit_code"],
                "runtime_used": result["runtime_used"],
                "execution_time_ms": elapsed_ms
            }
        }
    except Exception as exc:
        logger.error("[Execute] 실행 오류: %s", exc)
        return {
            "status": "FAILED",
            "error": {"code": "EXECUTION_ERROR", "message": str(exc)}
        }
    finally:
        await _ctx.pool.release(vm)

# ── 실행 로직 ──────────────────────────────────────────────────────────────────

async def run_cli():
    """CLI 모드: 환경 변수에서 페이로드를 읽어 1회 실행 후 종료"""
    payload_json = os.environ.get("SANDBOX_PAYLOAD")
    if not payload_json:
        logger.error("[CLI] SANDBOX_PAYLOAD 환경 변수가 없습니다.")
        return

    try:
        import json
        data = json.loads(payload_json)
        req = DispatchExecuteRequest.model_validate(data)
        
        # CLI 모드에서는 풀을 사용하지 않고 즉시 생성
        from .sandbox.docker_sandbox import DockerSandbox
        from .sandbox.firecracker import FirecrackerSandbox
        
        runtime = _detect_runtime()
        vm = FirecrackerSandbox() if runtime == "firecracker" else DockerSandbox()
        if runtime == "firecracker":
            await vm.start()
            
        logger.info("[CLI] 실행 시작: task_id=%s", req.task_id)
        result = await vm.execute(req.params)
        await vm.close()
        
        # 결과를 stdout으로 출력 (카시오페아가 캡처)
        print(json.dumps({
            "status": "SUCCESS",
            "result_data": result
        }, ensure_ascii=False))
        
    except Exception as exc:
        logger.error("[CLI] 실행 실패: %s", exc)
        import json
        print(json.dumps({
            "status": "FAILED",
            "error": {"code": "CLI_EXECUTION_ERROR", "message": str(exc)}
        }))

def main():
    mode = os.environ.get("SANDBOX_MODE", "server").lower()
    
    if mode == "cli":
        asyncio.run(run_cli())
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8003)

if __name__ == "__main__":
    main()
