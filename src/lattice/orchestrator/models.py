"""Orchestrator data models: TaskRecord, CircuitBreakerState, BreakerConfig, OrchestratorConfig.

All models are frozen Pydantic models (immutable after construction).

TaskRecord represents a unit of work to be assigned to a CC instance.
CircuitBreakerState tracks per-instance circuit breaker status.
BreakerConfig holds circuit breaker thresholds.
OrchestratorConfig holds fleet-wide orchestrator settings.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class TaskRecord(BaseModel):
    """A unit of work to be dispatched to a managed CC instance.

    task_id defaults to a new UUID4 string.
    priority defaults to "normal".
    status defaults to "pending".
    payload is a JSON string of task data.
    """

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    instance_id: str | None = None
    priority: Literal["high", "normal", "low"] = "normal"
    status: Literal["pending", "assigned", "running", "completed", "failed", "cancelled"] = "pending"
    payload: str  # JSON string of task data
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    assigned_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    project_id: str | None = None

    model_config = {"frozen": True}


class CircuitBreakerState(BaseModel):
    """Per-instance circuit breaker state.

    Tracks iteration count, consecutive errors, trip status and reason.
    Frozen — use model_copy(update=...) to create updated instances.
    """

    instance_id: str
    iteration_count: int = 0
    consecutive_errors: int = 0
    tripped: bool = False
    trip_reason: Literal["iteration_cap", "wall_clock", "error_limit"] | None = None

    model_config = {"frozen": True}


class BreakerConfig(BaseModel):
    """Circuit breaker thresholds.

    Defaults:
        iteration_cap: 50 iterations before tripping
        wall_clock_timeout_seconds: 1800s (30 min) wall-clock limit
        consecutive_error_limit: 3 consecutive errors before tripping
    """

    iteration_cap: int = 50
    wall_clock_timeout_seconds: int = 1800
    consecutive_error_limit: int = 3


class OrchestratorConfig(BaseModel):
    """Fleet-wide orchestrator settings.

    Defaults:
        max_instances: 3 concurrent CC instances
        idle_timeout_seconds: 60s before idle instance is reclaimed
        max_queue_depth: 20 pending tasks before rejecting new ones
        breaker: BreakerConfig with defaults
    """

    max_instances: int = 3
    idle_timeout_seconds: int = 60
    max_queue_depth: int = 20
    breaker: BreakerConfig = Field(default_factory=BreakerConfig)


class ContextManagerConfig(BaseModel):
    """Configuration for per-instance context utilization monitoring.

    Defaults:
        compaction_threshold: 55.0% utilization triggers compaction
        window_tokens: 128_000 token context window size
        verification_enabled: True — verify compacted soul before committing
    """

    compaction_threshold: float = 55.0
    window_tokens: int = 128_000
    verification_enabled: bool = True
