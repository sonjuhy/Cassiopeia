"""
LLM 기반 에이전트 라우터/분류기
- shared_core.llm 공급자 기반 (Gemini, Claude, Local 지원)
- python-strict-typing 전략: Protocol 기반 다형성
"""

from __future__ import annotations

import asyncio
import os
import warnings
from typing import Protocol

from shared_core.llm import LLMGenerateOptions, LLMProviderProtocol, build_llm_provider

from ..models import AgentName, SlackEvent, AGENT_REGISTRY

# 폴백 에이전트 — 환경변수 CLASSIFIER_FALLBACK_AGENT 로 재정의 가능합니다.
# 하드코딩을 피하고, 어떤 에이전트 이름도 코드 수정 없이 변경할 수 있습니다.
_FALLBACK_AGENT: str = os.environ.get("CLASSIFIER_FALLBACK_AGENT", "archive_agent")

_SYSTEM_PROMPT_TEMPLATE = """당신은 사용자 메시지를 분석하여 처리할 에이전트를 선택하는 라우터입니다.

사용 가능한 에이전트 목록:
{agents_description}

규칙:
1. 메시지 내용을 분석하여 가장 적합한 에이전트 이름 하나만 반환하세요.
2. 반드시 위 목록에 있는 에이전트 이름 중 하나만 출력하세요.
3. 설명 없이 에이전트 이름만 출력하세요. (예: archive_agent)
4. 판단하기 어려우면 폴백 에이전트(CLASSIFIER_FALLBACK_AGENT)를 반환하세요."""


def _build_system_prompt(agent_registry: dict[str, str]) -> str:
    fallback = os.environ.get("CLASSIFIER_FALLBACK_AGENT", "archive_agent")
    agents_description = "\n".join(
        f"- {name}: {desc}" for name, desc in agent_registry.items()
    ) or f"- {fallback}: 기본 처리 에이전트"
    return _SYSTEM_PROMPT_TEMPLATE.format(agents_description=agents_description)


def _build_user_prompt(event: SlackEvent) -> str:
    return f"다음 Slack 메시지를 처리할 에이전트를 선택하세요:\n\n{event['text']}"


def _parse_agent_name(raw: str, agent_registry: dict[str, str]) -> AgentName:
    """LLM 응답에서 유효한 에이전트 이름을 추출합니다."""
    candidate = raw.strip().lower()
    if candidate in agent_registry:
        return candidate
    for name in agent_registry:
        if name in candidate:
            return name
    return _FALLBACK_AGENT


class ClassifierProtocol(Protocol):
    """메시지를 분석하여 적합한 에이전트 이름을 반환하는 추상 인터페이스"""

    async def classify(self, event: SlackEvent) -> AgentName:
        """
        Slack 이벤트를 분석하여 처리할 에이전트 이름을 반환합니다.

        Args:
            event (SlackEvent): 수신된 Slack 메시지 이벤트.

        Returns:
            AgentName: 에이전트 레지스트리에 등록된 에이전트 이름.
        """
        ...


class LLMClassifier:
    """
    LLMProviderProtocol을 사용하는 에이전트 라우터 분류기.
    공급자 종류(Gemini, Claude, Local)에 무관하게 동일하게 작동합니다.

    환경 변수:
        LLM_BACKEND: "gemini" | "claude" | "local" (기본값: gemini)
        COMM_AGENT_REGISTRY: JSON 형식의 에이전트 레지스트리 (미설정 시 빈 레지스트리)
        CLASSIFIER_FALLBACK_AGENT: LLM 판단 실패 시 반환할 에이전트 이름 (기본값: archive_agent)

    Args:
        provider: LLM 공급자 인스턴스. None 이면 LLM_BACKEND 환경변수로 자동 선택.
        agent_registry: 에이전트 이름 → 역할 설명 매핑.
                        None 이면 COMM_AGENT_REGISTRY 환경변수에서 로드합니다.
    """

    def __init__(
        self,
        provider: LLMProviderProtocol | None = None,
        agent_registry: dict[str, str] | None = None,
    ) -> None:
        self._provider = provider or build_llm_provider()
        # 외부 주입 레지스트리 → COMM_AGENT_REGISTRY 환경변수 → 모델 전역 상수 순으로 참조
        self._agent_registry: dict[str, str] = agent_registry if agent_registry is not None else AGENT_REGISTRY
        self._system_prompt = _build_system_prompt(self._agent_registry)

    async def classify(self, event: SlackEvent) -> AgentName:
        raw, _ = await self._provider.generate_response(
            prompt=_build_user_prompt(event),
            system_instruction=self._system_prompt,
            options=LLMGenerateOptions(max_tokens=64),
        )
        return _parse_agent_name(raw, self._agent_registry)


# ── 레거시 별칭 ──────────────────────────────────────────────────────────────────
# 기존 fastapi_app.py 의 _build_classifier 와의 호환성을 위해 유지합니다.

class ClaudeAPIClassifier(LLMClassifier):
    """레거시 별칭. LLMClassifier(ClaudeProvider)를 사용하세요."""

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        agent_registry: dict[str, str] | None = None,
    ) -> None:
        from shared_core.llm import ClaudeProvider
        super().__init__(provider=ClaudeProvider(model=model), agent_registry=agent_registry)


class GeminiAPIClassifier(LLMClassifier):
    """레거시 별칭. LLMClassifier(GeminiProvider)를 사용하세요."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        agent_registry: dict[str, str] | None = None,
    ) -> None:
        from shared_core.llm import GeminiProvider
        super().__init__(provider=GeminiProvider(model=model), agent_registry=agent_registry)


class ClaudeCLIClassifier:
    """
    레거시 subprocess 기반 구현체.
    대신 LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL 환경변수를 사용하세요.
    """

    def __init__(self, agent_registry: dict[str, str] | None = None) -> None:
        warnings.warn(
            "ClaudeCLIClassifier는 deprecated입니다. "
            "LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL을 사용하세요.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._agent_registry = agent_registry if agent_registry is not None else AGENT_REGISTRY
        self._prompt_prefix = _build_system_prompt(self._agent_registry)

    async def classify(self, event: SlackEvent) -> AgentName:
        full_prompt = f"{self._prompt_prefix}\n\n{_build_user_prompt(event)}"
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", full_prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            print(f"[classifier] Claude CLI 오류: {stderr.decode()}")
            return _FALLBACK_AGENT
        return _parse_agent_name(stdout.decode().strip(), self._agent_registry)


class GeminiCLIClassifier:
    """
    레거시 subprocess 기반 구현체.
    대신 LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL 환경변수를 사용하세요.
    """

    def __init__(self, agent_registry: dict[str, str] | None = None) -> None:
        warnings.warn(
            "GeminiCLIClassifier는 deprecated입니다. "
            "LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL을 사용하세요.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._agent_registry = agent_registry if agent_registry is not None else AGENT_REGISTRY
        self._prompt_prefix = _build_system_prompt(self._agent_registry)

    async def classify(self, event: SlackEvent) -> AgentName:
        full_prompt = f"{self._prompt_prefix}\n\n{_build_user_prompt(event)}"
        proc = await asyncio.create_subprocess_exec(
            "gemini",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=full_prompt.encode())
        if proc.returncode != 0:
            print(f"[classifier] Gemini CLI 오류: {stderr.decode()}")
            return _FALLBACK_AGENT
        return _parse_agent_name(stdout.decode().strip(), self._agent_registry)
