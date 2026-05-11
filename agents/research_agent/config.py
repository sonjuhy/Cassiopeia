import os
from dataclasses import dataclass, field
from pathlib import Path

from shared_core.search.interfaces import SearchProviderName


@dataclass(frozen=True)
class ResearchAgentConfig:
    """
    리서치 에이전트의 설정 정보를 관리합니다.

    Attributes:
        search_provider: 사용할 검색 공급자 ("gemini" 또는 "perplexity").
        search_api_key: 검색 공급자 API 키.
        gemini_model: Gemini 검색 모델 이름.
        perplexity_model: Perplexity 검색 모델 이름.
        report_output_dir: 보고서 저장 기본 경로.
        fallback_provider: 보조 검색 공급자 (선택적).
        fallback_api_key: 보조 검색 공급자 API 키.
    """

    search_provider: SearchProviderName = "gemini"
    search_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    perplexity_model: str = "sonar"
    report_output_dir: Path = field(default_factory=lambda: Path("./reports"))
    cassiopeia_api_key: str = ""
    fallback_provider: SearchProviderName | None = None
    fallback_api_key: str = ""


def load_config_from_env() -> ResearchAgentConfig:
    """
    환경 변수로부터 ResearchAgentConfig를 로드합니다.

    환경 변수:
        RESEARCH_SEARCH_PROVIDER : 검색 공급자 ("gemini" | "perplexity"). 기본값 "gemini".
        GEMINI_API_KEY            : Gemini API 키 (provider=gemini 일 때 사용).
        PERPLEXITY_API_KEY        : Perplexity API 키 (provider=perplexity 일 때 사용).
        GEMINI_SEARCH_MODEL       : Gemini 모델 이름. 기본값 "gemini-2.5-flash".
        PERPLEXITY_SEARCH_MODEL   : Perplexity 모델 이름. 기본값 "sonar".
        RESEARCH_REPORT_OUTPUT_DIR: 보고서 저장 디렉터리. 기본값 "./reports".
        CASSIOPEIA_API_KEY        : 오케스트라(Cassiopeia) API 키.
        RESEARCH_FALLBACK_PROVIDER: 보조 검색 공급자 ("gemini" | "perplexity"). 기본값 None.
    """
    provider: SearchProviderName = os.environ.get(  # type: ignore[assignment]
        "RESEARCH_SEARCH_PROVIDER", "gemini"
    )

    if provider == "perplexity":
        api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    else:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        
    fallback_provider: SearchProviderName | None = os.environ.get( # type: ignore[assignment]
        "RESEARCH_FALLBACK_PROVIDER"
    )
    fallback_api_key = ""
    if fallback_provider:
        if fallback_provider == "perplexity":
            fallback_api_key = os.environ.get("PERPLEXITY_API_KEY", "")
        else:
            fallback_api_key = os.environ.get("GEMINI_API_KEY", "")

    return ResearchAgentConfig(
        search_provider=provider,
        search_api_key=api_key,
        gemini_model=os.environ.get("GEMINI_SEARCH_MODEL", "gemini-2.5-flash"),
        perplexity_model=os.environ.get("PERPLEXITY_SEARCH_MODEL", "sonar"),
        report_output_dir=Path(
            os.environ.get("RESEARCH_REPORT_OUTPUT_DIR", "./reports")
        ),
        cassiopeia_api_key=(os.environ.get("CASSIOPEIA_API_KEY") or os.environ.get("CLIENT_API_KEY", "")).strip('"\''),
        fallback_provider=fallback_provider,
        fallback_api_key=fallback_api_key,
    )
