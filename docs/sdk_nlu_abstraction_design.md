# Cassiopeia SDK NLU 추상화 설계 가이드 (v5 - Final Spec)

## 1. 배경 및 목적 (Background & Objectives)

현재 카시오페아(Cassiopeia) 시스템 내에서 개별 에이전트들은 자율성(Autonomy)을 확보하기 위해 내부적으로 LLM을 호출하여 의도를 파악하고 도구를 선택합니다. 
하지만 이 과정에서 다음과 같은 문제점(Anti-patterns)이 지적되었습니다.

1. **결합도 증가 및 철학적 충돌**: 기존 `request_llm` 기반의 Gateway 방식(중앙 API 키 관리)과 에이전트가 직접 LLM을 호출하는 방식이 혼재되어 있습니다.
2. **로직 중복**: 프롬프트 구성, JSON 파싱, 재시도(Self-healing), 보안(인젝션 방어) 로직이 에이전트마다 중복됩니다.
3. **스키마 불일치**: LLM에게 도구를 설명할 때 문자열 리스트(`list[str]`)만 제공하여 정확한 파라미터 추출이 어렵습니다.

**목적**: 이러한 NLU 추론 기능을 `cassiopeia-sdk` 내의 `brain` 모듈로 완벽히 추상화하여, 타입 안정성, 보안 정책, 중앙 통제력을 제공하면서도 개발 생산성을 극대화합니다.

---

## 2. 핵심 아키텍처 의사결정 (Architecture Decisions)

### 2.1. Provider 추상화 (Gateway & Direct 동시 지원)
에이전트 배포 환경에 따라 LLM 호출 경로를 유연하게 선택할 수 있도록 Provider 인터페이스를 도입합니다.
- **`GatewayProvider`**: `AgentBase.request_llm`과 같은 **비동기 Callable 객체**를 주입받아 사용합니다. 이는 `CassiopeiaClient`만으로는 불가능한 '응답 대기(Future waiting)' 메커니즘을 에이전트 베이스 클래스로부터 위임받기 위함입니다.
- **`DirectProvider`**: Gemini, Claude 등 외부 API 직접 호출. (3rd-party 독립 에이전트용)

### 2.2. Tool 객체 통합 (`ToolExecutor` 연동)
LLM에게 단순 액션 이름이 아닌, 명확한 JSON Schema가 포함된 `Tool` 객체 기반의 정보를 제공합니다. `analyze_task`는 `Tool` 객체나 `dict` 형태의 Schema 리스트를 유연하게 수용합니다.

### 2.3. Pydantic 기반의 엄격한 반환 타입 (`BrainDecision`)
파싱의 책임을 SDK가 전담합니다. 신뢰도(confidence) 미달 시 SDK가 스스로 `action="ask_clarification"` 형태로 반환하여 에이전트 코드의 복잡성을 줄입니다.

---

## 3. SDK 내 신규 모듈 설계 (SDK Design)

### 3.1. 타입 정의 및 모델
```python
from __future__ import annotations

from collections.abc import Callable, Awaitable, Sequence
from typing import Any, Literal
from pydantic import BaseModel
from cassiopeia_sdk.schemas import LLMResponse
from cassiopeia_sdk.tools import Tool

BackendType = Literal["gateway", "gemini", "claude", "local"]

# GatewayProvider에 주입되는 llm_caller의 타입 별칭
LLMCallerType = Callable[..., Awaitable[LLMResponse]]


class BrainDecision(BaseModel):
    action: str                        # 실행할 도구 이름 (신뢰도 미달 시 'ask_clarification')
    params: dict[str, Any]             # 도구에 전달할 파라미터
    reasoning: str | None = None       # LLM이 해당 결정을 내린 이유
    confidence: float = 1.0            # 결과 신뢰도 (0.0 ~ 1.0). LLM 미반환 시 기본값 1.0
    suggested_reply: str | None = None # ask_clarification 시 사용자에게 전달할 텍스트
```

