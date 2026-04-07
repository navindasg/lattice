"""Tests for DuckDB event persistence layer.

Covers:
- Table creation on init
- insert_event + query back
- mark_processed changes flag
- get_history with no filters, session_id filter, event_type filter
- get_pending returns only unprocessed
- count_sessions returns distinct count
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pytest

from lattice.orchestrator.events.models import CCEvent
from lattice.orchestrator.events.persistence import (
    count_sessions,
    get_history,
    get_pending,
    init_events_table,
    insert_event,
    mark_processed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection with events table."""
    conn = duckdb.connect(":memory:")
    init_events_table(conn)
    return conn


def _make_event(
    session_id: str = "sess-001",
    event_type: str = "PreToolUse",
    **overrides,
) -> CCEvent:
    """Create a CCEvent with sensible defaults."""
    defaults = {
        "session_id": session_id,
        "event_type": event_type,
        "timestamp": datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    }
    return CCEvent(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_table_creation() -> None:
    """init_events_table creates the orchestrator_events table."""
    conn = _make_conn()
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'orchestrator_events'"
    ).fetchall()
    assert len(tables) == 1


def test_table_creation_idempotent() -> None:
    """Calling init_events_table twice does not raise."""
    conn = duckdb.connect(":memory:")
    init_events_table(conn)
    init_events_table(conn)  # should not raise


def test_insert_and_query() -> None:
    """insert_event persists a CCEvent that can be queried back."""
    conn = _make_conn()
    event = _make_event(tool_name="Read", tool_input={"file": "a.py"})
    insert_event(conn, "evt-001", event)

    rows = get_history(conn)
    assert len(rows) == 1
    assert rows[0]["event_id"] == "evt-001"
    assert rows[0]["session_id"] == "sess-001"
    assert rows[0]["event_type"] == "PreToolUse"
    assert rows[0]["tool_name"] == "Read"
    assert rows[0]["processed"] is False


def test_mark_processed() -> None:
    """mark_processed sets processed=true for the given event."""
    conn = _make_conn()
    event = _make_event()
    insert_event(conn, "evt-002", event)

    mark_processed(conn, "evt-002")

    rows = get_history(conn)
    assert rows[0]["processed"] is True


def test_get_history_no_filters() -> None:
    """get_history with no filters returns all events."""
    conn = _make_conn()
    for i in range(5):
        insert_event(conn, f"evt-{i}", _make_event())

    rows = get_history(conn)
    assert len(rows) == 5


def test_get_history_session_filter() -> None:
    """get_history filters by session_id."""
    conn = _make_conn()
    insert_event(conn, "evt-a", _make_event(session_id="alpha"))
    insert_event(conn, "evt-b", _make_event(session_id="beta"))
    insert_event(conn, "evt-c", _make_event(session_id="alpha"))

    rows = get_history(conn, session_id="alpha")
    assert len(rows) == 2
    assert all(r["session_id"] == "alpha" for r in rows)


def test_get_history_event_type_filter() -> None:
    """get_history filters by event_type."""
    conn = _make_conn()
    insert_event(conn, "evt-1", _make_event(event_type="PreToolUse"))
    insert_event(conn, "evt-2", _make_event(event_type="PostToolUse"))
    insert_event(conn, "evt-3", _make_event(event_type="PreToolUse"))

    rows = get_history(conn, event_type="PostToolUse")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "PostToolUse"


def test_get_history_limit() -> None:
    """get_history respects the limit parameter."""
    conn = _make_conn()
    for i in range(10):
        insert_event(conn, f"evt-{i}", _make_event())

    rows = get_history(conn, limit=3)
    assert len(rows) == 3


def test_get_pending_only_unprocessed() -> None:
    """get_pending returns only events where processed=false."""
    conn = _make_conn()
    insert_event(conn, "evt-p1", _make_event())
    insert_event(conn, "evt-p2", _make_event())
    insert_event(conn, "evt-p3", _make_event())

    mark_processed(conn, "evt-p2")

    pending = get_pending(conn)
    assert len(pending) == 2
    event_ids = {r["event_id"] for r in pending}
    assert "evt-p2" not in event_ids


def test_count_sessions_distinct() -> None:
    """count_sessions returns distinct session count."""
    conn = _make_conn()
    insert_event(conn, "evt-1", _make_event(session_id="s1"))
    insert_event(conn, "evt-2", _make_event(session_id="s2"))
    insert_event(conn, "evt-3", _make_event(session_id="s1"))
    insert_event(conn, "evt-4", _make_event(session_id="s3"))

    assert count_sessions(conn) == 3


def test_count_sessions_empty() -> None:
    """count_sessions returns 0 for empty table."""
    conn = _make_conn()
    assert count_sessions(conn) == 0
