"""Unit tests for soul ecosystem compaction lifecycle.

Tests cover:
- pre_compaction_flush writes context to STATE.md
- post_compaction_restore returns SoulContext matching what was flushed
- Round-trip fidelity: flush -> restore -> compare
"""
from __future__ import annotations

from pathlib import Path

from lattice.orchestrator.soul_ecosystem.compaction import (
    post_compaction_restore,
    pre_compaction_flush,
)
from lattice.orchestrator.soul_ecosystem.models import (
    InstanceAssignment,
    OrchestratorState,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.soul_ecosystem.writer import SoulWriter


class TestPreCompactionFlush:
    """pre_compaction_flush writes context to STATE.md."""

    def test_flushes_plan_and_blockers(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        context = {
            "plan": ["Step 1", "Step 2"],
            "blockers": ["Need API key"],
        }
        pre_compaction_flush(writer, context)

        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "Step 1" in content
        assert "Step 2" in content
        assert "Need API key" in content

    def test_flushes_instances(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        context = {
            "instances": [
                {
                    "instance_id": "inst-001",
                    "task_description": "Build API",
                    "status": "active",
                    "assigned_at": "2026-04-07T12:00:00Z",
                }
            ],
        }
        pre_compaction_flush(writer, context)

        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "inst-001" in content
        assert "Build API" in content

    def test_flushes_with_model_instances(self, tmp_path: Path):
        """Accepts InstanceAssignment objects directly."""
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        context = {
            "instances": [
                InstanceAssignment(
                    instance_id="inst-002",
                    task_description="Write tests",
                    status="idle",
                    assigned_at="2026-04-07T14:00:00Z",
                )
            ],
        }
        pre_compaction_flush(writer, context)

        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "inst-002" in content

    def test_empty_context(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        pre_compaction_flush(writer, {})

        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "_No active instances_" in content
        assert "_No current plan_" in content


class TestPostCompactionRestore:
    """post_compaction_restore returns SoulContext."""

    def test_returns_soul_context(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        reader = SoulReader(soul_dir)
        ctx = post_compaction_restore(reader)

        assert ctx.soul is not None
        assert ctx.agents is not None
        assert ctx.state is not None
        assert ctx.memory is not None


class TestFlushRestoreRoundTrip:
    """Round-trip fidelity: flush -> restore -> compare."""

    def test_round_trip(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        context = {
            "instances": [
                {
                    "instance_id": "inst-001",
                    "task_description": "Build feature",
                    "status": "active",
                    "assigned_at": "2026-04-07T12:00:00Z",
                }
            ],
            "plan": ["Step 1", "Step 2"],
            "decisions": [
                {
                    "timestamp": "2026-04-07T12:30:00Z",
                    "event_type": "approve",
                    "target": "deploy",
                    "reason": "Tests pass",
                }
            ],
            "blockers": ["Waiting for review"],
        }
        pre_compaction_flush(writer, context)

        reader = SoulReader(soul_dir)
        ctx = post_compaction_restore(reader)

        # Verify the state content matches what was flushed
        state = OrchestratorState.from_markdown(ctx.state)
        assert len(state.instances) == 1
        assert state.instances[0].instance_id == "inst-001"
        assert state.plan == ["Step 1", "Step 2"]
        assert len(state.decisions) == 1
        assert state.decisions[0].event_type == "approve"
        assert state.blockers == ["Waiting for review"]
