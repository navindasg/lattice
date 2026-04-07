"""Tests for orchestrator agent state schema."""
import pytest
from pydantic import ValidationError

from lattice.orchestrator.agent.state import (
    AgentState,
    InstanceInfo,
    PendingApproval,
)


class TestPendingApproval:
    def test_construction(self) -> None:
        approval = PendingApproval(
            event_id="evt-001",
            instance="3",
            tool_name="Bash",
            tool_input={"command": "ls"},
            file_path="/tmp/test.py",
            timestamp="2026-04-07T12:00:00Z",
        )
        assert approval.event_id == "evt-001"
        assert approval.instance == "3"
        assert approval.tool_name == "Bash"

    def test_frozen(self) -> None:
        approval = PendingApproval(
            event_id="evt-001",
            instance="3",
            tool_name="Bash",
        )
        with pytest.raises(Exception):
            approval.instance = "4"  # type: ignore[misc]

    def test_defaults(self) -> None:
        approval = PendingApproval(
            event_id="evt-001",
            instance="1",
            tool_name="Read",
        )
        assert approval.tool_input == {}
        assert approval.file_path == ""
        assert approval.timestamp == ""


class TestInstanceInfo:
    def test_construction(self) -> None:
        info = InstanceInfo(
            instance_id="3",
            pane_id="%5",
            task="fix auth bug",
            status="active",
            assigned_at="2026-04-07T12:00:00Z",
        )
        assert info.instance_id == "3"
        assert info.pane_id == "%5"
        assert info.status == "active"

    def test_frozen(self) -> None:
        info = InstanceInfo(instance_id="1")
        with pytest.raises(Exception):
            info.status = "blocked"  # type: ignore[misc]

    def test_defaults(self) -> None:
        info = InstanceInfo(instance_id="1")
        assert info.pane_id == ""
        assert info.task == ""
        assert info.status == "idle"
        assert info.assigned_at == ""


class TestAgentState:
    def test_agent_state_is_typed_dict(self) -> None:
        """AgentState can be used as a dict."""
        state: AgentState = {
            "messages": [],
            "instances": {},
            "pending_approvals": {},
            "plan": [],
            "last_event": None,
        }
        assert state["messages"] == []
        assert state["instances"] == {}
        assert state["plan"] == []
        assert state["last_event"] is None

    def test_agent_state_with_data(self) -> None:
        """AgentState can hold instance and approval data."""
        state: AgentState = {
            "messages": [],
            "instances": {
                "3": {"pane_id": "%5", "task": "fix auth", "status": "active"},
            },
            "pending_approvals": {
                "evt-001": {"instance": "3", "tool_name": "Bash"},
            },
            "plan": ["Step 1: Fix auth", "Step 2: Test"],
            "last_event": {"event_type": "PreToolUse"},
        }
        assert "3" in state["instances"]
        assert len(state["plan"]) == 2
