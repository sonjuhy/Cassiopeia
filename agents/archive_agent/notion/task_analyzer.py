"""
기획 에이전트 작업 세분화 인터페이스 및 구현
- shared_core.llm 공급자 기반 (Gemini, Claude, Local 지원)
- python-strict-typing 전략: 엄격한 정적 타입 선언 및 추상 인터페이스
"""

from __future__ import annotations

import os
import warnings
from typing import Protocol

from shared_core.llm import LLMGenerateOptions, LLMProviderProtocol, build_llm_provider

from ..models import ParsedTask


_SYSTEM_PROMPT = """당신은 소프트웨어 기획 전문가입니다.
주어진 태스크를 분석하여 다음 구조의 마크다운 문서를 작성하세요.

## 1. 목표
이 태스크가 달성하려는 목적과 기대 효과를 명확히 기술하세요.

## 2. 과정
구현을 위한 단계별 처리 흐름을 기술하세요.

## 3. 결과

### 기능
구현될 기능 목록을 나열하세요.

### 기능들의 조립도
컴포넌트/모듈 아키텍처 및 연결 구조를 기술하세요.

### 출력
최종 결과물 형태 및 제약사항을 기술하세요.

반드시 한국어로 작성하고, 마크다운 형식을 준수하세요."""


def _build_prompt(task: ParsedTask) -> str:
    parts = [f"# 태스크: {task['title']}"]
    if task.get("description"):
        parts.append(f"\n## 목적\n{task['description']}")
    if task.get("task_type"):
        parts.append(f"\n**타입**: {task['task_type']}")
    if task.get("priority"):
        parts.append(f"\n**우선순위**: {task['priority']}")
    return "\n".join(parts)


class TaskAnalyzerProtocol(Protocol):
    """
    Notion 태스크를 입력받아 세분화된 마크다운 문서로 변환하는 추상 인터페이스입니다.
    """

    async def analyze_task(self, task: ParsedTask) -> str:
        """
        주어진 기획 태스크를 세분화하여 마크다운 문자열로 반환합니다.

        Args:
            task (ParsedTask): 파싱 완료된 작업 데이터.

        Returns:
            str: 생성된 마크다운 문서.
        """
        ...


class LLMTaskAnalyzer:
    """
    LLMProviderProtocol을 사용하는 태스크 분석기.
    공급자 종류(Gemini, Claude, Local)에 무관하게 동일하게 작동합니다.

    환경 변수:
        LLM_BACKEND: "gemini" | "claude" | "local" (기본값: gemini)
    """

    def __init__(self, provider: LLMProviderProtocol | None = None) -> None:
        self._provider = provider or build_llm_provider()

    async def analyze_task(self, task: ParsedTask) -> str:
        raw, _ = await self._provider.generate_response(
            prompt=_build_prompt(task),
            system_instruction=_SYSTEM_PROMPT,
            options=LLMGenerateOptions(max_tokens=4096),
        )
        return raw


class ClaudeThinkingTaskAnalyzer:
    """
    Claude 확장 사고(Extended Thinking) 스트리밍을 사용하는 태스크 분석기.
    TASK_ANALYZER_BACKEND=claude_thinking 으로 선택됩니다.

    Anthropic SDK의 고급 기능(thinking + streaming)이므로
    shared_core.llm 공급자로 추상화하지 않고 직접 사용합니다.
    """

    def __init__(self, model: str = "claude-opus-4-6") -> None:
        import anthropic
        self._client = anthropic.AsyncAnthropic()
        self._model = model

    async def analyze_task(self, task: ParsedTask) -> str:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=4096,
            thinking={"type": "enabled", "budget_tokens": 2048},
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_prompt(task)}],
        ) as stream:
            final = await stream.get_final_message()
        return next(b.text for b in final.content if b.type == "text")


# ── 레거시 별칭 ──────────────────────────────────────────────────────────────────
# 기존 코드와의 호환성을 위해 유지합니다.

