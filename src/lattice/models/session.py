"""Session model: MappingSession.

Represents a single codebase mapping run with its lifecycle state.
The model is frozen (immutable after construction).
"""
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class MappingSession(BaseModel):
    """A codebase mapping session tracking its lifecycle.

    Required fields: target_path.
    id defaults to a new UUID4 string.
    status defaults to "pending".
    started_at defaults to utcnow; completed_at defaults to None.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_path: str
    status: Literal["pending", "running", "complete", "failed"] = "pending"
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    completed_at: datetime | None = None

    model_config = {"frozen": True}
