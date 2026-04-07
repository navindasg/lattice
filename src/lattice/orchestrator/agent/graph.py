"""LangGraph StateGraph definition for the orchestrator agent.

Builds a supervisor graph with:
    - supervisor node: LLM reasoning with tool binding
    - tool node: executes tool calls from the supervisor
    - conditional routing: loops back to supervisor after tools, ends on no tool calls

The graph supports checkpointing via DuckDBCheckpointer for crash recovery.
"""
from __future__ import annotations

from typing import Any, Literal

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from lattice.orchestrator.agent.prompt import build_system_prompt
from lattice.orchestrator.agent.state import AgentState
from lattice.orchestrator.agent.tools import ALL_TOOLS, ToolContext, set_tool_context
from lattice.orchestrator.soul_ecosystem.reader import SoulReader

logger = structlog.get_logger(__name__)


def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    """Determine whether to route to tools or end.

    If the last message is an AIMessage with tool_calls, route to tools.
    Otherwise, end the graph execution.

    Args:
        state: Current agent state.

    Returns:
        "tools" if there are pending tool calls, "__end__" otherwise.
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"

    return "__end__"


def _create_supervisor_node(
    model: BaseChatModel,
    system_prompt: str,
):
    """Create the supervisor node function.

    The supervisor node prepends the system prompt to messages,
    invokes the LLM with tool binding, and returns the response.

    Args:
        model: The chat model to use for reasoning.
        system_prompt: The assembled system prompt.

    Returns:
        A function compatible with LangGraph node API.
    """
    tools = ALL_TOOLS
    model_with_tools = model.bind_tools(tools)

    def supervisor(state: AgentState) -> dict[str, Any]:
        """Supervisor node: reason about state and decide next action."""
        messages = list(state.get("messages", []))

        # Prepend system prompt if not already present
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + messages

        response = model_with_tools.invoke(messages)

        logger.info(
            "orchestrator_agent.supervisor",
            tool_calls=len(response.tool_calls) if hasattr(response, "tool_calls") else 0,
        )

        return {"messages": [response]}

    return supervisor


def build_orchestrator_graph(
    model: BaseChatModel,
    tool_context: ToolContext,
    soul_reader: SoulReader | None = None,
) -> StateGraph:
    """Build the orchestrator LangGraph StateGraph.

    Creates a supervisor agent with tool binding, conditional routing,
    and checkpointing support.

    Args:
        model: The chat model for the supervisor node.
        tool_context: ToolContext with dependencies for all tools.
        soul_reader: SoulReader for system prompt assembly.
            If None, uses the one from tool_context.

    Returns:
        Uncompiled StateGraph. Call graph.compile(checkpointer=...) before
        invoking to enable checkpointing, or graph.compile() for in-memory use.
    """
    # Set module-level tool context for tools to access
    set_tool_context(tool_context)

    # Build system prompt
    reader = soul_reader or tool_context.soul_reader
    system_prompt = build_system_prompt(reader)

    # Create nodes
    supervisor = _create_supervisor_node(model, system_prompt)
    tool_node = ToolNode(ALL_TOOLS)

    # Build graph
    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("tools", tool_node)

    # Set entry point
    graph.set_entry_point("supervisor")

    # Conditional routing: supervisor -> tools (if tool_calls) or END
    graph.add_conditional_edges(
        "supervisor",
        _should_continue,
        {
            "tools": "tools",
            "__end__": END,
        },
    )

    # After tools, always return to supervisor for next reasoning step
    graph.add_edge("tools", "supervisor")

    logger.info(
        "orchestrator_agent.graph_built",
        tool_count=len(ALL_TOOLS),
    )

    return graph
