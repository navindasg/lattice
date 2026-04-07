"""Tests for orchestrator agent tools.

Each tool is tested with mocked terminal backend and soul ecosystem.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lattice.orchestrator.agent.tools import (
    ALL_TOOLS,
    ToolContext,
    cc_approve,
    cc_deny,
    cc_interrupt,
    cc_send,
    cc_spawn,
    cc_status,
    get_tool_context,
    github_read,
    map_query,
    set_tool_context,
    soul_read,
    soul_update,
    write_todos,
)
from lattice.orchestrator.soul_ecosystem.models import (
    InstanceAssignment,
    OrchestratorState,
    SoulContext,
)


@pytest.fixture
def mock_terminal() -> MagicMock:
    """Create a mock TerminalBackend."""
    terminal = MagicMock()
    terminal.send_text = AsyncMock()
    terminal.send_enter = AsyncMock()
    terminal.send_interrupt = AsyncMock()
    terminal.capture_output = AsyncMock(return_value=["line1", "line2"])
    terminal.spawn_pane = AsyncMock(return_value="%5")
    terminal.detect_cc_panes = AsyncMock(return_value=[
        MagicMock(pane_id="%5", user_number=3),
    ])
    return terminal


@pytest.fixture
def mock_soul_reader() -> MagicMock:
    """Create a mock SoulReader."""
    reader = MagicMock()
    reader.read_all.return_value = SoulContext(
        soul="# Identity",
        agents="# Procedures",
        state="## Instances\n_No active instances_",
        memory="# Memory",
    )
    reader.read_state.return_value = OrchestratorState(
        instances=[
            InstanceAssignment(
                instance_id="3",
                task_description="fix auth bug",
                status="active",
                assigned_at="2026-04-07T12:00:00Z",
            ),
        ],
        plan=["Fix auth"],
        decisions=[],
        blockers=[],
    )
    reader.build_system_prompt.return_value = "system prompt content"
    return reader


@pytest.fixture
def mock_soul_writer() -> MagicMock:
    """Create a mock SoulWriter."""
    writer = MagicMock()
    writer.update_state = MagicMock()
    writer.update_full_state = MagicMock()
    writer.append_memory = MagicMock()
    return writer


@pytest.fixture
def tool_ctx(
    mock_terminal: MagicMock,
    mock_soul_reader: MagicMock,
    mock_soul_writer: MagicMock,
    tmp_path: Path,
) -> ToolContext:
    """Create a ToolContext with mocked dependencies."""
    ctx = ToolContext(
        terminal=mock_terminal,
        soul_reader=mock_soul_reader,
        soul_writer=mock_soul_writer,
        instance_pane_map={"3": "%5", "1": "%0"},
        event_history={"3": [{"event_type": "PostToolUse", "tool_name": "Bash"}]},
        shadow_root=tmp_path / ".agent-docs",
        event_loop=None,  # triggers asyncio.run fallback in _run_async
    )
    set_tool_context(ctx)
    return ctx


class TestToolRegistry:
    def test_all_tools_has_eleven_entries(self) -> None:
        """ALL_TOOLS contains exactly 11 tools."""
        assert len(ALL_TOOLS) == 11

    def test_all_tools_have_names(self) -> None:
        """All tools have non-empty names."""
        for tool_fn in ALL_TOOLS:
            assert tool_fn.name, f"Tool missing name: {tool_fn}"

    def test_expected_tool_names(self) -> None:
        """All 11 expected tools are present."""
        names = {t.name for t in ALL_TOOLS}
        expected = {
            "cc_send", "cc_approve", "cc_deny", "cc_status",
            "cc_spawn", "cc_interrupt", "github_read",
            "soul_read", "soul_update", "map_query", "write_todos",
        }
        assert names == expected


class TestToolContext:
    def test_set_and_get_context(self, tool_ctx: ToolContext) -> None:
        """set_tool_context / get_tool_context round-trip."""
        retrieved = get_tool_context()
        assert retrieved is tool_ctx

    def test_get_context_without_set_raises(self) -> None:
        """get_tool_context raises RuntimeError if not set."""
        set_tool_context(None)  # type: ignore[arg-type]
        # Restore after test
        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                get_tool_context()
        finally:
            pass


class TestCCSend:
    def test_sends_text_and_enter(self, tool_ctx: ToolContext, mock_terminal: MagicMock) -> None:
        result = cc_send.invoke({"instance": "3", "message": "fix the auth bug"})
        assert result["success"] is True
        assert result["instance"] == "3"
        mock_terminal.send_text.assert_called_once_with("%5", "fix the auth bug")
        mock_terminal.send_enter.assert_called_once_with("%5")

    def test_unknown_instance_returns_error(self, tool_ctx: ToolContext) -> None:
        result = cc_send.invoke({"instance": "9", "message": "hello"})
        assert result["success"] is False
        assert "not found" in result["error"]


class TestCCApprove:
    def test_sends_y_and_enter(self, tool_ctx: ToolContext, mock_terminal: MagicMock) -> None:
        result = cc_approve.invoke({"instance": "3"})
        assert result["success"] is True
        assert result["decision"] == "approved"
        mock_terminal.send_text.assert_called_once_with("%5", "y")
        mock_terminal.send_enter.assert_called_once_with("%5")

    def test_unknown_instance_returns_error(self, tool_ctx: ToolContext) -> None:
        result = cc_approve.invoke({"instance": "9"})
        assert result["success"] is False


class TestCCDeny:
    def test_sends_n_and_enter(self, tool_ctx: ToolContext, mock_terminal: MagicMock) -> None:
        result = cc_deny.invoke({"instance": "3"})
        assert result["success"] is True
        assert result["decision"] == "denied"
        mock_terminal.send_text.assert_called_with("%5", "n")

    def test_sends_redirect_after_deny(self, tool_ctx: ToolContext, mock_terminal: MagicMock) -> None:
        result = cc_deny.invoke({"instance": "3", "reason": "use AWS instead"})
        assert result["success"] is True
        assert result["reason"] == "use AWS instead"
        # Should have sent "n", Enter, then "use AWS instead", Enter
        assert mock_terminal.send_text.call_count == 2
        assert mock_terminal.send_enter.call_count == 2


class TestCCStatus:
    def test_returns_events_and_assignment(
        self, tool_ctx: ToolContext, mock_soul_reader: MagicMock
    ) -> None:
        result = cc_status.invoke({"instance": "3"})
        assert result["success"] is True
        assert result["instance"] == "3"
        assert len(result["recent_events"]) == 1
        assert result["assignment"]["task"] == "fix auth bug"
        assert result["assignment"]["status"] == "active"

    def test_unknown_instance_returns_no_assignment(self, tool_ctx: ToolContext) -> None:
        result = cc_status.invoke({"instance": "7"})
        assert result["success"] is True
        assert result["assignment"] is None
        assert result["recent_events"] == []


class TestCCInterrupt:
    def test_sends_ctrl_c(self, tool_ctx: ToolContext, mock_terminal: MagicMock) -> None:
        result = cc_interrupt.invoke({"instance": "3"})
        assert result["success"] is True
        assert result["action"] == "interrupted"
        mock_terminal.send_interrupt.assert_called_once_with("%5")


class TestGithubRead:
    def test_parses_valid_reference(self, tool_ctx: ToolContext) -> None:
        result = github_read.invoke({"issue_ref": "navindasg/lattice#7"})
        assert result["success"] is True
        assert result["owner"] == "navindasg"
        assert result["repo"] == "lattice"
        assert result["issue_number"] == 7

    def test_invalid_reference_no_hash(self, tool_ctx: ToolContext) -> None:
        result = github_read.invoke({"issue_ref": "navindasg/lattice"})
        assert result["success"] is False
        assert "Invalid issue reference" in result["error"]

    def test_invalid_reference_no_slash(self, tool_ctx: ToolContext) -> None:
        result = github_read.invoke({"issue_ref": "lattice#7"})
        assert result["success"] is False
        assert "Invalid repo" in result["error"]

    def test_invalid_issue_number(self, tool_ctx: ToolContext) -> None:
        result = github_read.invoke({"issue_ref": "navindasg/lattice#abc"})
        assert result["success"] is False
        assert "Invalid issue number" in result["error"]


class TestSoulRead:
    def test_returns_all_files(self, tool_ctx: ToolContext) -> None:
        result = soul_read.invoke({})
        assert result["success"] is True
        assert "soul" in result
        assert "agents" in result
        assert "state" in result
        assert "memory" in result


class TestSoulUpdate:
    def test_update_state_section(
        self, tool_ctx: ToolContext, mock_soul_writer: MagicMock
    ) -> None:
        result = soul_update.invoke({
            "file": "STATE",
            "section": "Instances",
            "content": "1: working on #7\n2: idle",
        })
        assert result["success"] is True
        assert result["file"] == "STATE"
        mock_soul_writer.update_state.assert_called_once_with(
            "Instances", "1: working on #7\n2: idle"
        )

    def test_append_memory(
        self, tool_ctx: ToolContext, mock_soul_writer: MagicMock
    ) -> None:
        result = soul_update.invoke({
            "file": "MEMORY",
            "section": "decision",
            "content": "Approved file write to auth.py",
        })
        assert result["success"] is True
        assert result["file"] == "MEMORY"
        mock_soul_writer.append_memory.assert_called_once()

    def test_unknown_file_returns_error(self, tool_ctx: ToolContext) -> None:
        result = soul_update.invoke({
            "file": "UNKNOWN",
            "section": "test",
            "content": "data",
        })
        assert result["success"] is False
        assert "Unknown file" in result["error"]


class TestMapQuery:
    def test_returns_dir_doc_content(self, tool_ctx: ToolContext, tmp_path: Path) -> None:
        # Create a _dir.md file
        dir_doc_dir = tmp_path / ".agent-docs" / "src" / "auth"
        dir_doc_dir.mkdir(parents=True)
        (dir_doc_dir / "_dir.md").write_text("# Auth module\nHandles authentication.")

        result = map_query.invoke({"directory": "src/auth"})
        assert result["success"] is True
        assert "Auth module" in result["content"]

    def test_missing_dir_doc_returns_error(self, tool_ctx: ToolContext) -> None:
        result = map_query.invoke({"directory": "nonexistent/path"})
        assert result["success"] is False
        assert "No _dir.md found" in result["error"]

    def test_no_shadow_root_returns_error(self) -> None:
        ctx = ToolContext(
            terminal=MagicMock(),
            soul_reader=MagicMock(),
            soul_writer=MagicMock(),
            shadow_root=None,
        )
        set_tool_context(ctx)
        result = map_query.invoke({"directory": "src"})
        assert result["success"] is False
        assert "No shadow root" in result["error"]


class TestWriteTodos:
    def test_updates_plan_section(
        self, tool_ctx: ToolContext, mock_soul_writer: MagicMock
    ) -> None:
        tasks = ["Fix auth bug", "Write tests", "Deploy to staging"]
        result = write_todos.invoke({"tasks": tasks})
        assert result["success"] is True
        assert result["task_count"] == 3
        mock_soul_writer.update_state.assert_called_once()
        call_args = mock_soul_writer.update_state.call_args
        assert call_args[0][0] == "Plan"
        assert "Fix auth bug" in call_args[0][1]
        assert "Write tests" in call_args[0][1]
