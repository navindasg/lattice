"""Tests for orchestrator agent graph construction and routing."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lattice.orchestrator.agent.graph import (
    _should_continue,
    build_orchestrator_graph,
)
from lattice.orchestrator.agent.tools import ALL_TOOLS, ToolContext, set_tool_context
from lattice.orchestrator.soul_ecosystem.models import SoulContext


@pytest.fixture
def mock_soul_reader() -> MagicMock:
    reader = MagicMock()
    reader.read_all.return_value = SoulContext(
        soul="# Identity",
        agents="# Procedures",
        state="## Instances\n_No active instances_",
        memory="# Memory",
    )
    reader.build_system_prompt.return_value = "=== IDENTITY ===\ntest"
    return reader


@pytest.fixture
def tool_ctx(mock_soul_reader: MagicMock) -> ToolContext:
    ctx = ToolContext(
        terminal=MagicMock(),
        soul_reader=mock_soul_reader,
        soul_writer=MagicMock(),
        instance_pane_map={},
        event_history={},
    )
    set_tool_context(ctx)
    return ctx


class TestShouldContinue:
    def test_empty_messages_returns_end(self) -> None:
        state = {"messages": [], "instances": {}, "pending_approvals": {}, "plan": [], "last_event": None}
        assert _should_continue(state) == "__end__"

    def test_ai_message_with_tool_calls_returns_tools(self) -> None:
        ai_msg = AIMessage(content="", tool_calls=[{"name": "cc_send", "args": {}, "id": "1"}])
        state = {"messages": [ai_msg], "instances": {}, "pending_approvals": {}, "plan": [], "last_event": None}
        assert _should_continue(state) == "tools"

    def test_ai_message_without_tool_calls_returns_end(self) -> None:
        ai_msg = AIMessage(content="All done.")
        state = {"messages": [ai_msg], "instances": {}, "pending_approvals": {}, "plan": [], "last_event": None}
        assert _should_continue(state) == "__end__"

    def test_human_message_returns_end(self) -> None:
        human_msg = HumanMessage(content="hello")
        state = {"messages": [human_msg], "instances": {}, "pending_approvals": {}, "plan": [], "last_event": None}
        assert _should_continue(state) == "__end__"


class TestBuildOrchestratorGraph:
    def test_graph_builds_successfully(
        self, tool_ctx: ToolContext, mock_soul_reader: MagicMock
    ) -> None:
        """build_orchestrator_graph returns a valid StateGraph."""
        mock_model = MagicMock()
        mock_model.bind_tools.return_value = mock_model

        graph = build_orchestrator_graph(
            model=mock_model,
            tool_context=tool_ctx,
            soul_reader=mock_soul_reader,
        )

        assert graph is not None

    def test_graph_has_supervisor_and_tools_nodes(
        self, tool_ctx: ToolContext, mock_soul_reader: MagicMock
    ) -> None:
        """Graph contains supervisor and tools nodes."""
        mock_model = MagicMock()
        mock_model.bind_tools.return_value = mock_model

        graph = build_orchestrator_graph(
            model=mock_model,
            tool_context=tool_ctx,
            soul_reader=mock_soul_reader,
        )

        # Check that nodes exist by inspecting the graph's nodes dict
        assert "supervisor" in graph.nodes
        assert "tools" in graph.nodes

    def test_model_bound_with_all_tools(
        self, tool_ctx: ToolContext, mock_soul_reader: MagicMock
    ) -> None:
        """The model is bound with all 11 tools."""
        mock_model = MagicMock()
        mock_model.bind_tools.return_value = mock_model

        build_orchestrator_graph(
            model=mock_model,
            tool_context=tool_ctx,
            soul_reader=mock_soul_reader,
        )

        mock_model.bind_tools.assert_called_once_with(ALL_TOOLS)