### 3.2. `AgentBrainConfig` (정책 관리)
```python
class AgentBrainConfig(BaseModel):
    max_retries: int = 2
    # JSON 파싱 실패 또는 필수 파라미터 누락 시 최대 재시도 횟수.
    # 각 재시도는 지수 백오프(1s → 2s → 4s ...) 적용.

    confidence_threshold: float = 0.7
    # 이 수치 미만이면 SDK가 내부적으로 action="ask_clarification" 결정을 반환.
    # 에이전트 코드에서 직접 confidence 수치를 비교할 필요 없음.

    enable_injection_guard: bool = True
    # False로 설정 시 PromptInjectionGuard 검사를 전면 비활성화.

    injection_guard_policy: Literal["raise", "fallback"] = "fallback"
    # "raise"   : 인젝션 탐지 시 PromptInjectionError 예외 발생.
    # "fallback": 인젝션 탐지 시 예외 없이 confidence=0 + action="ask_clarification" 으로 강제 라우팅.
```

### 3.3. `PromptInjectionGuard` (보안 명세)
```python
class PromptInjectionGuard:
    """
    탐지 패턴:
    - 시스템 프롬프트 구조 탈출 시도
      (예: `[현재 요청 종료]`, `</system>`, `<|im_end|>`)
    - 마크다운 헤더 기반 인젝션
      (예: `## New Instruction`, `Ignore previous instructions`)
    - 역할 전환 시도
      (예: `You are now`, `Act as`, `새로운 역할`)

    처리 정책 (AgentBrainConfig.injection_guard_policy 에 따라 결정):
    - "raise"   : PromptInjectionError 예외 발생 → 호출자가 직접 처리
    - "fallback": confidence=0, action="ask_clarification" 으로 강제 라우팅
                  → 에이전트 코드 변경 없이 보안 위협 무력화
    """
    def __init__(self, enabled: bool = True, policy: Literal["raise", "fallback"] = "fallback"):
        self.enabled = enabled
        self.policy = policy

    def check(self, user_request: str) -> None:
        """탐지 시 policy에 따라 예외 발생 또는 InjectionFallbackSignal 반환."""
        ...
```

### 3.4. `AgentBrain` 메인 인터페이스
```python
class AgentBrain:
    def __init__(self,
                 agent_name: str,
                 capabilities: str,
                 backend: BackendType = "gateway",
                 llm_caller: LLMCallerType | None = None,
                 config: AgentBrainConfig | None = None):
        """
        Args:
            agent_name:   에이전트 식별 이름
            capabilities: 에이전트가 수행할 수 있는 작업에 대한 자연어 설명.
                          시스템 프롬프트 조립에 사용됨.
            backend:      LLM 호출 경로 선택.
            llm_caller:   backend="gateway"일 때 필수.
                          AgentBase.request_llm 메서드를 직접 주입. (예: self.request_llm)
                          Future 대기 메커니즘을 AgentBase로부터 위임받기 위함.
            config:       정책 설정. 미전달 시 AgentBrainConfig 기본값 적용.

        Note:
            api_key는 생성자에 직접 전달하지 않습니다.
            DirectProvider는 환경변수(GEMINI_API_KEY, ANTHROPIC_API_KEY 등)를 통해
            안전하게 키를 로드합니다.
        """
        self.agent_name = agent_name
        self.capabilities = capabilities
        self.config = config or AgentBrainConfig()

        if backend == "gateway":
            if not llm_caller:
                raise ValueError("Gateway backend requires an llm_caller (e.g., self.request_llm).")
            self.provider = GatewayProvider(caller=llm_caller)
        else:
            self.provider = LLMProviderFactory.create(backend)  # 환경변수에서 키 로드

        self.guard = PromptInjectionGuard(
            enabled=self.config.enable_injection_guard,
            policy=self.config.injection_guard_policy,
        )

    async def analyze_task(self,
                           user_request: str,
                           tools: Sequence[Tool | dict[str, Any]],
                           history: list[dict[str, str]] | None = None) -> BrainDecision:
        """
        Args:
            user_request: 사용자의 자연어 요청.
            tools:        실행 가능한 Tool 객체 또는 dict Schema 리스트.
                          ToolExecutor.get_registered_tools() 반환값과 호환.
            history:      멀티턴 대화 히스토리. [{"role": "user"|"assistant", "content": "..."}]
        """
        # 1. 인젝션 방어 검증 (policy에 따라 예외 또는 fallback 처리)
        self.guard.check(user_request)
        # 2. 시스템 프롬프트 조립 (capabilities + tools의 JSON Schema 변환)
        # 3. LLM 호출 (주입된 llm_caller 또는 DirectProvider 활용)
        # 4. JSON 안전 파싱 및 필수 파라미터 검증
        #    실패 시 config.max_retries 횟수만큼 지수 백오프 재시도
        # 5. 신뢰도 평가: confidence < config.confidence_threshold
        #    → action="ask_clarification", suggested_reply 자동 생성 후 반환
        return BrainDecision(...)
```

---

## 4. 에이전트 코드 변화 (Before vs After)

### Before (현재 모노리포 방식의 문제점)
```python
# shared_core에 강결합, 프롬프트 관리 부담, 모호한 dict 반환
from shared_core.llm import ClaudeProvider
import json

class ArchiveAgent:
    def __init__(self):
        self.llm = ClaudeProvider()

    async def handle_dispatch(self, msg):
        prompt = f"다음 문장을 분석해줘. {msg.get('content')}"
        res, _ = await self.llm.generate_response(prompt)
        data = json.loads(res)       # 파싱 에러 발생 가능성 높음
        action = data.get("action")  # 타입 보장 안 됨
        # ...
```

### After (SDK 기반 우아한 추상화 - 최종형)
```python
# 오직 SDK에만 의존, 프롬프트 엔지니어링 생략, 타입 안전성 보장
from cassiopeia_sdk.brain import AgentBrain, BrainDecision

class ArchiveAgent(AgentBase):
    def __init__(self, ...):
        super().__init__(...)
        self.brain = AgentBrain(
            agent_name="archive_agent",
            capabilities="노션 및 옵시디언 데이터 관리",
            backend="gateway",
            # 핵심: Future 대기 메커니즘이 포함된 베이스 클래스의 메서드를 주입
            llm_caller=self.request_llm,
        )

    async def handle_dispatch(self, msg):
        # SDK가 보안, 재시도, 스키마 변환, Future 대기, 신뢰도 평가를 모두 관리
        decision: BrainDecision = await self.brain.analyze_task(
            user_request=msg.get("content"),
            tools=self.executor.get_registered_tools(),  # Tool 객체·dict 모두 호환
            history=msg.get("context", []),
        )

        # SDK가 신뢰도 미달 시 ask_clarification을 자동 반환 — 에이전트는 분기만 처리
        if decision.action == "ask_clarification":
            return self.request_clarification(
                decision.suggested_reply or "요청을 좀 더 구체적으로 말씀해주세요."
            )

        return await self.executor.execute(decision.action, decision.params)
```

---

## 5. 구현 우선순위 (Implementation Checklist)

1. **`cassiopeia-sdk` 확장**
   - `brain/` 디렉토리 구조 셋업
   - `BrainDecision`, `AgentBrainConfig` Pydantic 모델 작성

2. **Provider 추상화 구현**
   - `GatewayProvider` (llm_caller 주입 방식) 최우선 구현
   - `DirectProvider` 구현 시 `optional extras` 설정 (`pip install cassiopeia-sdk[brain]`)

3. **Core 로직 개발**
   - `Sequence[Tool | dict]` 호환 JSON Schema 변환기 작성
   - `PromptInjectionGuard` 탐지 정규식 및 정책 분기 구현
   - `max_retries` 지수 백오프 루프 및 JSON 안전 파싱 구현
   - 신뢰도 자동 평가 및 `ask_clarification` 라우팅 로직

4. **리팩토링**
   - `archive_agent` 등에 신규 SDK 시범 적용 및 동작 검증

---

## 6. 구현 가이드 요약

1. **결합도 최적화**: `GatewayProvider`는 `AgentBase` 전체를 알 필요 없이 오직 `llm_caller` 함수 인터페이스에만 의존하여 가장 낮은 결합도를 유지합니다.
2. **책임의 분리**: Future 관리와 통신은 `AgentBase`가, 의도 분석과 데이터 정제는 `AgentBrain`이 담당합니다.
3. **확장성**: 새로운 LLM 공급자 추가 시 `DirectProvider` 구현체만 추가하면 모든 에이전트가 즉시 혜택을 받습니다.
