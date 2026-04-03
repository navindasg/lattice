"""Tests for NDJSON stdin/stdout protocol.

Covers:
- write_message: JSON + newline + drain
- read_message: readline + JSON parse + EOF/error handling
- drain_stderr: background stream drain
- create_request_envelope: auto UUID request_id
- parse_response_envelope: success/error structure
"""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lattice.orchestrator.protocol import (
    create_request_envelope,
    drain_stderr,
    parse_response_envelope,
    read_message,
    write_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockWriter:
    """Minimal asyncio.StreamWriter stand-in that captures write() calls."""

    def __init__(self) -> None:
        self._buffer = b""
        self.drain = AsyncMock()

    def write(self, data: bytes) -> None:
        self._buffer += data

    @property
    def written(self) -> bytes:
        return self._buffer


def _make_reader(data: bytes, eof: bool = True) -> asyncio.StreamReader:
    """Create an asyncio.StreamReader pre-loaded with data."""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


# ---------------------------------------------------------------------------
# write_message tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_message_writes_json_newline() -> None:
    """write_message writes JSON bytes + b'\\n' to writer."""
    writer = _MockWriter()
    payload = {"request_id": "abc", "task": "test"}
    await write_message(writer, payload)  # type: ignore[arg-type]
    assert writer.written == b'{"request_id":"abc","task":"test"}\n'


@pytest.mark.asyncio
async def test_write_message_calls_drain() -> None:
    """write_message calls drain() after write."""
    writer = _MockWriter()
    await write_message(writer, {"x": 1})  # type: ignore[arg-type]
    writer.drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_message_includes_request_id_in_serialized_json() -> None:
    """write_message includes request_id in the serialized JSON when provided."""
    writer = _MockWriter()
    payload = {"request_id": "abc", "task": "test"}
    await write_message(writer, payload)  # type: ignore[arg-type]
    decoded = json.loads(writer.written.rstrip(b"\n"))
    assert decoded["request_id"] == "abc"


@pytest.mark.asyncio
async def test_write_message_produces_exact_ndjson() -> None:
    """write_message with request_id and task produces exact expected bytes."""
    writer = _MockWriter()
    payload = {"request_id": "abc", "task": "test"}
    await write_message(writer, payload)  # type: ignore[arg-type]
    assert writer.written == b'{"request_id":"abc","task":"test"}\n'


# ---------------------------------------------------------------------------
# read_message tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_message_returns_dict_on_valid_json() -> None:
    """read_message returns dict with correct fields on valid NDJSON line."""
    reader = _make_reader(b'{"request_id":"r1","success":true}\n')
    result = await read_message(reader)
    assert result is not None
    assert result["request_id"] == "r1"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_read_message_returns_none_on_eof() -> None:
    """read_message returns None on empty bytes (EOF)."""
    reader = _make_reader(b"", eof=True)
    result = await read_message(reader)
    assert result is None


@pytest.mark.asyncio
async def test_read_message_returns_none_on_invalid_json_and_logs_warning() -> None:
    """read_message returns None on invalid JSON and logs a warning."""
    reader = _make_reader(b"not-json\n")
    with patch("lattice.orchestrator.protocol.log") as mock_log:
        result = await read_message(reader)
    assert result is None
    mock_log.warning.assert_called_once()
    call_kwargs = mock_log.warning.call_args
    assert "ndjson_parse_error" in call_kwargs[0]


@pytest.mark.asyncio
async def test_read_message_returns_none_on_partial_line_and_logs_warning() -> None:
    """read_message returns None on partial line (EOF without newline) and logs warning."""
    # Feed partial data without newline, then EOF
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"partial":')
    reader.feed_eof()
    with patch("lattice.orchestrator.protocol.log") as mock_log:
        result = await read_message(reader)
    assert result is None
    mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# drain_stderr tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drain_stderr_reads_lines_and_logs() -> None:
    """drain_stderr reads lines from stderr and logs each via structlog."""
    reader = _make_reader(b"line1\nline2\n")
    mock_logger = MagicMock()
    mock_logger.debug = MagicMock()
    await drain_stderr(reader, logger=mock_logger)
    assert mock_logger.debug.call_count == 2


@pytest.mark.asyncio
async def test_drain_stderr_stops_on_exhausted_stream() -> None:
    """drain_stderr stops (does not hang) when stream is exhausted."""
    reader = _make_reader(b"")
    # Should complete without blocking
    await asyncio.wait_for(drain_stderr(reader), timeout=1.0)


# ---------------------------------------------------------------------------
# create_request_envelope tests
# ---------------------------------------------------------------------------

def test_create_request_envelope_includes_auto_uuid_request_id() -> None:
    """create_request_envelope returns dict with auto-generated UUID request_id."""
    payload = {"task": "do_work"}
    result = create_request_envelope(payload)
    assert "request_id" in result
    # Validate it's a proper UUID
    uuid.UUID(result["request_id"])


def test_create_request_envelope_includes_all_payload_keys() -> None:
    """create_request_envelope includes all keys from task_payload."""
    payload = {"task": "do_work", "priority": "high", "data": {"x": 1}}
    result = create_request_envelope(payload)
    assert result["task"] == "do_work"
    assert result["priority"] == "high"
    assert result["data"] == {"x": 1}


def test_create_request_envelope_generates_unique_ids() -> None:
    """create_request_envelope generates different request_id each call."""
    r1 = create_request_envelope({"task": "a"})
    r2 = create_request_envelope({"task": "a"})
    assert r1["request_id"] != r2["request_id"]


# ---------------------------------------------------------------------------
# parse_response_envelope tests
# ---------------------------------------------------------------------------

def test_parse_response_envelope_success() -> None:
    """parse_response_envelope with success=True returns structured result with data."""
    msg = {"request_id": "r1", "success": True, "data": {"result": 42}}
    result = parse_response_envelope(msg)
    assert result["request_id"] == "r1"
    assert result["success"] is True
    assert result["data"] == {"result": 42}
    assert "error" not in result


def test_parse_response_envelope_failure() -> None:
    """parse_response_envelope with success=False returns error info."""
    msg = {
        "request_id": "r1",
        "success": False,
        "error": {"code": "TASK_FAILED", "message": "boom"},
    }
    result = parse_response_envelope(msg)
    assert result["request_id"] == "r1"
    assert result["success"] is False
    assert result["error"]["code"] == "TASK_FAILED"
    assert result["error"]["message"] == "boom"
    assert "data" not in result
