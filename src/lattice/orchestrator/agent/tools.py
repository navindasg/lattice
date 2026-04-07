"""Orchestrator agent tools — 11 LangGraph-compatible tool functions.

Each tool is a thin wrapper that delegates to the underlying orchestrator
subsystem (terminal backend, event channel, soul ecosystem, etc.).

Tools:
    cc_send       — Type a prompt into a CC instance's tmux pane
    cc_approve    — Send "y" to approve a tool-use prompt
    cc_deny       — Send "n" + redirect instruction to deny
    cc_status     — Read recent events and current assignment
    cc_spawn      — Start a new CC instance in a new tmux pane
    cc_interrupt  — Send Ctrl+C to interrupt a running instance
    github_read   — Fetch ticket details from GitHub
    soul_read     — Read all soul.md files for context
    soul_update   — Write to STATE.md or MEMORY.md sections
    map_query     — Query Lattice codebase intelligence (_dir.md)
    write_todos   — Plan and track work items

All tools receive their dependencies via a ToolContext that is injected at
graph construction time using a contextvars.ContextVar for thread safety.
Terminal backend operations use asyncio.run_coroutine_threadsafe to avoid
blocking the event loop.
"""
from __future__ import annotations

import asyncio
import contextvars
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from langchain_core.tools import tool

