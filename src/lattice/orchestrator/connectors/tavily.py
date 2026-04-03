"""TavilyConnector: web search via Tavily API with digest summarization.

Returns structured search results as a digest (title + snippet + URL)
injected into CC instance via soul_file delivery mode.

Design decisions:
- Lazy client initialization (_ensure_client) avoids creating client at import time
- AsyncTavilyClient preferred; falls back to sync TavilyClient via asyncio.to_thread
- _build_digest caps at 5 results and truncates content at 200 chars per result
- Tavily is read-only — write() returns error ConnectorResult
- TavilyClient imported at module level so tests can patch it via
  'lattice.orchestrator.connectors.tavily.TavilyClient'
"""
from __future__ import annotations

import asyncio

import structlog

from lattice.orchestrator.connectors.base import BaseConnector, ConnectorError
from lattice.orchestrator.connectors.models import ConnectorConfig, ConnectorResult

log = structlog.get_logger(__name__)

# Module-level imports so tests can patch them.
# AsyncTavilyClient may not exist in all versions — fall back to TavilyClient.
try:
    from tavily import AsyncTavilyClient  # type: ignore[import]
    _HAS_ASYNC_CLIENT = True
except (ImportError, AttributeError):
    AsyncTavilyClient = None  # type: ignore[assignment]
    _HAS_ASYNC_CLIENT = False

try:
    from tavily import TavilyClient  # type: ignore[import]
except ImportError:
    TavilyClient = None  # type: ignore[assignment]


class TavilyConnector(BaseConnector):
    """Web search connector using the Tavily API.

    Fetches search results and formats them as a numbered digest with
    title, content snippet (200 chars max), and URL per result.
    Results are capped at 5 entries.

    Args:
        config: ConnectorConfig with api_key set for Tavily authentication.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._client: object | None = None
        self._is_async: bool = False

    def _ensure_client(self) -> object:
        """Lazily create TavilyClient on first use.

        Tries AsyncTavilyClient first; falls back to sync TavilyClient.
        Stores client in self._client and sets self._is_async accordingly.

        Returns:
            The TavilyClient instance.
        """
        if self._client is not None:
            return self._client

        if _HAS_ASYNC_CLIENT and AsyncTavilyClient is not None:
            self._client = AsyncTavilyClient(api_key=self._config.api_key)
            self._is_async = True
        elif TavilyClient is not None:
            self._client = TavilyClient(api_key=self._config.api_key)
            self._is_async = False
        else:
            raise ConnectorError("tavily-python is not installed")

        return self._client

    def _build_digest(self, results: list[dict]) -> str:
        """Format search results as a numbered digest string.

        Caps at 5 results. Truncates content at 200 characters per result.
        Results are separated by blank lines.

        Args:
            results: List of result dicts with 'title', 'content', 'url' keys.

        Returns:
            Formatted digest string, or empty string if results is empty.
        """
        if not results:
            return ""

        entries = []
        for i, item in enumerate(results[:5], start=1):
            title = item.get("title", "")
            content = item.get("content", "")[:200]
            url = item.get("url", "")
            entries.append(f"{i}. {title}\n   {content}\n   URL: {url}")

        return "\n\n".join(entries)

    async def fetch(self, query: str, **kwargs: object) -> ConnectorResult:
        """Search the web via Tavily and return a digest ConnectorResult.

        Args:
            query: The search query string.
            **kwargs: Ignored for Tavily.

        Returns:
            ConnectorResult with success=True, delivery_mode='soul_file', and
            content prefixed with '[Source: Tavily]'.

        Raises:
            ConnectorError: When the Tavily API call fails.
        """
        client = self._ensure_client()

        try:
            if self._is_async:
                response = await client.search(query)  # type: ignore[attr-defined]
            else:
                response = await asyncio.to_thread(client.search, query)  # type: ignore[attr-defined]
        except Exception as exc:
            raise ConnectorError(f"Tavily search failed: {exc}") from exc

        # Tavily returns {"results": [...]} or a list directly
        if isinstance(response, dict):
            results: list[dict] = response.get("results", [])
        elif isinstance(response, list):
            results = response
        else:
            results = []

        digest = self._build_digest(results)
        content = f"[Source: Tavily]\n{digest}" if digest else "[Source: Tavily]\nNo results found."

        log.info("tavily_search_completed", query=query, result_count=len(results))

        return ConnectorResult(
            success=True,
            source="tavily",
            content=content,
            delivery_mode="soul_file",
            metadata={
                "query": query,
                "result_count": str(len(results)),
            },
        )

    async def write(self, content: str, **kwargs: object) -> ConnectorResult:
        """Tavily is read-only — always returns an error result.

        Args:
            content: Ignored.
            **kwargs: Ignored.

        Returns:
            ConnectorResult(success=False) with read-only error message.
        """
        return ConnectorResult(
            success=False,
            source="tavily",
            error="Tavily is read-only",
        )

    async def health_check(self) -> bool:
        """Check if the connector is configured with an API key.

        Returns:
            True if api_key is non-empty, False otherwise.
        """
        return bool(self._config.api_key)
