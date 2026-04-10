"""Tests for orchestrator agent graph construction via Deep Agent harness."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lattice.orchestrator.agent.graph import build_orchestrator_graph
from lattice.orchestrator.agent.tools import ALL_TOOLS, CUSTOM_TOOLS, ToolContext, set_tool_context
from lattice.orchestrator.soul_ecosystem.models import SoulContext


@pytest.fixture
def mock_soul_reader(tmp_path: Path) -> MagicMock:
    reader = MagicMock()
    reader.soul_dir = tmp_path / "soul"
    reader.soul_dir.mkdir(parents=True, exist_ok=True)
    # Create SOUL.md and AGENTS.md for memory loading
    (reader.soul_dir / "SOUL.md").write_text("# Identity\nTest orchestrator")
    (reader.soul_dir / "AGENTS.md").write_text("# Procedures\nTest rules")
    reader.read_all.return_value = SoulContext(
        soul="# Identity",
        agents="# Procedures",
        state="## Instances\n_No active instances_",
        memory="# Memory",
    )
    reader.build_system_prompt.return_value = "=== IDENTITY ===\ntest"
    reader._read_file.return_value = "## Instances\n_No active instances_"
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


class TestBuildOrchestratorGraph:
    def test_graph_builds_successfully(
        self, tool_ctx: ToolContext, mock_soul_reader: MagicMock
    ) -> None:
        """build_orchestrator_graph returns a compiled graph."""
        from langchain_core.language_models import FakeListChatModel

        fake_model = FakeListChatModel(responses=["test"])

        graph = build_orchestrator_graph(
            model=fake_model,
            tool_context=tool_ctx,
            soul_reader=mock_soul_reader,
        )

        assert graph is not None

    def test_custom_tools_excludes_write_todos(self) -> None:
        """CUSTOM_TOOLS does not include write_todos (built-in to deep agent)."""
        names = {t.name for t in CUSTOM_TOOLS}
        assert "write_todos" not in names

    def test_custom_tools_includes_map_refresh(self) -> None:
        """CUSTOM_TOOLS includes map_refresh."""
        names = {t.name for t in CUSTOM_TOOLS}
        assert "map_refresh" in names

    def test_all_tools_has_expected_count(self) -> None:
        """ALL_TOOLS has 11 entries."""
        assert len(ALL_TOOLS) == 11

    def test_expected_custom_tool_names(self) -> None:
        """All expected custom tools are present."""
        names = {t.name for t in CUSTOM_TOOLS}
        expected = {
            "cc_send", "cc_approve", "cc_deny", "cc_status",
            "cc_spawn", "cc_interrupt", "github_read",
            "soul_read", "soul_update", "map_query", "map_refresh",
        }
        assert names == expected
