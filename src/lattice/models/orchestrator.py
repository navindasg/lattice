"""Orchestrator models: ManagedInstance and MapperCommand.

ManagedInstance tracks a running Claude Code worker process.
MapperCommand represents a typed command sent to a mapping session.

Both models are frozen (immutable after construction).
"""
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class ManagedInstance(BaseModel):
    """A managed Claude Code worker instance.

    id defaults to a new UUID4 string.
    status defaults to "idle".
    pid and task_id default to None until the process starts.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    pid: int | None = None
    task_id: str | None = None
    status: Literal["idle", "running", "stopped", "crashed"] = "idle"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_heartbeat: str | None = None
    error_reason: str | None = None
    project_id: str | None = None

    model_config = {"frozen": True}


class MapperCommand(BaseModel):
    """A typed command sent to a mapping session.

    command must be one of the supported mapper commands.
    args defaults to an empty dict.
    session_id defaults to None.
    """

    command: Literal[
        "map:init",
        "map:hint",
        "map:status",
        "map:stop",
        "map:doc",
        "map:gaps",
        "map:cross",
        "map:correct",
        "map:skip",
        "map:queue",
        "map:test-status",
    ]
    args: dict = Field(default_factory=dict)
    session_id: str | None = None

    model_config = {"frozen": True}
