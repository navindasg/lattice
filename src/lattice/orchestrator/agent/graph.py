"""LangGraph agent graph for the orchestrator — built via Deep Agent harness.

Uses `create_deep_agent` from the `deepagents` library to produce a compiled
LangGraph graph with built-in planning (`write_todos`), filesystem tools,
sub-agent delegation (`task`), and automatic context summarization.

Custom orchestrator tools (cc_spawn, cc_send, cc_approve, etc.) are passed
as additional tools.  Soul files (SOUL.md, AGENTS.md) are loaded via the
``memory`` parameter so they are always present in the system prompt.

The returned graph is a standard LangGraph ``CompiledGraph`` — it supports
checkpointers, streaming, and all LangGraph features.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from langchain_core.language_models import BaseChatModel

from lattice.orchestrator.agent.prompt import build_system_prompt
from lattice.orchestrator.agent.tools import (
    CUSTOM_TOOLS,
    ToolContext,
    set_tool_context,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader

logger = structlog.get_logger(__name__)


def build_orchestrator_graph(
    model: BaseChatModel,
    tool_context: ToolContext,
    soul_reader: SoulReader | None = None,
    checkpointer: Any | None = None,
) -> Any:
    """Build the orchestrator agent via the Deep Agent harness.

    Creates a ``create_deep_agent`` graph with:
    - Custom orchestrator tools (cc_spawn, cc_send, etc.)
    - Soul files loaded as persistent memory
    - Filesystem backend rooted at the soul directory
    - DuckDB checkpointer for crash recovery

    Args:
        model: The chat model for reasoning (e.g. ChatAnthropic).
        tool_context: ToolContext with dependencies for all tools.
        soul_reader: SoulReader for system prompt assembly.
            If None, uses the one from tool_context.
        checkpointer: LangGraph-compatible checkpoint saver.
            If None, the graph runs without persistence.

    Returns:
        Compiled LangGraph graph (from create_deep_agent).
    """
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend

    # Set module-level tool context for tools to access
    set_tool_context(tool_context)

    # Build the core directive (does NOT include soul file content —
    # soul files are loaded via the memory= parameter below)
    reader = soul_reader or tool_context.soul_reader
    # Extract project root from the soul directory path (two levels up from .lattice/soul/)
    project_root = str(reader.soul_dir.parent.parent) if reader.soul_dir else ""
    system_prompt = build_system_prompt(reader, project_root=project_root)

    # Resolve soul directory for memory files and filesystem backend
    soul_dir = reader.soul_dir

    # Memory files: always loaded into the system prompt by the harness.
    # SOUL.md = identity/mission, AGENTS.md = approval procedures.
    # STATE.md and MEMORY.md are NOT included — they're accessed via
    # soul_read/soul_update tools to keep the prompt lean.
    memory_files: list[str] = []
    for fname in ("SOUL.md", "AGENTS.md"):
        fpath = soul_dir / fname
        if fpath.exists():
            memory_files.append(str(fpath))

    # Filesystem backend: lets the harness's built-in file tools
    # (read_file, write_file, ls, grep) operate within the soul directory.
    # The orchestrator can read/write STATE.md directly via these tools
    # as a fallback, but should prefer soul_read/soul_update for atomicity.
    backend = FilesystemBackend(root_dir=str(soul_dir.parent), virtual_mode=True)

    # Build the deep agent graph
    agent = create_deep_agent(
        model=model,
        tools=list(CUSTOM_TOOLS),
        system_prompt=system_prompt,
        memory=memory_files if memory_files else None,
        backend=backend,
        checkpointer=checkpointer,
        name="lattice-orchestrator",
    )

    logger.info(
        "orchestrator_agent.deep_agent_built",
        custom_tool_count=len(CUSTOM_TOOLS),
        memory_files=len(memory_files),
        soul_dir=str(soul_dir),
    )

    return agent
