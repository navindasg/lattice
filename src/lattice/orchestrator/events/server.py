"""FastAPI application for the event channel.

Provides REST endpoints for CC hook events:
- POST /events — fire-and-forget event ingestion
- POST /events/approval — synchronous approval flow with timeout
- GET /events/history — filtered event history
- GET /events/pending — unprocessed events
- GET /health — server health check

Use create_app(db_conn) to build an app instance with a DuckDB connection.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import duckdb
import structlog
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from lattice.orchestrator.events.models import (
    ApprovalDecision,
    CCEvent,
    EventEnvelope,
    HealthResponse,
)
from lattice.orchestrator.events.persistence import (
    count_pending,
    count_sessions,
    get_history,
    get_pending,
    init_events_table,
    insert_event,
)

log = structlog.get_logger(__name__)

DEFAULT_APPROVAL_TIMEOUT: float = 30.0


def create_app(
    db_conn: duckdb.DuckDBPyConnection,
    *,
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT,
) -> FastAPI:
    """Create a FastAPI application wired to the given DuckDB connection.

    The app exposes an event_queue (asyncio.Queue) for orchestrator
    consumption, and approval waiters for synchronous approval flow.

    Args:
        db_conn: An open DuckDB connection.
        approval_timeout: Seconds to wait for an approval decision (default 30).

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(title="Lattice Event Channel")

    # Shared state attached to app
    app.state.db_conn = db_conn
    app.state.db_lock: asyncio.Lock = asyncio.Lock()
    app.state.event_queue: asyncio.Queue = asyncio.Queue()
    app.state.start_time: float = time.monotonic()
    app.state.approval_waiters: dict[str, asyncio.Event] = {}
    app.state.approval_decisions: dict[str, ApprovalDecision] = {}
    app.state.approval_timeout: float = approval_timeout

    init_events_table(db_conn)

    @app.post("/events", response_model=EventEnvelope)
    async def post_event(event: CCEvent) -> EventEnvelope:
        """Ingest a fire-and-forget hook event.

        Generates a UUID4 event_id, persists to DuckDB, and puts the
        event on the in-memory queue for orchestrator consumption.
        """
        event_id = str(uuid.uuid4())
        async with app.state.db_lock:
            insert_event(app.state.db_conn, event_id, event)
        await app.state.event_queue.put(event)
        return EventEnvelope(event_id=event_id, accepted=True)

    @app.post("/events/approval", response_model=ApprovalDecision)
    async def post_approval(event: CCEvent) -> ApprovalDecision:
        """Submit a tool-use event that requires approval.

        Persists the event, then waits up to approval_timeout seconds
        for the orchestrator to call submit_approval(). Returns a deny
        decision on timeout.
        """
        event_id = str(uuid.uuid4())
        async with app.state.db_lock:
            insert_event(app.state.db_conn, event_id, event)

        waiter = asyncio.Event()
        app.state.approval_waiters[event_id] = waiter

        try:
            await asyncio.wait_for(
                waiter.wait(),
                timeout=app.state.approval_timeout,
            )
            decision = app.state.approval_decisions.get(
                event_id,
                ApprovalDecision(approved=False, reason="timeout"),
            )
        except asyncio.TimeoutError:
            decision = ApprovalDecision(approved=False, reason="timeout")
            log.warning("approval_timeout", event_id=event_id)
        finally:
            app.state.approval_waiters.pop(event_id, None)
            app.state.approval_decisions.pop(event_id, None)

        return decision

    @app.get("/events/history")
    async def get_event_history(
        session_id: str | None = None,
        event_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict]:
        """Query event history with optional filters.

        Args:
            session_id: Filter by session ID.
            event_type: Filter by event type.
            limit: Maximum results (default 100).
        """
        async with app.state.db_lock:
            return get_history(
                app.state.db_conn,
                session_id=session_id,
                event_type=event_type,
                limit=limit,
            )

    @app.get("/events/pending")
    async def get_pending_events() -> list[dict]:
        """Return all unprocessed events, up to 1000."""
        async with app.state.db_lock:
            return get_pending(app.state.db_conn, limit=1000)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Return server health metrics."""
        elapsed = time.monotonic() - app.state.start_time
        async with app.state.db_lock:
            sessions = count_sessions(app.state.db_conn)
            pending_count = count_pending(app.state.db_conn)
        return HealthResponse(
            status="ok",
            uptime_seconds=round(elapsed, 2),
            connected_sessions=sessions,
            pending_events=pending_count,
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch unhandled exceptions so the server never crashes."""
        log.error("unhandled_exception", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


def submit_approval(app: FastAPI, event_id: str, decision: ApprovalDecision) -> bool:
    """Resolve a pending approval request.

    Called by the orchestrator to approve or deny a tool-use event.

    Args:
        app: The FastAPI app instance.
        event_id: The event_id returned by POST /events/approval.
        decision: The approval decision.

    Returns:
        True if a waiter was found and notified, False otherwise.
    """
    waiter = app.state.approval_waiters.get(event_id)
    if waiter is None:
        log.warning("approval_no_waiter", event_id=event_id)
        return False

    app.state.approval_decisions[event_id] = decision
    waiter.set()
    log.info("approval_submitted", event_id=event_id, approved=decision.approved)
    return True