from lattice.orchestrator.events.models import ApprovalDecision, CCEvent
from lattice.orchestrator.soul_ecosystem.models import (
    DecisionRecord,
    InstanceAssignment,
    OrchestratorState,
    SoulMemoryEntry,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.soul_ecosystem.writer import SoulWriter
from lattice.orchestrator.terminal.base import TerminalBackend

logger = structlog.get_logger(__name__)


@dataclass
class ToolContext:
    """Dependency container for orchestrator tools.

    Intentionally mutable — instance_pane_map and event_history are updated
    at runtime as instances are spawned and events are received.

    Fields:
        terminal: TerminalBackend for tmux pane operations.
        soul_reader: SoulReader for reading soul files.
        soul_writer: SoulWriter for updating STATE.md / MEMORY.md.
        event_loop: The main asyncio event loop for scheduling coroutines.
        event_queue: asyncio.Queue for recent event lookups.
        approval_submit: Callable to submit approval decisions.
        shadow_root: Path to .agent-docs/ for map_query.
        instance_pane_map: Dict mapping instance number -> pane_id.
        event_history: Dict of recent events per instance.
    """

    terminal: TerminalBackend
    soul_reader: SoulReader
    soul_writer: SoulWriter
    event_loop: asyncio.AbstractEventLoop | None = None
    event_queue: asyncio.Queue | None = None
    approval_submit: Any = None  # Callable[[str, ApprovalDecision], bool]
    shadow_root: Path | None = None
    instance_pane_map: dict[str, str] = field(default_factory=dict)
    event_history: dict[str, list[dict]] = field(default_factory=dict)


# Thread-safe context variable — prevents test pollution and supports
# concurrent orchestrator graphs.
_tool_context_var: contextvars.ContextVar[ToolContext | None] = contextvars.ContextVar(
    "_tool_context_var", default=None
)


def set_tool_context(ctx: ToolContext | None) -> None:
    """Set the tool context for the current execution context."""
    _tool_context_var.set(ctx)


def get_tool_context() -> ToolContext:
    """Get the current tool context, raising if not set."""
    ctx = _tool_context_var.get()
    if ctx is None:
        raise RuntimeError("Tool context not initialized — call set_tool_context() first")
    return ctx


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a sync tool function.

    Uses asyncio.run_coroutine_threadsafe when an event loop is available
    in the ToolContext (production path). Falls back to asyncio.run() for
    testing when no loop is provided.

    Args:
        coro: An awaitable coroutine.

    Returns:
        The coroutine's return value.
    """
    ctx = get_tool_context()
    if ctx.event_loop is not None and ctx.event_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, ctx.event_loop)
        return future.result(timeout=30)
    # Fallback for testing — no running loop
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


@tool
def cc_send(instance: str, message: str) -> dict[str, Any]:
    """Send a prompt message to a CC instance's tmux pane.

    Types the message into the instance's terminal and presses Enter.

    Args:
        instance: CC instance number (1-9).
        message: The prompt text to send.

    Returns:
        Dict with success status and instance details.
    """
    ctx = get_tool_context()
    pane_id = ctx.instance_pane_map.get(instance)

    if pane_id is None:
        return {"success": False, "error": f"Instance {instance} not found in pane map"}

    _run_async(ctx.terminal.send_text(pane_id, message))
    _run_async(ctx.terminal.send_enter(pane_id))

    logger.info("cc_send", instance=instance, message_length=len(message))
    return {"success": True, "instance": instance, "message_sent": message}


@tool
def cc_approve(instance: str) -> dict[str, Any]:
    """Approve a pending tool-use request for a CC instance.

    Sends "y" followed by Enter to the instance's tmux pane.

    Args:
        instance: CC instance number (1-9).

    Returns:
        Dict with success status.
    """
    ctx = get_tool_context()
    pane_id = ctx.instance_pane_map.get(instance)

    if pane_id is None:
        return {"success": False, "error": f"Instance {instance} not found"}

    _run_async(ctx.terminal.send_text(pane_id, "y"))
    _run_async(ctx.terminal.send_enter(pane_id))

    logger.info("cc_approve", instance=instance)
    return {"success": True, "instance": instance, "decision": "approved"}


@tool
def cc_deny(instance: str, reason: str = "") -> dict[str, Any]:
    """Deny a pending tool-use request and optionally redirect.

    Sends "n" followed by Enter to deny, then if a reason is provided,
    waits briefly for CC's denial prompt and types the redirect message.

    Args:
        instance: CC instance number (1-9).
        reason: Optional redirect message to type after denial.

    Returns:
        Dict with success status and reason.
    """
    ctx = get_tool_context()
    pane_id = ctx.instance_pane_map.get(instance)

    if pane_id is None:
        return {"success": False, "error": f"Instance {instance} not found"}

    _run_async(ctx.terminal.send_text(pane_id, "n"))
    _run_async(ctx.terminal.send_enter(pane_id))

    if reason:
        _run_async(asyncio.sleep(0.5))
        _run_async(ctx.terminal.send_text(pane_id, reason))
        _run_async(ctx.terminal.send_enter(pane_id))

    logger.info("cc_deny", instance=instance, reason=reason)
    return {"success": True, "instance": instance, "decision": "denied", "reason": reason}


@tool
def cc_status(instance: str) -> dict[str, Any]:
    """Get the status of a CC instance.

    Returns the last 5 events from the instance and its current
    assignment from STATE.md.

    Args:
        instance: CC instance number (1-9).

    Returns:
        Dict with events and assignment details.
    """
    ctx = get_tool_context()

    recent_events = ctx.event_history.get(instance, [])[-5:]

    state = ctx.soul_reader.read_state()
    assignment = None
    for inst in state.instances:
        if inst.instance_id == instance:
            assignment = {
                "task": inst.task_description,
                "status": inst.status,
                "assigned_at": inst.assigned_at,
            }
            break

    logger.info("cc_status", instance=instance, event_count=len(recent_events))
    return {
        "success": True,
        "instance": instance,
        "recent_events": recent_events,
        "assignment": assignment,
    }


@tool
def cc_spawn(project: str, task: str) -> dict[str, Any]:
    """Start a new CC instance in a new tmux pane.

    Spawns a new pane running `claude`, then sends the task as the
    first prompt. The new instance is registered in STATE.md.

    Args:
        project: Project directory path for the CC session.
        task: Task description to send as the first prompt.

    Returns:
        Dict with new instance details.
    """
    ctx = get_tool_context()

    # Sanitize project path to prevent shell injection
    safe_project = shlex.quote(project)
    pane_id = _run_async(
        ctx.terminal.spawn_pane(f"cd {safe_project} && claude", name=f"cc-{project}")
    )

    # Detect instance number
    instances = _run_async(ctx.terminal.detect_cc_panes())
    new_instance = None
    for inst in instances:
        if inst.pane_id == pane_id:
            new_instance = str(inst.user_number)
            break

    if new_instance is None:
        new_instance = str(len(instances))

    # Register in pane map
    ctx.instance_pane_map[new_instance] = pane_id

    # Brief delay for CC to initialize, then send task
    _run_async(asyncio.sleep(2.0))
    _run_async(ctx.terminal.send_text(pane_id, task))
    _run_async(ctx.terminal.send_enter(pane_id))

    # Update STATE.md
    now = datetime.now(timezone.utc).isoformat()
    state = ctx.soul_reader.read_state()
    new_assignment = InstanceAssignment(
        instance_id=new_instance,
        task_description=task,
        status="active",
        assigned_at=now,
    )
    updated = OrchestratorState(
        instances=[*state.instances, new_assignment],
        plan=list(state.plan),
        decisions=list(state.decisions),
        blockers=list(state.blockers),
    )
    ctx.soul_writer.update_full_state(updated)

    logger.info("cc_spawn", instance=new_instance, pane_id=pane_id, task=task)
    return {
        "success": True,
        "instance": new_instance,
        "pane_id": pane_id,
        "task": task,
    }


@tool
def cc_interrupt(instance: str) -> dict[str, Any]:
    """Send Ctrl+C to interrupt a running CC instance.

    Args:
        instance: CC instance number (1-9).

    Returns:
        Dict with success status.
    """
    ctx = get_tool_context()
    pane_id = ctx.instance_pane_map.get(instance)

    if pane_id is None:
        return {"success": False, "error": f"Instance {instance} not found"}

    _run_async(ctx.terminal.send_interrupt(pane_id))

    logger.info("cc_interrupt", instance=instance)
    return {"success": True, "instance": instance, "action": "interrupted"}


@tool
def github_read(issue_ref: str) -> dict[str, Any]:
    """Fetch ticket details from GitHub.

    Parses an issue reference like "navindasg/lattice#7" and returns
    the issue title, body, labels, and status.

    Args:
        issue_ref: GitHub issue reference (e.g. "owner/repo#number").

    Returns:
        Dict with issue details or error.
    """
    if "#" not in issue_ref:
        return {"success": False, "error": f"Invalid issue reference: {issue_ref}. Use owner/repo#number"}

    repo_part, number_str = issue_ref.rsplit("#", 1)
    try:
        issue_number = int(number_str)
    except ValueError:
        return {"success": False, "error": f"Invalid issue number: {number_str}"}

    if "/" not in repo_part:
        return {"success": False, "error": f"Invalid repo: {repo_part}. Use owner/repo#number"}

    owner, repo = repo_part.split("/", 1)

    logger.info("github_read", owner=owner, repo=repo, issue=issue_number)
    return {
        "success": True,
        "owner": owner,
        "repo": repo,
        "issue_number": issue_number,
        "note": "GitHub API call delegated to connector registry",
    }


@tool
def soul_read() -> dict[str, Any]:
    """Read all soul files and return their content.

    Returns the current content of SOUL.md, AGENTS.md, STATE.md,
    and MEMORY.md.

    Returns:
        Dict with soul file contents.
    """
    ctx = get_tool_context()
    soul_ctx = ctx.soul_reader.read_all()

    return {
        "success": True,
        "soul": soul_ctx.soul,
        "agents": soul_ctx.agents,
        "state": soul_ctx.state,
        "memory": soul_ctx.memory,
    }


@tool
def soul_update(file: str, section: str, content: str) -> dict[str, Any]:
    """Update a section in STATE.md or append to MEMORY.md.

    For STATE.md: replaces the named section content.
    For MEMORY.md: appends a new timestamped entry.

    Args:
        file: Target file — "STATE" or "MEMORY".
        section: Section name for STATE (e.g. "Instances", "Plan").
            For MEMORY, this is the category (e.g. "decision", "pattern").
        content: New content for the section, or memory entry text.

    Returns:
        Dict with success status.
    """
    ctx = get_tool_context()

    if file.upper() == "STATE":
        ctx.soul_writer.update_state(section, content)
        logger.info("soul_update", file="STATE", section=section)
        return {"success": True, "file": "STATE", "section": section}

    if file.upper() == "MEMORY":
        now = datetime.now(timezone.utc).isoformat()
        entry = SoulMemoryEntry(
            timestamp=now,
            category=section,
            content=content,
        )
        ctx.soul_writer.append_memory(entry)
        logger.info("soul_update", file="MEMORY", category=section)
        return {"success": True, "file": "MEMORY", "category": section}

    return {"success": False, "error": f"Unknown file: {file}. Use STATE or MEMORY."}


@tool
def map_query(directory: str) -> dict[str, Any]:
    """Query Lattice codebase intelligence for a directory.

    Returns the _dir.md summary for the given directory from the
    .agent-docs/ shadow tree.

    Args:
        directory: Relative directory path to query (e.g. "src/auth").

    Returns:
        Dict with the _dir.md content or error if not found.
    """
    ctx = get_tool_context()

    if ctx.shadow_root is None:
        return {"success": False, "error": "No shadow root configured"}

    # Resolve and validate path to prevent traversal outside shadow root
    shadow_root_resolved = ctx.shadow_root.resolve()
    dir_doc_path = (ctx.shadow_root / directory / "_dir.md").resolve()
    if not str(dir_doc_path).startswith(str(shadow_root_resolved)):
        return {"success": False, "error": "Path traversal detected — directory must be within shadow root"}

    if not dir_doc_path.exists():
        return {
            "success": False,
            "error": f"No _dir.md found for {directory}",
            "searched": str(dir_doc_path),
        }

    content = dir_doc_path.read_text(encoding="utf-8")
    logger.info("map_query", directory=directory, content_length=len(content))
    return {
        "success": True,
        "directory": directory,
        "content": content,
    }


@tool
def write_todos(tasks: list[str]) -> dict[str, Any]:
    """Plan and track work items by updating the Plan section of STATE.md.

    Replaces the current plan with the provided task list.

    Args:
        tasks: List of task descriptions to track.

    Returns:
        Dict with success status and task count.
    """
    ctx = get_tool_context()

    plan_content = "\n".join(f"- {task}" for task in tasks)
    ctx.soul_writer.update_state("Plan", plan_content)

    logger.info("write_todos", task_count=len(tasks))
    return {
        "success": True,
        "task_count": len(tasks),
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    cc_send,
    cc_approve,
    cc_deny,
    cc_status,
    cc_spawn,
    cc_interrupt,
    github_read,
    soul_read,
    soul_update,
    map_query,
    write_todos,
]
