"""LangGraph state schema for the orchestrator agent.

Defines the typed state that flows through the supervisor graph.
Uses TypedDict with Annotated fields for LangGraph's message reducer.
"""
from __future__ import annotations

from typing import Annotated, Any, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel

from lattice.orchestrator.events.models import CCEvent


class PendingApproval(BaseModel):
    """A tool-use approval request waiting for orchestrator decision.

    Fields:
        event_id: UUID of the event from the event channel.
        instance: User-facing CC instance number (1-9).
        tool_name: The CC tool requesting approval.
        tool_input: Structured input to the tool.
        file_path: Primary file path involved (extracted from tool_input).
        timestamp: ISO 8601 timestamp of the event.
    """

    model_config = {"frozen": True}

    event_id: str
    instance: str
    tool_name: str
    tool_input: dict[str, Any] = {}
    file_path: str = ""
    timestamp: str = ""


class InstanceInfo(BaseModel):
    """Tracked state for a single CC instance.

    Fields:
        instance_id: User-facing instance number as string.
        pane_id: Tmux pane ID (e.g. "%0").
        task: Current task description.
        status: Current status ("active", "idle", "blocked", "spawning").
        assigned_at: ISO 8601 timestamp when task was assigned.
    """

    model_config = {"frozen": True}

    instance_id: str
    pane_id: str = ""
    task: str = ""
    status: str = "idle"
    assigned_at: str = ""


from typing import TypedDict


class AgentState(TypedDict):
    """LangGraph-compatible state dict for the orchestrator agent."""

    messages: Annotated[Sequence[BaseMessage], add_messages]
    instances: dict[str, dict[str, Any]]
    pending_approvals: dict[str, dict[str, Any]]
    plan: list[str]
    last_event: dict[str, Any] | None
