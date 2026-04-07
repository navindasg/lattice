"""Orchestrator agent subpackage: LangGraph supervisor with CC management tools.

The orchestrator agent is an LLM-powered project manager that coordinates
multiple Claude Code worker instances. It reasons about what to do, assigns
work, handles tool-use approvals, and manages project state.

Components:
    AgentState           — LangGraph state schema
    ToolContext           — Dependency container for tools
    ALL_TOOLS             — List of all 11 LangGraph tool functions
    build_orchestrator_graph — Build the LangGraph StateGraph
    DuckDBCheckpointer   — DuckDB-backed checkpoint saver
    AgentEventLoop       — Event consumption and agent invocation loop
    build_system_prompt   — System prompt assembly from soul files
    PendingApproval       — Model for pending approval requests
    InstanceInfo          — Model for tracked CC instance state
"""
from lattice.orchestrator.agent.checkpointer import DuckDBCheckpointer
from lattice.orchestrator.agent.event_loop import AgentEventLoop
from lattice.orchestrator.agent.graph import build_orchestrator_graph
from lattice.orchestrator.agent.prompt import build_system_prompt
from lattice.orchestrator.agent.state import (
    AgentState,
    InstanceInfo,
    PendingApproval,
)
from lattice.orchestrator.agent.tools import (
    ALL_TOOLS,
    ToolContext,
    get_tool_context,
    set_tool_context,
)

__all__ = [
    "ALL_TOOLS",
    "AgentEventLoop",
    "AgentState",
    "DuckDBCheckpointer",
    "InstanceInfo",
    "PendingApproval",
    "ToolContext",
    "build_orchestrator_graph",
    "build_system_prompt",
    "get_tool_context",
    "set_tool_context",
]
