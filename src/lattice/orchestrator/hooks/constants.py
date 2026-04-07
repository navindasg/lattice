"""Hook definitions and constants for the CC hook installer.

Defines the 6 hook event types that Lattice installs into CC settings:
    - SessionStart   (async) — CC session began
    - PreToolUse     (sync)  — CC is about to use a tool, blocks for approval
    - PostToolUse    (async) — CC finished using a tool
    - Stop           (async) — CC session is ending
    - PreCompact     (async) — CC is about to compact context
    - TaskCompleted  (async) — CC finished a task

The LATTICE_HOOK_MARKER is embedded in hook URLs to identify Lattice-managed
hooks during uninstall (prevents removal of user's custom hooks).
"""
from __future__ import annotations

from lattice.orchestrator.hooks.models import HookDefinition

# Marker string embedded in hook URLs to identify Lattice-managed hooks.
# Used by uninstall to selectively remove only Lattice hooks.
LATTICE_HOOK_MARKER = "lattice-orchestrator"

# All 6 event types that Lattice hooks into.
HOOK_EVENT_TYPES = frozenset({
    "SessionStart",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "PreCompact",
    "TaskCompleted",
})

# Complete hook definitions with endpoints and sync/async configuration.
CC_HOOK_DEFINITIONS: tuple[HookDefinition, ...] = (
    HookDefinition(
        event_type="SessionStart",
        endpoint="/events",
        is_async=True,
        description="Notify orchestrator when a CC session starts",
    ),
    HookDefinition(
        event_type="PreToolUse",
        endpoint="/events/approval",
        is_async=False,
        timeout_seconds=30,
        description="Block CC for orchestrator approval before tool execution",
    ),
    HookDefinition(
        event_type="PostToolUse",
        endpoint="/events",
        is_async=True,
        description="Notify orchestrator after tool execution completes",
    ),
    HookDefinition(
        event_type="Stop",
        endpoint="/events",
        is_async=True,
        description="Notify orchestrator when a CC session ends",
    ),
    HookDefinition(
        event_type="PreCompact",
        endpoint="/events",
        is_async=True,
        description="Notify orchestrator before context compaction",
    ),
    HookDefinition(
        event_type="TaskCompleted",
        endpoint="/events",
        is_async=True,
        description="Notify orchestrator when a CC task completes",
    ),
)
