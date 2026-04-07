"""Integration tests for the event channel.

Covers:
- Full flow: start server → send events → verify history + queue
- Concurrent producers: 6 coroutines sending ~17 events each (100 total)
- Spool drain: insert into spool → create app → drain → verify in history
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import duckdb
import httpx
import pytest

from lattice.orchestrator.events.models import CCEvent
from lattice.orchestrator.events.persistence import get_history, init_events_table
from lattice.orchestrator.events.server import create_app
from lattice.orchestrator.events.spool import append_to_spool, drain_spool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app():
    """Create a FastAPI app with in-memory DuckDB."""
    conn = duckdb.connect(":memory:")
    app = create_app(conn)
    return app, conn


def _event_payload(session_id: str = "sess-001", event_type: str = "PreToolUse") -> dict:
    """Create a valid CCEvent JSON payload."""
    return {
        "session_id": session_id,
        "event_type": event_type,
        "timestamp": datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
    }


def _make_client(app) -> httpx.AsyncClient:
    """Create an httpx async client for the given FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ---------------------------------------------------------------------------
# Full flow test
# ---------------------------------------------------------------------------

async def test_full_flow_send_and_verify() -> None:
    """Send 10 events, verify all appear in history and queue."""
    app, conn = _make_app()

    async with _make_client(app) as client:
        for i in range(10):
            resp = await client.post("/events", json=_event_payload(session_id=f"sess-{i}"))
            assert resp.status_code == 200

    # Verify history
    rows = get_history(conn)
    assert len(rows) == 10

    # Verify queue
    assert app.state.event_queue.qsize() == 10


# ---------------------------------------------------------------------------
# Concurrent producers test
# ---------------------------------------------------------------------------

async def test_concurrent_producers_no_drops() -> None:
    """6 producers sending ~17 events each (100 total) — no drops or duplicates."""
    app, conn = _make_app()
    total_events = 102  # 6 * 17
    events_per_producer = 17

    async def producer(producer_id: int, client: httpx.AsyncClient) -> list[str]:
        """Send events_per_producer events and return their event_ids."""
        event_ids = []
        for i in range(events_per_producer):
            resp = await client.post(
                "/events",
                json=_event_payload(
                    session_id=f"producer-{producer_id}",
                    event_type=f"event-{i}",
                ),
            )
            assert resp.status_code == 200
            event_ids.append(resp.json()["event_id"])
        return event_ids

    async with _make_client(app) as client:
        tasks = [
            asyncio.create_task(producer(pid, client))
            for pid in range(6)
        ]
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)

    all_ids = [eid for batch in results for eid in batch]

    # No drops
    assert len(all_ids) == total_events

    # No duplicates
    assert len(set(all_ids)) == total_events

    # All in DB
    rows = get_history(conn, limit=200)
    assert len(rows) == total_events

    # All in queue
    assert app.state.event_queue.qsize() == total_events


# ---------------------------------------------------------------------------
# Spool drain integration test
# ---------------------------------------------------------------------------

async def test_spool_drain_into_history(tmp_path) -> None:
    """Insert events to spool, create app, drain, verify in history."""
    spool_file = tmp_path / "events.jsonl"
    conn = duckdb.connect(":memory:")
    app = create_app(conn)

    # Spool 5 events before app "starts"
    for i in range(5):
        event = CCEvent(
            session_id=f"spool-sess-{i}",
            event_type="PreToolUse",
            timestamp=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        append_to_spool(event, spool_file=spool_file)

    # Drain spool into app
    drained = drain_spool(conn, app.state.event_queue, spool_file=spool_file)
    assert drained == 5

    # Verify in history
    rows = get_history(conn)
    assert len(rows) == 5

    # Verify in queue
    assert app.state.event_queue.qsize() == 5

    # Spool file should be empty
    content = spool_file.read_text(encoding="utf-8")
    assert content == ""
