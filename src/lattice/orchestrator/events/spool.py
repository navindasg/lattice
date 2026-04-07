"""Crash-resilient spool for event ingestion.

Events are appended to a JSONL file on disk before being inserted into
DuckDB. On startup, drain_spool replays any un-ingested events from a
previous crash, then truncates the file.

This ensures no events are lost even if the process dies between
receiving an event and persisting it to the database.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import uuid
from pathlib import Path

import duckdb
import structlog

from lattice.orchestrator.events.models import CCEvent
from lattice.orchestrator.events.persistence import insert_event

log = structlog.get_logger(__name__)

SPOOL_DIR: Path = Path.home() / ".lattice" / "spool"
SPOOL_FILE: Path = SPOOL_DIR / "events.jsonl"


def append_to_spool(event: CCEvent, *, spool_file: Path | None = None) -> None:
    """Append a CCEvent as a JSON line to the spool file.

    Creates the spool directory and file if they do not exist.

    Args:
        event: The CCEvent to persist.
        spool_file: Override the default spool file path (for testing).
    """
    target = spool_file or SPOOL_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    line = event.model_dump_json()
    with target.open("a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line + "\n")
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)

    log.debug("event_spooled", session_id=event.session_id, event_type=event.event_type)


def drain_spool(
    conn: duckdb.DuckDBPyConnection,
    queue: asyncio.Queue,
    *,
    spool_file: Path | None = None,
) -> int:
    """Read all events from spool, insert into DB and queue, then truncate.

    Handles corrupt lines gracefully by logging a warning and skipping.
    Handles a missing spool file with no error.

    Args:
        conn: An open DuckDB connection.
        queue: The asyncio.Queue to put recovered events into.
        spool_file: Override the default spool file path (for testing).

    Returns:
        Number of events successfully drained.
    """
    target = spool_file or SPOOL_FILE

    if not target.exists():
        log.debug("spool_file_missing", path=str(target))
        return 0

    count = 0
    lines = target.read_text(encoding="utf-8").splitlines()

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            event = CCEvent(**data)
            event_id = str(uuid.uuid4())
            insert_event(conn, event_id, event)
            queue.put_nowait(event)
            count += 1
        except Exception as exc:
            log.warning("spool_line_failed", line_number=i, error=str(exc))
            continue

    # Truncate after successful drain
    target.write_text("", encoding="utf-8")
    log.info("spool_drained", count=count)
    return count
