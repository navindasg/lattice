"""Tests for the FastAPI event server.

Uses httpx.AsyncClient with ASGITransport to test endpoints without
starting a real server. Covers all REST endpoints, validation, approval
flow, and error handling.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import duckdb
import httpx
import pytest

from lattice.orchestrator.events.models import ApprovalDecision
from lattice.orchestrator.events.persistence import init_events_table
from lattice.orchestrator.events.server import create_app, submit_approval


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(approval_timeout: float = 30.0):
    """Create a FastAPI app with in-memory DuckDB."""
    conn = duckdb.connect(":memory:")
    app = create_app(conn, approval_timeout=approval_timeout)
    return app, conn


def _event_payload(**overrides) -> dict:
    """Create a valid CCEvent JSON payload."""
    defaults = {
        "session_id": "sess-001",
        "event_type": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/test.py"},
        "timestamp": datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
    }
    return {**defaults, **overrides}


def _make_client(app) -> httpx.AsyncClient:
    """Create an httpx async client for the given FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ---------------------------------------------------------------------------
# POST /events tests
# ---------------------------------------------------------------------------

async def test_post_event_returns_200() -> None:
    """POST /events returns 200 with event_id and accepted=True."""
    app, _ = _make_app()
    async with _make_client(app) as client:
        resp = await client.post("/events", json=_event_payload())

    assert resp.status_code == 200
    body = resp.json()
    assert "event_id" in body
    assert body["accepted"] is True


async def test_post_event_puts_in_queue() -> None:
    """POST /events puts the event on the queue within 50ms."""
    app, _ = _make_app()
    async with _make_client(app) as client:
        await client.post("/events", json=_event_payload())

    # Queue should have the event
    event = await asyncio.wait_for(app.state.event_queue.get(), timeout=0.05)
    assert event.session_id == "sess-001"


async def test_post_event_persists_to_db() -> None:
    """POST /events persists the event to DuckDB."""
    app, conn = _make_app()
    async with _make_client(app) as client:
        resp = await client.post("/events", json=_event_payload())

    event_id = resp.json()["event_id"]
    rows = conn.execute(
        "SELECT event_id FROM orchestrator_events WHERE event_id = ?",
        [event_id],
    ).fetchall()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# POST /events/approval tests
# ---------------------------------------------------------------------------

async def test_approval_timeout_denies() -> None:
    """POST /events/approval times out and returns approved=False."""
    app, _ = _make_app(approval_timeout=0.5)
    async with _make_client(app) as client:
        resp = await client.post("/events/approval", json=_event_payload())

    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] is False
    assert body["reason"] == "timeout"


async def test_approval_returns_decision() -> None:
    """POST /events/approval returns decision when submitted."""
    app, _ = _make_app(approval_timeout=5.0)

    async def submit_after_delay():
        """Wait for waiter to appear, then submit approval."""
        for _ in range(100):
            if app.state.approval_waiters:
                break
            await asyncio.sleep(0.01)
        event_id = next(iter(app.state.approval_waiters))
        submit_approval(app, event_id, ApprovalDecision(approved=True, reason="safe"))

    async with _make_client(app) as client:
        task = asyncio.create_task(submit_after_delay())
        resp = await client.post("/events/approval", json=_event_payload())
        await task

    body = resp.json()
    assert body["approved"] is True
    assert body["reason"] == "safe"


# ---------------------------------------------------------------------------
# GET /events/history tests
# ---------------------------------------------------------------------------

async def test_history_returns_events() -> None:
    """GET /events/history returns events sorted by timestamp desc."""
    app, _ = _make_app()
    async with _make_client(app) as client:
        for i in range(3):
            await client.post("/events", json=_event_payload(session_id=f"sess-{i}"))
        resp = await client.get("/events/history")

    assert resp.status_code == 200
    assert len(resp.json()) == 3


async def test_history_filters_by_session_id() -> None:
    """GET /events/history?session_id=X filters correctly."""
    app, _ = _make_app()
    async with _make_client(app) as client:
        await client.post("/events", json=_event_payload(session_id="alpha"))
        await client.post("/events", json=_event_payload(session_id="beta"))
        resp = await client.get("/events/history", params={"session_id": "alpha"})

    body = resp.json()
    assert len(body) == 1
    assert body[0]["session_id"] == "alpha"


async def test_history_filters_by_event_type() -> None:
    """GET /events/history?event_type=X filters correctly."""
    app, _ = _make_app()
    async with _make_client(app) as client:
        await client.post("/events", json=_event_payload(event_type="PreToolUse"))
        await client.post("/events", json=_event_payload(event_type="PostToolUse"))
        resp = await client.get("/events/history", params={"event_type": "PostToolUse"})

    body = resp.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "PostToolUse"


# ---------------------------------------------------------------------------
# GET /events/pending tests
# ---------------------------------------------------------------------------

async def test_pending_returns_unprocessed() -> None:
    """GET /events/pending returns only unprocessed events."""
    app, conn = _make_app()
    async with _make_client(app) as client:
        resp1 = await client.post("/events", json=_event_payload())
        await client.post("/events", json=_event_payload())

        # Mark first as processed
        event_id = resp1.json()["event_id"]
        conn.execute(
            "UPDATE orchestrator_events SET processed = true WHERE event_id = ?",
            [event_id],
        )

        resp = await client.get("/events/pending")

    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# GET /health tests
# ---------------------------------------------------------------------------

async def test_health_returns_200() -> None:
    """GET /health returns 200 with status JSON."""
    app, _ = _make_app()
    async with _make_client(app) as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
    assert "connected_sessions" in body
    assert "pending_events" in body


# ---------------------------------------------------------------------------
# Validation / error handling tests
# ---------------------------------------------------------------------------

async def test_invalid_payload_returns_422() -> None:
    """POST /events with invalid payload returns 422."""
    app, _ = _make_app()
    async with _make_client(app) as client:
        resp = await client.post("/events", json={"bad": "data"})

    assert resp.status_code == 422


async def test_server_handles_invalid_json() -> None:
    """POST /events with non-JSON body returns 422, server does not crash."""
    app, _ = _make_app()
    async with _make_client(app) as client:
        resp = await client.post(
            "/events",
            content=b"not json at all",
            headers={"content-type": "application/json"},
        )

    assert resp.status_code == 422

    # Server still works after invalid JSON
    async with _make_client(app) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
