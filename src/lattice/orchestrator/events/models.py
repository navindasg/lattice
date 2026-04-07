"""Event channel Pydantic models.

All models are frozen (immutable after construction).

CCEvent represents a hook event from a Claude Code session.
ApprovalDecision is the orchestrator's response to an approval request.
HealthResponse is returned by the /health endpoint.
EventEnvelope wraps POST /events responses.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CCEvent(BaseModel):
    """A hook event emitted by a Claude Code session.

    Captures PreToolUse, PostToolUse, SessionStart, Stop, and other
    lifecycle events with optional tool metadata and transcript path.
    """

    session_id: str
    event_type: str
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_response: str | None = None
    transcript_path: str | None = None
    cwd: str | None = None
    timestamp: datetime

    model_config = {"frozen": True}


class ApprovalDecision(BaseModel):
    """Orchestrator decision on a tool-use approval request.

    approved=True allows the tool to proceed; False denies it.
    reason provides optional human-readable justification.
    """

    approved: bool
    reason: str | None = None

    model_config = {"frozen": True}


class HealthResponse(BaseModel):
    """Health check response from the event server.

    status is always "ok" when the server is running.
    uptime_seconds is wall-clock time since server start.
    connected_sessions is distinct session IDs seen.
    pending_events is count of unprocessed events.
    """

    status: str
    uptime_seconds: float
    connected_sessions: int
    pending_events: int

    model_config = {"frozen": True}


class EventEnvelope(BaseModel):
    """Wrapper returned from POST /events.

    event_id is a UUID4 string assigned by the server.
    accepted indicates the event was received (always True on success).
    """

    event_id: str
    accepted: bool = True

    model_config = {"frozen": True}
