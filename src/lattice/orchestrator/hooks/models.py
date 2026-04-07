"""Pydantic models for hook installer types.

All models are frozen (immutable after construction).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HookDefinition(BaseModel):
    """Definition of a single CC hook to be installed.

    Fields:
        event_type: CC hook event name (e.g. "PreToolUse", "PostToolUse").
        endpoint: URL path on the orchestrator (e.g. "/events/approval").
        is_async: Whether the hook fires asynchronously (non-blocking).
            PreToolUse is synchronous (blocks CC until orchestrator responds).
        timeout_seconds: Timeout for synchronous hooks (ignored for async).
        description: Human-readable description of what this hook does.
    """

    model_config = {"frozen": True}

    event_type: str
    endpoint: str
    is_async: bool
    timeout_seconds: int = 30
    description: str = ""


class HookEventStatus(BaseModel):
    """Status of a single hook event type after check.

    Fields:
        event_type: The CC event type name.
        installed: Whether a Lattice hook is installed for this event.
        reachable: Whether the orchestrator socket is reachable (None if not checked).
    """

    model_config = {"frozen": True}

    event_type: str
    installed: bool
    reachable: bool | None = None


class HookInstallResult(BaseModel):
    """Result of hook installation.

    Fields:
        success: Whether installation succeeded.
        installed_count: Number of hooks installed or updated.
        already_present: Number of hooks that were already installed (idempotent).
        settings_path: Path to the settings file that was modified.
        error: Error message if installation failed.
    """

    model_config = {"frozen": True}

    success: bool
    installed_count: int = 0
    already_present: int = 0
    settings_path: str = ""
    error: str | None = None


class HookUninstallResult(BaseModel):
    """Result of hook uninstallation.

    Fields:
        success: Whether uninstallation succeeded.
        removed_count: Number of hooks removed.
        settings_path: Path to the settings file that was modified.
        error: Error message if uninstallation failed.
    """

    model_config = {"frozen": True}

    success: bool
    removed_count: int = 0
    settings_path: str = ""
    error: str | None = None


class HookCheckResult(BaseModel):
    """Result of hook status check.

    Fields:
        all_installed: Whether all required hooks are installed.
        events: Per-event-type status.
        settings_path: Path to the settings file checked.
        error: Error message if check failed.
    """

    model_config = {"frozen": True}

    all_installed: bool
    events: list[HookEventStatus]
    settings_path: str = ""
    error: str | None = None
