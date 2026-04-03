"""Unit tests for the stdio NDJSON adapter (lattice.api.stdio).

Tests cover:
    - Dispatches known commands via HANDLERS dict to correct handler
    - Returns error_response for unrecognized commands
    - Handles malformed JSON gracefully (no crash, continues reading)
    - Handles EOF (empty readline) by exiting cleanly
    - handle_map_doc with background_tasks=None uses asyncio.create_task
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lattice.api.models import error_response, success_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reader(data: bytes) -> asyncio.StreamReader:
    """Create an asyncio.StreamReader pre-populated with bytes + EOF."""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


class _BytesCollector:
    """Collects bytes written via write(data) calls."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def write(self, data: bytes) -> None:
        self._buffer.extend(data)

    @property
    def collected(self) -> bytes:
        return bytes(self._buffer)

    def lines(self) -> list[str]:
        return [l for l in self.collected.decode().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# Test 1: stdio adapter dispatches known command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_dispatches_map_status(tmp_path: Path) -> None:
    """stdio adapter dispatches 'map:status' and returns success_response envelope."""
    from lattice.api.stdio import _dispatch_loop

    payload = {"target": str(tmp_path)}
    line = json.dumps({"command": "map:status", "payload": payload}).encode() + b"\n"

    mock_handler = AsyncMock(
        return_value=success_response("map:status", {"phases": 0, "test": True})
    )

    reader = _make_reader(line)
    collector = _BytesCollector()

    with patch("lattice.api.stdio.HANDLERS", {"map:status": mock_handler}):
        await _dispatch_loop(reader, collector.write)

    assert collector.lines(), "No output written"
    response = json.loads(collector.lines()[0])
    assert response["success"] is True
    assert response["command"] == "map:status"

    # Verify called with (payload_dict, None) — None for background_tasks
    mock_handler.assert_called_once()
    call_args = mock_handler.call_args[0]
    assert call_args[0] == payload
    assert call_args[1] is None  # background_tasks=None


# ---------------------------------------------------------------------------
# Test 2: stdio adapter returns error for unknown command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_unknown_command() -> None:
    """stdio adapter returns error_response with UNKNOWN_COMMAND for unknown command."""
    from lattice.api.stdio import _dispatch_loop

    line = json.dumps({"command": "unknown:cmd", "payload": {}}).encode() + b"\n"
    reader = _make_reader(line)
    collector = _BytesCollector()

    await _dispatch_loop(reader, collector.write)

    assert collector.lines(), "No output written"
    response = json.loads(collector.lines()[0])
    assert response["success"] is False
    assert response["error"]["code"] == "UNKNOWN_COMMAND"


# ---------------------------------------------------------------------------
# Test 3: stdio adapter handles malformed JSON without crashing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_malformed_json(tmp_path: Path) -> None:
    """stdio adapter skips malformed JSON and processes the following valid line."""
    from lattice.api.stdio import _dispatch_loop

    mock_handler = AsyncMock(
        return_value=success_response("map:status", {"ok": True})
    )

    # First line: invalid JSON; second line: valid command
    data = (
        b"not-valid-json\n"
        + json.dumps({"command": "map:status", "payload": {"target": str(tmp_path)}}).encode()
        + b"\n"
    )

    reader = _make_reader(data)
    collector = _BytesCollector()

    with patch("lattice.api.stdio.HANDLERS", {"map:status": mock_handler}):
        await _dispatch_loop(reader, collector.write)

    # Only one valid response expected (malformed line was skipped)
    lines = collector.lines()
    assert len(lines) == 1, f"Expected 1 response line, got: {lines}"
    response = json.loads(lines[0])
    assert response["success"] is True
    mock_handler.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: stdio adapter exits cleanly on EOF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_eof_exits() -> None:
    """stdio adapter exits without error when stdin is immediately EOF."""
    from lattice.api.stdio import _dispatch_loop

    reader = _make_reader(b"")  # empty = immediate EOF
    collector = _BytesCollector()

    # Should return without raising
    await _dispatch_loop(reader, collector.write)

    # No output expected
    assert collector.collected == b""


# ---------------------------------------------------------------------------
# Test 5: handle_map_doc with background_tasks=None does not crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_map_doc_none_background_tasks(tmp_path: Path) -> None:
    """handle_map_doc with background_tasks=None does not raise AttributeError.

    Returns GRAPH_NOT_FOUND (no _graph.json exists) — that is expected.
    The key assertion is no AttributeError from background_tasks.add_task(None...).
    """
    from lattice.api.handlers import handle_map_doc

    # No _graph.json — should return error envelope, not crash
    result = await handle_map_doc({"target": str(tmp_path)}, None)

    assert result["success"] is False
    assert result["error"]["code"] == "GRAPH_NOT_FOUND"


@pytest.mark.asyncio
async def test_handle_map_doc_none_background_tasks_with_graph(tmp_path: Path) -> None:
    """handle_map_doc with background_tasks=None and _graph.json uses asyncio.create_task."""
    from lattice.api.handlers import handle_map_doc

    # Create a minimal _graph.json
    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir()
    graph_data = {"nodes": [], "edges": [], "metadata": {}}
    (agent_docs / "_graph.json").write_text(
        json.dumps(graph_data), encoding="utf-8"
    )

    with patch("lattice.api.handlers.asyncio") as mock_asyncio:
        mock_asyncio.create_task = MagicMock()
        with patch("lattice.api.handlers.uuid.uuid4", return_value="test-run-id"):
            result = await handle_map_doc({"target": str(tmp_path)}, None)

    assert result["success"] is True
    assert result["data"]["status"] == "started"
    # asyncio.create_task should have been called (not background_tasks.add_task)
    mock_asyncio.create_task.assert_called_once()


# ---------------------------------------------------------------------------
# Test: run_stdio_server function exists and is importable
# ---------------------------------------------------------------------------


def test_run_stdio_server_importable() -> None:
    """run_stdio_server function is importable from lattice.api.stdio."""
    from lattice.api.stdio import run_stdio_server

    assert callable(run_stdio_server)
