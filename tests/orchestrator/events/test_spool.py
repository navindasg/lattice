"""Tests for crash-resilient event spool.

Covers:
- append_to_spool creates file and writes JSONL
- append_to_spool appends multiple events
- drain_spool reads all events into DB and queue
- drain_spool truncates file after drain
- drain_spool handles corrupt lines (skips, logs warning)
- drain_spool handles missing file (no error)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from lattice.orchestrator.events.models import CCEvent
from lattice.orchestrator.events.persistence import get_history, init_events_table
from lattice.orchestrator.events.spool import append_to_spool, drain_spool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(session_id: str = "sess-001", event_type: str = "PreToolUse") -> CCEvent:
    """Create a CCEvent with sensible defaults."""
    return CCEvent(
        session_id=session_id,
        event_type=event_type,
        timestamp=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_conn() -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection with events table."""
    conn = duckdb.connect(":memory:")
    init_events_table(conn)
    return conn


# ---------------------------------------------------------------------------
# append_to_spool tests
# ---------------------------------------------------------------------------

def test_append_creates_file(tmp_path: Path) -> None:
    """append_to_spool creates the spool file if it does not exist."""
    spool_file = tmp_path / "spool" / "events.jsonl"
    event = _make_event()

    append_to_spool(event, spool_file=spool_file)

    assert spool_file.exists()
    lines = spool_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["session_id"] == "sess-001"


def test_append_multiple_events(tmp_path: Path) -> None:
    """append_to_spool appends multiple events as separate lines."""
    spool_file = tmp_path / "events.jsonl"

    for i in range(3):
        append_to_spool(_make_event(session_id=f"sess-{i}"), spool_file=spool_file)

    lines = spool_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    for i, line in enumerate(lines):
        data = json.loads(line)
        assert data["session_id"] == f"sess-{i}"


# ---------------------------------------------------------------------------
# drain_spool tests
# ---------------------------------------------------------------------------

async def test_drain_reads_into_db_and_queue(tmp_path: Path) -> None:
    """drain_spool inserts all events into DB and puts them on the queue."""
    spool_file = tmp_path / "events.jsonl"
    conn = _make_conn()
    queue: asyncio.Queue = asyncio.Queue()

    for i in range(3):
        append_to_spool(_make_event(session_id=f"sess-{i}"), spool_file=spool_file)

    count = drain_spool(conn, queue, spool_file=spool_file)

    assert count == 3
    assert queue.qsize() == 3
    rows = get_history(conn)
    assert len(rows) == 3


async def test_drain_truncates_file(tmp_path: Path) -> None:
    """drain_spool truncates the spool file after draining."""
    spool_file = tmp_path / "events.jsonl"
    conn = _make_conn()
    queue: asyncio.Queue = asyncio.Queue()

    append_to_spool(_make_event(), spool_file=spool_file)
    drain_spool(conn, queue, spool_file=spool_file)

    content = spool_file.read_text(encoding="utf-8")
    assert content == ""


async def test_drain_handles_corrupt_lines(tmp_path: Path) -> None:
    """drain_spool skips corrupt lines and processes valid ones."""
    spool_file = tmp_path / "events.jsonl"
    conn = _make_conn()
    queue: asyncio.Queue = asyncio.Queue()

    # Write a valid event, a corrupt line, and another valid event
    append_to_spool(_make_event(session_id="good-1"), spool_file=spool_file)
    with spool_file.open("a", encoding="utf-8") as f:
        f.write("THIS IS NOT JSON\n")
    append_to_spool(_make_event(session_id="good-2"), spool_file=spool_file)

    count = drain_spool(conn, queue, spool_file=spool_file)

    assert count == 2
    assert queue.qsize() == 2
    rows = get_history(conn)
    session_ids = {r["session_id"] for r in rows}
    assert session_ids == {"good-1", "good-2"}


async def test_drain_handles_missing_file(tmp_path: Path) -> None:
    """drain_spool returns 0 and does not error when spool file is missing."""
    spool_file = tmp_path / "nonexistent.jsonl"
    conn = _make_conn()
    queue: asyncio.Queue = asyncio.Queue()

    count = drain_spool(conn, queue, spool_file=spool_file)

    assert count == 0
    assert queue.qsize() == 0