class ClaudeAPITaskAnalyzer(LLMTaskAnalyzer):
    """레거시 별칭. LLMTaskAnalyzer(ClaudeProvider)를 사용하세요."""

    def __init__(self, model: str = "claude-opus-4-6") -> None:
        from shared_core.llm import ClaudeProvider
        super().__init__(provider=ClaudeProvider(model=model))


class GeminiAPITaskAnalyzer(LLMTaskAnalyzer):
    """레거시 별칭. LLMTaskAnalyzer(GeminiProvider)를 사용하세요."""

    def __init__(self, model: str = "gemini-2.5-flash") -> None:
        from shared_core.llm import GeminiProvider
        super().__init__(provider=GeminiProvider(model=model))


class ClaudeCLITaskAnalyzer:
    """
    레거시 subprocess 기반 구현체.
    대신 LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL 환경변수를 사용하세요.
    """

    def __init__(self) -> None:
        warnings.warn(
            "ClaudeCLITaskAnalyzer는 deprecated입니다. "
            "LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL을 사용하세요.",
            DeprecationWarning,
            stacklevel=2,
        )

    async def analyze_task(self, task: ParsedTask) -> str:
        import asyncio
        prompt = f"{_SYSTEM_PROMPT}\n\n{_build_prompt(task)}"
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Claude CLI 오류: {stderr.decode()}")
        return stdout.decode().strip()


class GeminiCLITaskAnalyzer:
    """
    레거시 subprocess 기반 구현체.
    대신 LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL 환경변수를 사용하세요.
    """

    def __init__(self) -> None:
        warnings.warn(
            "GeminiCLITaskAnalyzer는 deprecated입니다. "
            "LLM_BACKEND=local 과 LOCAL_LLM_BASE_URL을 사용하세요.",
            DeprecationWarning,
            stacklevel=2,
        )

    async def analyze_task(self, task: ParsedTask) -> str:
        import asyncio
        prompt = f"{_SYSTEM_PROMPT}\n\n{_build_prompt(task)}"
        proc = await asyncio.create_subprocess_exec(
            "gemini",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=prompt.encode())
        if proc.returncode != 0:
            raise RuntimeError(f"Gemini CLI 오류: {stderr.decode()}")
        return stdout.decode().strip()


def build_task_analyzer(backend: str | None = None) -> TaskAnalyzerProtocol:
    """
    환경변수 TASK_ANALYZER_BACKEND에 따라 적절한 TaskAnalyzer를 반환합니다.

    백엔드 선택:
        claude (기본):      LLMTaskAnalyzer(ClaudeProvider) — ANTHROPIC_API_KEY 필요
        claude_thinking:    ClaudeThinkingTaskAnalyzer — Extended Thinking, ANTHROPIC_API_KEY 필요
        gemini:             LLMTaskAnalyzer(GeminiProvider) — GEMINI_API_KEY 필요
        local:              LLMTaskAnalyzer(LocalProvider) — LOCAL_LLM_BASE_URL 필요
        claude_cli:         [deprecated] ClaudeCLITaskAnalyzer
        gemini_cli:         [deprecated] GeminiCLITaskAnalyzer
    """
    selected = backend or os.environ.get("TASK_ANALYZER_BACKEND", "claude")
    match selected:
        case "claude_thinking":
            return ClaudeThinkingTaskAnalyzer()
        case "gemini":
            return LLMTaskAnalyzer(provider=build_llm_provider(backend="gemini"))
        case "local":
            return LLMTaskAnalyzer(provider=build_llm_provider(backend="local"))
        case "claude_cli":
            return ClaudeCLITaskAnalyzer()
        case "gemini_cli":
            return GeminiCLITaskAnalyzer()
        case _:  # "claude" 기본값
            return LLMTaskAnalyzer(provider=build_llm_provider(backend="claude"))
