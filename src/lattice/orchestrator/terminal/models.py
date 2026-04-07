"""Terminal backend data models: PaneInfo, CCInstance.

All models are frozen Pydantic models (immutable after construction).

PaneInfo represents information about a single terminal pane.
CCInstance represents a detected Claude Code instance running in a terminal pane.
"""
from __future__ import annotations

from pydantic import BaseModel


class PaneInfo(BaseModel):
    """Information about a single terminal pane.

    Captures identity, location, and state of a pane within the
    terminal multiplexer hierarchy (session > window > pane).
    """

    pane_id: str
    session_name: str
    window_name: str
    pane_index: int
    running_command: str
    cwd: str

    model_config = {"frozen": True}


class CCInstance(BaseModel):
    """A detected Claude Code instance running in a terminal pane.

    Extends pane identity with a stable user-facing number that persists
    across successive detect calls.  Numbers are never reused until a
    full rescan.
    """

    pane_id: str
    session_name: str
    window_name: str
    user_number: int
    running_command: str
    cwd: str

    model_config = {"frozen": True}
