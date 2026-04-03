"""Connector data models: ConnectorPermissions, ConnectorConfig, ConnectorState, ConnectorResult.

All models are frozen Pydantic models (immutable after construction).

ConnectorConfig holds per-connector settings including breaker config and credentials.
ConnectorState tracks runtime status of a connector in the registry.
ConnectorResult is the return type of fetch/write operations.
ConnectorPermissions controls read/write access for a connector.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from lattice.orchestrator.models import BreakerConfig


class ConnectorPermissions(BaseModel):
    """Read/write permissions for a connector.

    Defaults to read-only (read=True, write=False).
    Frozen — use model_copy(update=...) to create updated instances.
    """

    read: bool = True
    write: bool = False

    model_config = {"frozen": True}


class ConnectorConfig(BaseModel):
    """Per-connector configuration.

    Includes credentials, routing hints, and per-connector circuit breaker config.
    Frozen — all fields are set at construction time.

    Supported connector types: tavily, github, mattermost.
    """

    name: str
    connector_type: Literal["tavily", "github", "mattermost"]
    enabled: bool = True
    permissions: ConnectorPermissions = Field(default_factory=ConnectorPermissions)

    # Credentials (type-specific — only relevant fields will be set)
    api_key: str = ""
    token: str = ""
    repo: str = ""
    base_url: str = ""
    channel_ids: list[str] = Field(default_factory=list)

    # Operational config
    polling_interval_seconds: int = 60
    breaker_cooldown_seconds: int = 120
    breaker: BreakerConfig = Field(
        default_factory=lambda: BreakerConfig(
            consecutive_error_limit=3,
            wall_clock_timeout_seconds=30,
            iteration_cap=100,
        )
    )

    model_config = {"frozen": True}


class ConnectorState(BaseModel):
    """Runtime state of a registered connector.

    Stored in DuckDB connector_registry table.
    Frozen — transitions produce new instances via model_copy.
    """

    name: str
    connector_type: str
    status: Literal["online", "offline", "tripped"]
    last_used: str | None = None
    trip_time: str | None = None
    registered_at: str

    model_config = {"frozen": True}


class ConnectorResult(BaseModel):
    """Result of a connector fetch or write operation.

    success=False with error set indicates failure.
    delivery_mode controls how content is consumed downstream.
    Frozen — immutable after construction.
    """

    success: bool
    source: str
    content: str = ""
    error: str = ""
    delivery_mode: Literal["ndjson", "soul_file"] = "ndjson"
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = {"frozen": True}
