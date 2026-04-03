"""Tests for TavilyConnector.

Tests use mocked TavilyClient — no real API calls.
Covers digest formatting, result capping, truncation, source tagging,
error handling, and health check.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lattice.orchestrator.connectors.base import ConnectorError
from lattice.orchestrator.connectors.models import ConnectorConfig, ConnectorPermissions
from lattice.orchestrator.connectors.tavily import TavilyConnector


@pytest.fixture()
def config() -> ConnectorConfig:
    return ConnectorConfig(
        name="tavily",
        connector_type="tavily",
        api_key="test-api-key",
    )


@pytest.fixture()
def connector(config: ConnectorConfig) -> TavilyConnector:
    return TavilyConnector(config)


# ---------------------------------------------------------------------------
# _build_digest tests
# ---------------------------------------------------------------------------


class TestBuildDigest:
    def test_single_result_format(self, connector: TavilyConnector) -> None:
        """Digest for a single result has numbered entry with title, content, URL."""
        results = [{"title": "T1", "content": "C1", "url": "http://u1"}]
        digest = connector._build_digest(results)
        assert digest == "1. T1\n   C1\n   URL: http://u1"

    def test_caps_at_five_results(self, connector: TavilyConnector) -> None:
        """_build_digest caps output at 5 results even when given 10."""
        results = [
            {"title": f"T{i}", "content": f"C{i}", "url": f"http://u{i}"}
            for i in range(10)
        ]
        digest = connector._build_digest(results)
        # Only 5 numbered entries: "1. T0", "2. T1", ..., "5. T4"
        numbered = [l for l in digest.split("\n") if l and l[0].isdigit() and ". " in l]
        assert len(numbered) == 5

    def test_truncates_content_at_200_chars(self, connector: TavilyConnector) -> None:
        """_build_digest truncates content to 200 characters per result."""
        long_content = "A" * 300
        results = [{"title": "T", "content": long_content, "url": "http://u"}]
        digest = connector._build_digest(results)
        # Content line is the second line (index 1) after "1. T"
        lines = digest.split("\n")
        content_line = lines[1].strip()
        assert len(content_line) <= 200

    def test_multiple_results_separator(self, connector: TavilyConnector) -> None:
        """Multiple results are separated by a blank line."""
        results = [
            {"title": "T1", "content": "C1", "url": "http://u1"},
            {"title": "T2", "content": "C2", "url": "http://u2"},
        ]
        digest = connector._build_digest(results)
        assert "\n\n" in digest

    def test_empty_results_returns_empty_string(self, connector: TavilyConnector) -> None:
        """_build_digest with no results returns empty string."""
        assert connector._build_digest([]) == ""


# ---------------------------------------------------------------------------
# fetch tests
# ---------------------------------------------------------------------------


def _make_mock_client(search_return=None, search_side_effect=None):
    """Helper to create a mock Tavily client that overrides _ensure_client."""
    mock_client = MagicMock()
    if search_side_effect is not None:
        mock_client.search.side_effect = search_side_effect
    else:
        mock_client.search.return_value = search_return
    return mock_client


def _run_fetch(connector: TavilyConnector, query: str, mock_result=None, side_effect=None):
    """Run fetch() with a mocked client by patching _ensure_client."""
    import asyncio

    mock_client = _make_mock_client(search_return=mock_result, search_side_effect=side_effect)

    original_ensure = connector._ensure_client

    def patched_ensure():
        connector._client = mock_client
        connector._is_async = False
        return mock_client

    connector._ensure_client = patched_ensure  # type: ignore[method-assign]
    connector._client = None  # Reset lazy state
    try:
        return asyncio.run(connector.fetch(query))
    finally:
        connector._ensure_client = original_ensure  # type: ignore[method-assign]
        connector._client = None


class TestFetch:
    def test_fetch_returns_success_result(self, connector: TavilyConnector) -> None:
        """fetch returns ConnectorResult(success=True, source='tavily')."""
        canned = {
            "results": [
                {"title": "Python Async", "content": "asyncio basics", "url": "http://example.com"}
            ]
        }
        result = _run_fetch(connector, "python async", mock_result=canned)
        assert result.success is True
        assert result.source == "tavily"

    def test_fetch_result_has_soul_file_delivery_mode(self, connector: TavilyConnector) -> None:
        """fetch returns delivery_mode='soul_file'."""
        canned = {"results": [{"title": "T", "content": "C", "url": "http://u"}]}
        result = _run_fetch(connector, "test query", mock_result=canned)
        assert result.delivery_mode == "soul_file"

    def test_fetch_content_prefixed_with_source_tavily(self, connector: TavilyConnector) -> None:
        """fetch content is prefixed with '[Source: Tavily]'."""
        canned = {"results": [{"title": "T", "content": "C", "url": "http://u"}]}
        result = _run_fetch(connector, "test query", mock_result=canned)
        assert result.content.startswith("[Source: Tavily]")

    def test_fetch_metadata_contains_query_and_result_count(self, connector: TavilyConnector) -> None:
        """fetch result metadata contains 'query' and 'result_count' keys."""
        canned = {
            "results": [
                {"title": "T1", "content": "C1", "url": "http://u1"},
                {"title": "T2", "content": "C2", "url": "http://u2"},
            ]
        }
        result = _run_fetch(connector, "test query", mock_result=canned)
        assert result.metadata["query"] == "test query"
        assert result.metadata["result_count"] == "2"

    def test_fetch_raises_connector_error_on_api_failure(self, connector: TavilyConnector) -> None:
        """fetch raises ConnectorError when TavilyClient raises an exception."""
        import asyncio
        with pytest.raises(ConnectorError, match="Tavily search failed"):
            _run_fetch(connector, "failing query", side_effect=Exception("API error"))

    def test_fetch_handles_results_key_format(self, connector: TavilyConnector) -> None:
        """fetch handles Tavily response dict with 'results' key."""
        canned = {
            "results": [
                {"title": "Result", "content": "Some content here", "url": "http://example.com"}
            ]
        }
        result = _run_fetch(connector, "search term", mock_result=canned)
        assert result.success is True
        assert "Result" in result.content


# ---------------------------------------------------------------------------
# health_check tests
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_check_false_when_api_key_empty(self) -> None:
        """health_check returns False when api_key is empty string."""
        config = ConnectorConfig(
            name="tavily-no-key",
            connector_type="tavily",
            api_key="",
        )
        connector = TavilyConnector(config)
        import asyncio
        result = asyncio.run(connector.health_check())
        assert result is False

    def test_health_check_true_when_api_key_set(self, connector: TavilyConnector) -> None:
        """health_check returns True when api_key is non-empty."""
        import asyncio
        result = asyncio.run(connector.health_check())
        assert result is True


# ---------------------------------------------------------------------------
# write tests
# ---------------------------------------------------------------------------


class TestWrite:
    def test_write_returns_failure_tavily_is_read_only(self, connector: TavilyConnector) -> None:
        """write returns ConnectorResult(success=False) — Tavily is read-only."""
        import asyncio
        result = asyncio.run(connector.write("anything"))
        assert result.success is False
        assert result.source == "tavily"
        assert "read-only" in result.error.lower()
