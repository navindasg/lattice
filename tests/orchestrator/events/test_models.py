"""Tests for event channel Pydantic models.

Covers:
- CCEvent creation with all fields and optional fields
- Frozen model immutability
- Invalid types rejected by Pydantic validation
- ApprovalDecision, HealthResponse, EventEnvelope construction
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from lattice.orchestrator.events.models import (
    ApprovalDecision,
    CCEvent,
    EventEnvelope,
    HealthResponse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(**overrides) -> CCEvent:
    """Create a CCEvent with sensible defaults, applying any overrides."""
    defaults = {
        "session_id": "sess-001",
        "event_type": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/test.py"},
        "tool_response": "file contents here",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/home/user/project",
        "timestamp": datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    }
    return CCEvent(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# CCEvent tests
# ---------------------------------------------------------------------------

def test_ccevent_all_fields() -> None:
    """CCEvent accepts all fields and stores them correctly."""
    event = _make_event()
    assert event.session_id == "sess-001"
    assert event.event_type == "PreToolUse"
    assert event.tool_name == "Read"
    assert event.tool_input == {"file_path": "/tmp/test.py"}
    assert event.tool_response == "file contents here"
    assert event.transcript_path == "/tmp/transcript.jsonl"
    assert event.cwd == "/home/user/project"
    assert event.timestamp == datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_ccevent_optional_fields_none() -> None:
    """CCEvent optional fields default to None when omitted."""
    event = CCEvent(
        session_id="sess-002",
        event_type="SessionStart",
        timestamp=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert event.tool_name is None
    assert event.tool_input is None
    assert event.tool_response is None
    assert event.transcript_path is None
    assert event.cwd is None


def test_ccevent_frozen() -> None:
    """CCEvent is frozen — attributes cannot be mutated."""
    event = _make_event()
    with pytest.raises(ValidationError):
        event.session_id = "new-session"


def test_ccevent_invalid_types_rejected() -> None:
    """CCEvent rejects invalid types (e.g., non-string session_id)."""
    with pytest.raises(ValidationError):
        CCEvent(
            session_id=12345,  # should be str
            event_type="PreToolUse",
            timestamp="not-a-datetime",
        )


# ---------------------------------------------------------------------------
# ApprovalDecision tests
# ---------------------------------------------------------------------------

def test_approval_decision_approved() -> None:
    """ApprovalDecision with approved=True."""
    decision = ApprovalDecision(approved=True, reason="safe tool")
    assert decision.approved is True
    assert decision.reason == "safe tool"


def test_approval_decision_denied_no_reason() -> None:
    """ApprovalDecision with approved=False and no reason."""
    decision = ApprovalDecision(approved=False)
    assert decision.approved is False
    assert decision.reason is None


def test_approval_decision_frozen() -> None:
    """ApprovalDecision is frozen."""
    decision = ApprovalDecision(approved=True)
    with pytest.raises(ValidationError):
        decision.approved = False


# ---------------------------------------------------------------------------
# HealthResponse tests
# ---------------------------------------------------------------------------

def test_health_response() -> None:
    """HealthResponse stores all fields correctly."""
    health = HealthResponse(
        status="ok",
        uptime_seconds=42.5,
        connected_sessions=3,
        pending_events=7,
    )
    assert health.status == "ok"
    assert health.uptime_seconds == 42.5
    assert health.connected_sessions == 3
    assert health.pending_events == 7


def test_health_response_frozen() -> None:
    """HealthResponse is frozen."""
    health = HealthResponse(status="ok", uptime_seconds=0, connected_sessions=0, pending_events=0)
    with pytest.raises(ValidationError):
        health.status = "bad"


# ---------------------------------------------------------------------------
# EventEnvelope tests
# ---------------------------------------------------------------------------

def test_event_envelope() -> None:
    """EventEnvelope stores event_id and defaults accepted to True."""
    envelope = EventEnvelope(event_id="abc-123")
    assert envelope.event_id == "abc-123"
    assert envelope.accepted is True


def test_event_envelope_frozen() -> None:
    """EventEnvelope is frozen."""
    envelope = EventEnvelope(event_id="abc-123")
    with pytest.raises(ValidationError):
        envelope.event_id = "new-id"
