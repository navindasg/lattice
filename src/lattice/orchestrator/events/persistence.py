"""DuckDB persistence for orchestrator events.

Provides CRUD operations on the orchestrator_events table:
- init_events_table: idempotent DDL
- insert_event: store a CCEvent with generated event_id
- mark_processed: flip processed flag
- get_history: filtered query sorted by timestamp desc
- get_pending: unprocessed events
- count_sessions: distinct session count
"""
from __future__ import annotations

import json

import duckdb
import structlog

from lattice.orchestrator.events.models import CCEvent

log = structlog.get_logger(__name__)

_CREATE_EVENTS_TABLE = """
    CREATE TABLE IF NOT EXISTS orchestrator_events (
        event_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        tool_name TEXT,
        tool_input TEXT,
        tool_response TEXT,
        transcript_path TEXT,
        cwd TEXT,
        timestamp TEXT NOT NULL,
        processed BOOLEAN NOT NULL DEFAULT false,
        received_at TEXT NOT NULL
    )
"""


def init_events_table(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the orchestrator_events table if it does not exist.

    Args:
        conn: An open DuckDB connection.
    """
    conn.execute(_CREATE_EVENTS_TABLE)


def insert_event(
    conn: duckdb.DuckDBPyConnection,
    event_id: str,
    event: CCEvent,
) -> None:
    """Insert a CCEvent into the orchestrator_events table.

    Args:
        conn: An open DuckDB connection.
        event_id: UUID4 string identifying this event.
        event: The CCEvent to persist.
    """
    from datetime import datetime, timezone

    tool_input_json = json.dumps(event.tool_input) if event.tool_input is not None else None
    received_at = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO orchestrator_events "
        "(event_id, session_id, event_type, tool_name, tool_input, tool_response, "
        "transcript_path, cwd, timestamp, processed, received_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, false, ?)",
        [
            event_id,
            event.session_id,
            event.event_type,
            event.tool_name,
            tool_input_json,
            event.tool_response,
            event.transcript_path,
            event.cwd,
            event.timestamp.isoformat(),
            received_at,
        ],
    )
    log.info("event_inserted", event_id=event_id, event_type=event.event_type)


def mark_processed(conn: duckdb.DuckDBPyConnection, event_id: str) -> None:
    """Set processed=true for a given event.

    Args:
        conn: An open DuckDB connection.
        event_id: The event UUID to mark.
    """
    conn.execute(
        "UPDATE orchestrator_events SET processed = true WHERE event_id = ?",
        [event_id],
    )
    log.info("event_processed", event_id=event_id)


def get_history(
    conn: duckdb.DuckDBPyConnection,
    *,
    session_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query event history with optional filters.

    Results are sorted by timestamp descending (most recent first).

    Args:
        conn: An open DuckDB connection.
        session_id: Filter to a specific session (optional).
        event_type: Filter to a specific event type (optional).
        limit: Maximum number of results (default 100).

    Returns:
        List of event dicts with all columns.
    """
    clauses: list[str] = []
    params: list = []

    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if event_type is not None:
        clauses.append("event_type = ?")
        params.append(event_type)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = (
        f"SELECT event_id, session_id, event_type, tool_name, tool_input, "
        f"tool_response, transcript_path, cwd, timestamp, processed, received_at "
        f"FROM orchestrator_events {where} "
        f"ORDER BY timestamp DESC LIMIT ?"
    )
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [
        {
            "event_id": r[0],
            "session_id": r[1],
            "event_type": r[2],
            "tool_name": r[3],
            "tool_input": r[4],
            "tool_response": r[5],
            "transcript_path": r[6],
            "cwd": r[7],
            "timestamp": r[8],
            "processed": r[9],
            "received_at": r[10],
        }
        for r in rows
    ]


def get_pending(
    conn: duckdb.DuckDBPyConnection,
    *,
    limit: int = 1000,
) -> list[dict]:
    """Get unprocessed events, up to *limit*.

    Args:
        conn: An open DuckDB connection.
        limit: Maximum number of results (default 1000).

    Returns:
        List of event dicts where processed is false, ordered by timestamp desc.
    """
    rows = conn.execute(
        "SELECT event_id, session_id, event_type, tool_name, tool_input, "
        "tool_response, transcript_path, cwd, timestamp, processed, received_at "
        "FROM orchestrator_events WHERE processed = false "
        "ORDER BY timestamp DESC LIMIT ?",
        [limit],
    ).fetchall()
    return [
        {
            "event_id": r[0],
            "session_id": r[1],
            "event_type": r[2],
            "tool_name": r[3],
            "tool_input": r[4],
            "tool_response": r[5],
            "transcript_path": r[6],
            "cwd": r[7],
            "timestamp": r[8],
            "processed": r[9],
            "received_at": r[10],
        }
        for r in rows
    ]


def count_pending(conn: duckdb.DuckDBPyConnection) -> int:
    """Count unprocessed events without materializing rows.

    Args:
        conn: An open DuckDB connection.

    Returns:
        Number of unprocessed events.
    """
    result = conn.execute(
        "SELECT COUNT(*) FROM orchestrator_events WHERE processed = false"
    ).fetchone()
    return result[0] if result else 0


def count_sessions(conn: duckdb.DuckDBPyConnection) -> int:
    """Count distinct session IDs in the events table.

    Args:
        conn: An open DuckDB connection.

    Returns:
        Number of unique session IDs.
    """
    result = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM orchestrator_events"
    ).fetchone()
    return result[0] if result else 0
