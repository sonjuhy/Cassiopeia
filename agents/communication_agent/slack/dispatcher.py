"""
Docker 컨테이너 디스패처
- docker run --rm 으로 에이전트 컨테이너를 단발성 실행합니다.
- ephemeral-docker-ops 전략: 작업 완료 즉시 컨테이너 제거
"""

import asyncio
import os

from ..models import AgentName, ExecutionResult, SlackEvent

# 에이전트 서비스 이름 → Docker 이미지 이름 매핑
#
# [우선순위 - 높은 것부터]
# 1. 환경변수 AGENT_IMAGE_{AGENT_NAME_UPPER}  (예: AGENT_IMAGE_ARCHIVE_AGENT=myrepo/archive_agent:latest)
# 2. 컨벤션 기반 이름: "agentmonorepo-{agent_name}" (docker-compose 기본 네이밍 규칙)
#
# 하드코딩된 매핑은 없습니다. 새 에이전트를 추가할 때 이 파일을 수정할 필요가 없습니다.
# COMM_AGENT_REGISTRY 에 등록된 에이전트 이름이 자동으로 컨벤션 기반 이미지명을 사용합니다.
_DEFAULT_IMAGE_MAP: dict[str, str] = {}

# 에이전트에 전달할 환경변수 키 목록 (호스트 환경변수에서 자동 전달)
_PASSTHROUGH_ENV_KEYS: list[str] = [
    "NOTION_TOKEN",
    "NOTION_DATABASE_ID",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "SLACK_WEBHOOK_URL",
]


def _resolve_image(agent_name: AgentName) -> str:
    """에이전트 이름에 해당하는 Docker 이미지명을 반환합니다.

    우선순위:
    1. 환경변수 AGENT_IMAGE_{AGENT_NAME_UPPER} (예: AGENT_IMAGE_ARCHIVE_AGENT)
    2. 컨벤션 기반 이름: agentmonorepo-{agent_name}
    """
    env_key = f"AGENT_IMAGE_{agent_name.upper()}"
    return os.environ.get(env_key) or _DEFAULT_IMAGE_MAP.get(agent_name) or f"agentmonorepo-{agent_name}"


def _build_env_args(event: SlackEvent) -> list[str]:
    """docker run 에 전달할 --env 인수 목록을 구성합니다."""
    slack_env: dict[str, str] = {
        "SLACK_MESSAGE_TEXT": event["text"],
        "SLACK_MESSAGE_USER": event["user"],
        "SLACK_MESSAGE_CHANNEL": event["channel"],
        "SLACK_MESSAGE_TS": event["ts"],
        "SLACK_MESSAGE_THREAD_TS": event["thread_ts"] or "",
    }

    # 호스트 환경변수 전달 (값이 있는 것만)
    for key in _PASSTHROUGH_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            slack_env[key] = value

    return [f"--env={k}={v}" for k, v in slack_env.items()]


class DockerDispatcher:
    """
    에이전트를 docker run --rm 으로 실행하는 디스패처.
    컨테이너는 백그라운드에서 실행되며, 실행 완료 후 자동 제거됩니다.
    호스트의 /var/run/docker.sock 마운트가 필요합니다.
    """

    async def dispatch(
        self, agent_name: AgentName, event: SlackEvent
    ) -> ExecutionResult:
        """
        지정된 에이전트 컨테이너를 Slack 이벤트 컨텍스트와 함께 실행합니다.

        Args:
            agent_name (AgentName): 실행할 에이전트 이름 (AGENT_REGISTRY 기준).
            event (SlackEvent): 에이전트에 전달할 Slack 메시지 이벤트.

        Returns:
            ExecutionResult: (성공 여부, 처리 결과 메시지)
        """
        image = _resolve_image(agent_name)

        env_args = _build_env_args(event)
        cmd = ["docker", "run", "--rm", "--detach", *env_args, image]

        print(f"[dispatcher] 실행: {agent_name} ({image})")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

            if proc.returncode != 0:
                return (False, f"컨테이너 실행 실패: {stderr.decode().strip()}")

            container_id = stdout.decode().strip()[:12]
            return (True, f"{agent_name} 컨테이너 시작됨 (id: {container_id})")

        except asyncio.TimeoutError:
            return (False, "docker run 타임아웃")
        except FileNotFoundError:
            return (False, "docker CLI를 찾을 수 없습니다. 경로를 확인하세요.")
        except Exception as e:
            return (False, f"디스패치 실패: {e}")
