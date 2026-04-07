"""Unit tests for soul ecosystem models.

Tests cover:
- OrchestratorState creation with defaults and populated fields
- to_markdown produces valid markdown with all 4 sections
- from_markdown round-trips correctly
- Frozen (immutable) enforcement on all models
- SoulMemoryEntry fields
- SoulContext frozen
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from lattice.orchestrator.soul_ecosystem.models import (
    DecisionRecord,
    InstanceAssignment,
    OrchestratorState,
    SoulContext,
    SoulMemoryEntry,
)


def _make_instance(**overrides) -> InstanceAssignment:
    """Create a default InstanceAssignment with optional overrides."""
    defaults = {
        "instance_id": "inst-001",
        "task_description": "Implement feature X",
        "status": "active",
        "assigned_at": "2026-04-07T12:00:00Z",
    }
    return InstanceAssignment(**{**defaults, **overrides})


def _make_decision(**overrides) -> DecisionRecord:
    """Create a default DecisionRecord with optional overrides."""
    defaults = {
        "timestamp": "2026-04-07T12:00:00Z",
        "event_type": "approve",
        "target": "file deletion",
    }
    return DecisionRecord(**{**defaults, **overrides})


def _make_memory_entry(**overrides) -> SoulMemoryEntry:
    """Create a default SoulMemoryEntry with optional overrides."""
    defaults = {
        "timestamp": "2026-04-07T12:00:00Z",
        "category": "preference",
        "content": "User prefers concise output",
    }
    return SoulMemoryEntry(**{**defaults, **overrides})


class TestOrchestratorStateDefaults:
    """OrchestratorState with default empty lists."""

    def test_empty_defaults(self):
        state = OrchestratorState()
        assert state.instances == []
        assert state.plan == []
        assert state.decisions == []
        assert state.blockers == []

    def test_populated_fields(self):
        inst = _make_instance()
        dec = _make_decision()
        state = OrchestratorState(
            instances=[inst],
            plan=["Step 1", "Step 2"],
            decisions=[dec],
            blockers=["Waiting for API key"],
        )
        assert len(state.instances) == 1
        assert state.instances[0].instance_id == "inst-001"
        assert state.plan == ["Step 1", "Step 2"]
        assert len(state.decisions) == 1
        assert state.blockers == ["Waiting for API key"]


class TestOrchestratorStateMarkdown:
    """to_markdown produces valid markdown with all 4 sections."""

    def test_to_markdown_contains_all_four_sections(self):
        state = OrchestratorState()
        md = state.to_markdown()
        assert "## Instances" in md
        assert "## Plan" in md
        assert "## Decisions" in md
        assert "## Blockers" in md

    def test_to_markdown_empty_state_has_placeholders(self):
        state = OrchestratorState()
        md = state.to_markdown()
        assert "_No active instances_" in md
        assert "_No current plan_" in md
        assert "_No recent decisions_" in md
        assert "_No blockers_" in md

    def test_to_markdown_populated_state(self):
        inst = _make_instance()
        dec = _make_decision(reason="Requested by user")
        state = OrchestratorState(
            instances=[inst],
            plan=["Fix bug"],
            decisions=[dec],
            blockers=["Blocked on CI"],
        )
        md = state.to_markdown()
        assert "**inst-001**" in md
        assert "Implement feature X" in md
        assert "Fix bug" in md
        assert "approve: file deletion" in md
        assert "Requested by user" in md
        assert "Blocked on CI" in md

    def test_to_markdown_decision_without_reason(self):
        dec = _make_decision()
        state = OrchestratorState(decisions=[dec])
        md = state.to_markdown()
        assert "approve: file deletion" in md
        assert "—" not in md.split("## Decisions")[1].split("## Blockers")[0]


class TestOrchestratorStateRoundTrip:
    """from_markdown round-trips correctly."""

    def test_round_trip_empty_state(self):
        original = OrchestratorState()
        md = original.to_markdown()
        restored = OrchestratorState.from_markdown(md)
        assert restored.instances == []
        assert restored.plan == []
        assert restored.decisions == []
        assert restored.blockers == []

    def test_round_trip_populated_state(self):
        inst = _make_instance()
        dec = _make_decision(reason="Approved by admin")
        original = OrchestratorState(
            instances=[inst],
            plan=["Step 1", "Step 2"],
            decisions=[dec],
            blockers=["Waiting for review"],
        )
        md = original.to_markdown()
        restored = OrchestratorState.from_markdown(md)

        assert len(restored.instances) == 1
        assert restored.instances[0].instance_id == "inst-001"
        assert restored.instances[0].task_description == "Implement feature X"
        assert restored.instances[0].status == "active"
        assert restored.plan == ["Step 1", "Step 2"]
        assert len(restored.decisions) == 1
        assert restored.decisions[0].event_type == "approve"
        assert restored.decisions[0].target == "file deletion"
        assert restored.decisions[0].reason == "Approved by admin"
        assert restored.blockers == ["Waiting for review"]

    def test_round_trip_multiple_instances(self):
        original = OrchestratorState(
            instances=[
                _make_instance(instance_id="inst-001"),
                _make_instance(instance_id="inst-002", status="idle"),
            ],
        )
        md = original.to_markdown()
        restored = OrchestratorState.from_markdown(md)
        assert len(restored.instances) == 2
        assert restored.instances[0].instance_id == "inst-001"
        assert restored.instances[1].instance_id == "inst-002"
        assert restored.instances[1].status == "idle"


class TestFrozenModels:
    """All models are frozen (immutable)."""

    def test_instance_assignment_frozen(self):
        inst = _make_instance()
        with pytest.raises(ValidationError):
            inst.instance_id = "changed"

    def test_decision_record_frozen(self):
        dec = _make_decision()
        with pytest.raises(ValidationError):
            dec.event_type = "changed"

    def test_soul_memory_entry_frozen(self):
        entry = _make_memory_entry()
        with pytest.raises(ValidationError):
            entry.content = "changed"

    def test_orchestrator_state_frozen(self):
        state = OrchestratorState()
        with pytest.raises(ValidationError):
            state.plan = ["changed"]

    def test_soul_context_frozen(self):
        ctx = SoulContext(
            soul="soul", agents="agents", state="state", memory="memory"
        )
        with pytest.raises(ValidationError):
            ctx.soul = "changed"


class TestSoulMemoryEntry:
    """SoulMemoryEntry fields and construction."""

    def test_fields(self):
        entry = _make_memory_entry()
        assert entry.timestamp == "2026-04-07T12:00:00Z"
        assert entry.category == "preference"
        assert entry.content == "User prefers concise output"

    def test_different_categories(self):
        for cat in ("preference", "convention", "pattern", "decision"):
            entry = _make_memory_entry(category=cat)
            assert entry.category == cat


class TestSoulContext:
    """SoulContext construction and fields."""

    def test_all_fields(self):
        ctx = SoulContext(
            soul="soul content",
            agents="agents content",
            state="state content",
            memory="memory content",
        )
        assert ctx.soul == "soul content"
        assert ctx.agents == "agents content"
        assert ctx.state == "state content"
        assert ctx.memory == "memory content"
