"""
SearchProviderProtocol 구체 구현체

- GeminiSearchProvider  : Google Gemini API + Search Grounding
- PerplexitySearchProvider: Perplexity Sonar API
- FallbackSearchProvider: 다중 공급자 체이닝 및 재시도 로직
- build_search_provider : 설정 기반 팩토리 함수
"""

import asyncio
import logging
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from shared_core.search.interfaces import SearchProviderName, SearchResult, SearchProviderProtocol

logger = logging.getLogger(__name__)

class GeminiSearchProvider:
    """
    Google Gemini API의 Search Grounding을 사용하는 검색 공급자입니다.

    필요 패키지: google-generativeai >= 0.8
    """

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        try:
            import google.generativeai as genai  # type: ignore[import]
        except ImportError as e:
            raise ImportError(
                "google-generativeai 패키지가 필요합니다: pip install google-generativeai"
            ) from e

        genai.configure(api_key=api_key)
        self._genai = genai
        self._model_name = model

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    async def search(self, query: str) -> SearchResult:
        """Gemini Search Grounding을 통해 웹 검색을 수행합니다."""
        return await asyncio.to_thread(self._sync_search, query, None)

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    async def search_with_context(self, query: str, context: str) -> SearchResult:
        """컨텍스트를 시스템 지침으로 전달하여 정밀 검색을 수행합니다."""
        return await asyncio.to_thread(self._sync_search, query, context)

    # ------------------------------------------------------------------ #
    # 내부 구현 (동기 – asyncio.to_thread 에서 실행)                       #
    # ------------------------------------------------------------------ #

    def _sync_search(self, query: str, context: str | None) -> SearchResult:
        google_search_tool = self._genai.protos.Tool(
            google_search=self._genai.protos.GoogleSearch()
        )
        model = self._genai.GenerativeModel(
            model_name=self._model_name,
            tools=[google_search_tool],
            system_instruction=context,
        )
        response = model.generate_content(query)
        answer = response.text or ""
        citations = self._extract_citations(response)
        return SearchResult(answer=answer, citations=citations)

    @staticmethod
    def _extract_citations(response: object) -> list[str]:
        citations: list[str] = []
        candidates = getattr(response, "candidates", [])
        for candidate in candidates:
            grounding = getattr(candidate, "grounding_metadata", None)
            if not grounding:
                continue
            for chunk in getattr(grounding, "grounding_chunks", []) or []:
                web = getattr(chunk, "web", None)
                if web and getattr(web, "uri", None):
                    citations.append(web.uri)
        return citations


class PerplexitySearchProvider:
    """
    Perplexity Sonar API를 사용하는 검색 공급자입니다.

    필요 패키지: httpx >= 0.27
    """

    _API_URL = "https://api.perplexity.ai/chat/completions"

    def __init__(self, api_key: str, model: str = "sonar") -> None:
        try:
            import httpx  # noqa: F401 – 패키지 존재 확인
        except ImportError as e:
            raise ImportError(
                "httpx 패키지가 필요합니다: pip install httpx"
            ) from e
        self._api_key = api_key
        self._model = model

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    async def search(self, query: str) -> SearchResult:
        """Perplexity API에 단일 쿼리를 전달하여 검색합니다."""
        messages = [{"role": "user", "content": query}]
        return await self._call_api(messages)

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
    async def search_with_context(self, query: str, context: str) -> SearchResult:
        """시스템 메시지로 컨텍스트를 포함하여 검색합니다."""
        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": query},
        ]
        return await self._call_api(messages)

    # ------------------------------------------------------------------ #
    # 내부 구현                                                            #
    # ------------------------------------------------------------------ #

    async def _call_api(self, messages: list[dict]) -> SearchResult:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self._API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self._model, "messages": messages},
            )
            response.raise_for_status()
            data = response.json()

        answer: str = data["choices"][0]["message"]["content"]
        citations: list[str] = data.get("citations", [])
        return SearchResult(answer=answer, citations=citations)


class FallbackSearchProvider(SearchProviderProtocol):
    """
    주 검색 공급자가 실패할 경우 보조 공급자로 전환하는 검색 공급자입니다.
    """
    def __init__(self, primary: SearchProviderProtocol, secondary: SearchProviderProtocol | None = None) -> None:
        self._primary = primary
        self._secondary = secondary

    async def search(self, query: str) -> SearchResult:
        try:
            return await self._primary.search(query)
        except Exception as e:
            if not self._secondary:
                logger.error("Primary search provider failed, and no secondary provider is configured: %s", e)
                raise
            logger.warning("Primary search provider failed (%s). Falling back to secondary.", e)
            return await self._secondary.search(query)

    async def search_with_context(self, query: str, context: str) -> SearchResult:
        try:
            return await self._primary.search_with_context(query, context)
        except Exception as e:
            if not self._secondary:
                logger.error("Primary search provider failed, and no secondary provider is configured: %s", e)
                raise
            logger.warning("Primary search provider failed (%s). Falling back to secondary.", e)
            return await self._secondary.search_with_context(query, context)


def build_search_provider(
    provider_name: SearchProviderName,
    api_key: str,
    gemini_model: str = "gemini-2.5-flash",
    perplexity_model: str = "sonar",
) -> SearchProviderProtocol:
    """
    설정값을 기반으로 SearchProvider 인스턴스를 생성합니다.

    Args:
        provider_name: "gemini" 또는 "perplexity".
        api_key: 해당 공급자의 API 키.
        gemini_model: Gemini 검색 모델 이름.
        perplexity_model: Perplexity 검색 모델 이름.

    Returns:
        초기화된 SearchProvider 인스턴스.
    """
    if provider_name == "gemini":
        return GeminiSearchProvider(api_key=api_key, model=gemini_model)
    if provider_name == "perplexity":
        return PerplexitySearchProvider(api_key=api_key, model=perplexity_model)
    raise ValueError(f"지원하지 않는 검색 공급자: {provider_name!r}")
