"""
에이전트 레지스트리 구현체
- AgentRegistryProtocol 구현
- 등록된 에이전트의 이름과 역할 설명을 인메모리 딕셔너리로 관리합니다.

[설계 원칙]
에이전트 목록을 이 파일에 하드코딩하지 마세요.
각 에이전트는 시작 시 HealthMonitor.register_agent() 를 통해 Redis 레지스트리에 자기 자신을 등록합니다.
카시오페아 매니저는 NLU 실행 전 HealthMonitor.get_nlu_capabilities() 로 동적 목록을 조회합니다.
이 클래스는 cassiopeia-sdk 기반 프로젝트에서 인메모리 오버라이드용으로만 사용하세요.
"""

from shared_core.messaging import AgentName

from .interfaces import AgentRegistryProtocol


class AgentRegistry:
    """
    AgentRegistryProtocol의 구체 구현체.
    에이전트 이름과 역할 설명을 인메모리 딕셔너리로 관리합니다.

    기본값으로 등록되는 에이전트는 없습니다.
    에이전트는 반드시 register_agent() 를 명시적으로 호출해 등록해야 합니다.
    """

    def __init__(self) -> None:
        self._agents: dict[str, str] = {}

    def register_agent(self, name: AgentName, capability_description: str) -> None:
        """에이전트를 레지스트리에 추가합니다."""
        self._agents[name] = capability_description
        print(f"[registry] 에이전트 등록: {name}")

    def unregister_agent(self, name: AgentName) -> None:
        """에이전트를 레지스트리에서 제거합니다."""
        if name in self._agents:
            del self._agents[name]
            print(f"[registry] 에이전트 해제: {name}")

    def get_agent_capabilities(self) -> dict[str, str]:
        """등록된 모든 에이전트의 능력 정보를 반환합니다 (LLM 컨텍스트용)."""
        return dict(self._agents)
