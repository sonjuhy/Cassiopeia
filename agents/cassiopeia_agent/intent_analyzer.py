"""
LLM 기반 사용자 의도 분석기
- shared_core.llm 공급자 기반 (Gemini, Claude, Local 지원)
- 사용자 자연어 입력 → AgentMessage 리스트 변환
- python-strict-typing 전략: Protocol 기반 다형성
"""

from __future__ import annotations

import asyncio
import json
import os
import warnings
from typing import Any, Protocol

from shared_core.llm import LLMGenerateOptions, LLMProviderProtocol, build_llm_provider
from shared_core.messaging import AgentMessage, AgentName

_FALLBACK_RECEIVER: AgentName = "planning"

_SYSTEM_PROMPT_TEMPLATE = """당신은 사용자의 자연어 요청을 분석하여 처리할 에이전트와 작업을 결정하는 카시오페아 라우터입니다.

사용 가능한 에이전트 목록:
{agents_description}

규칙:
1. 사용자 요청을 분석하여 필요한 에이전트 작업 목록을 JSON 배열로 반환하세요.
2. 반드시 다음 JSON 형식만 출력하세요 (설명 없이 JSON만):
[
  {{
    "receiver": "에이전트_이름",
    "action": "작업_이름",
    "payload": {{
      "key": "value"
    }}
  }}
]
3. receiver는 반드시 위 목록에 있는 에이전트 이름 중 하나여야 합니다.
4. action은 해당 에이전트가 수행할 작업을 snake_case로 표현하세요. (예: process_task, send_notification)
5. payload에는 작업에 필요한 상세 정보를 포함하세요.
6. 요청 처리에 여러 에이전트가 필요하면 여러 항목을 반환하세요.
"""


def _build_system_prompt(capabilities: dict[AgentName, str]) -> str:
    agents_description = "\n".join(
        f"- {name}: {desc}" for name, desc in capabilities.items()
    )
    return _SYSTEM_PROMPT_TEMPLATE.format(agents_description=agents_description)


def _parse_agent_messages(
    raw: str,
    sender: AgentName,
    valid_receivers: set[AgentName],
) -> list[AgentMessage]:
    """LLM 응답 JSON을 파싱하여 AgentMessage 리스트로 변환합니다."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data: list[dict[str, Any]] = json.loads(text)
        messages: list[AgentMessage] = []

        for item in data:
            receiver = item.get("receiver", _FALLBACK_RECEIVER)
            if receiver not in valid_receivers:
                receiver = _FALLBACK_RECEIVER
            messages.append(
                AgentMessage(
                    sender=sender,
                    receiver=receiver,
                    action=item.get("action", "process_request"),
                    payload=item.get("payload", {}),
                )
            )
        return messages if messages else _fallback_messages(sender)

    except (json.JSONDecodeError, TypeError, KeyError):
        return _fallback_messages(sender)


def _fallback_messages(sender: AgentName) -> list[AgentMessage]:
    return [
        AgentMessage(
            sender=sender,
            receiver=_FALLBACK_RECEIVER,
            action="process_request",
            payload={},
        )
    ]


class IntentAnalyzerProtocol(Protocol):
    """사용자 입력을 분석하여 AgentMessage 리스트를 반환하는 추상 인터페이스."""

    async def analyze(
        self,
        user_input: str,
        capabilities: dict[AgentName, str],
    ) -> list[AgentMessage]:
        """
        사용자 입력을 분석하여 각 에이전트에게 전달할 메시지 목록을 생성합니다.

        Args:
            user_input: 사용자의 자연어 입력.
            capabilities: 등록된 에이전트 이름 → 역할 설명 매핑.

        Returns:
            각 에이전트에게 전송할 AgentMessage 리스트.
        """
        ...


class LLMIntentAnalyzer:
    """
    LLMProviderProtocol을 사용하는 의도 분석기.
    공급자 종류(Gemini, Claude, Local)에 무관하게 동일하게 작동합니다.

    환경 변수:
        LLM_BACKEND: "gemini" | "claude" | "local" (기본값: gemini)
    """

    def __init__(self, provider: LLMProviderProtocol | None = None) -> None:
        self._provider = provider or build_llm_provider()

    async def analyze(
        self,
        user_input: str,
        capabilities: dict[AgentName, str],
    ) -> list[AgentMessage]:
        system_prompt = _build_system_prompt(capabilities)
        raw, _ = await self._provider.generate_response(
            prompt=user_input,
            system_instruction=system_prompt,
            options=LLMGenerateOptions(max_tokens=1024),
        )
        return _parse_agent_messages(raw, "cassiopeia", set(capabilities.keys()))


# ── 레거시 별칭 ──────────────────────────────────────────────────────────────────
# 기존 코드와의 호환성을 위해 유지합니다.

class ClaudeAPIIntentAnalyzer(LLMIntentAnalyzer):
    """레거시 별칭. LLMIntentAnalyzer(ClaudeProvider)를 사용하세요."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001") -> None:
        from shared_core.llm import ClaudeProvider
        super().__init__(provider=ClaudeProvider(model=model))


class GeminiAPIIntentAnalyzer(LLMIntentAnalyzer):
    """레거시 별칭. LLMIntentAnalyzer(GeminiProvider)를 사용하세요."""

    def __init__(self, model: str = "gemini-2.5-flash") -> None:
        from shared_core.llm import GeminiProvider
        super().__init__(provider=GeminiProvider(model=model))


class ClaudeCLIIntentAnalyzer:
    """
    레거시 subprocess 기반 구현체.
    대신 LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL 환경변수를 사용하세요.
    """

    def __init__(self) -> None:
        warnings.warn(
            "ClaudeCLIIntentAnalyzer는 deprecated입니다. "
            "LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL을 사용하세요.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._prompt_prefix = ""

    async def analyze(
        self,
        user_input: str,
        capabilities: dict[AgentName, str],
    ) -> list[AgentMessage]:
        full_prompt = (
            f"{_build_system_prompt(capabilities)}\n\n사용자 요청: {user_input}"
        )
        proc = await asyncio.create_subprocess_exec(
            "claude",
            "-p",
            full_prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            print(f"[intent_analyzer] Claude CLI 오류: {stderr.decode()}")
            return _fallback_messages("cassiopeia")
        return _parse_agent_messages(
            stdout.decode().strip(), "cassiopeia", set(capabilities.keys())
        )


class GeminiCLIIntentAnalyzer:
    """
    레거시 subprocess 기반 구현체.
    대신 LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL 환경변수를 사용하세요.
    """

    def __init__(self) -> None:
        warnings.warn(
            "GeminiCLIIntentAnalyzer는 deprecated입니다. "
            "LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL을 사용하세요.",
            DeprecationWarning,
            stacklevel=2,
        )

    async def analyze(
        self,
        user_input: str,
        capabilities: dict[AgentName, str],
    ) -> list[AgentMessage]:
        full_prompt = (
            f"{_build_system_prompt(capabilities)}\n\n사용자 요청: {user_input}"
        )
        proc = await asyncio.create_subprocess_exec(
            "gemini",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=full_prompt.encode())
        if proc.returncode != 0:
            print(f"[intent_analyzer] Gemini CLI 오류: {stderr.decode()}")
            return _fallback_messages("cassiopeia")
        return _parse_agent_messages(
            stdout.decode().strip(), "cassiopeia", set(capabilities.keys())
        )
