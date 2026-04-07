"""Event channel subpackage for CC hook event ingestion.

Provides models, persistence, crash-resilient spooling, a FastAPI
application, and a UDS server runner for receiving and processing
Claude Code hook events.
"""
from lattice.orchestrator.events.models import (
    ApprovalDecision,
    CCEvent,
    EventEnvelope,
    HealthResponse,
)
from lattice.orchestrator.events.runner import EventServer
from lattice.orchestrator.events.server import create_app, submit_approval
from lattice.orchestrator.events.spool import append_to_spool, drain_spool

__all__ = [
    "ApprovalDecision",
    "CCEvent",
    "EventEnvelope",
    "EventServer",
    "HealthResponse",
    "append_to_spool",
    "create_app",
    "drain_spool",
    "submit_approval",
]
