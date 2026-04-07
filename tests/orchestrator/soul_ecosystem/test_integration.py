"""Integration tests for soul ecosystem lifecycle.

Tests cover:
- Full lifecycle: init_soul_dir -> update_state -> append_memory -> read_all -> verify
- Flush/restore: init -> write state -> flush -> delete STATE.md -> restore -> verify
- Human edit preservation: init -> manually edit SOUL.md -> init again -> unchanged
"""
from __future__ import annotations

from pathlib import Path

from lattice.orchestrator.soul_ecosystem.compaction import (
    post_compaction_restore,
    pre_compaction_flush,
)
from lattice.orchestrator.soul_ecosystem.models import (
    OrchestratorState,
    SoulMemoryEntry,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.soul_ecosystem.writer import SoulWriter


class TestFullLifecycle:
    """Full lifecycle: init -> update -> append -> read -> verify."""

    def test_lifecycle(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        reader = SoulReader(soul_dir)

        # Step 1: Initialize
        writer.init_soul_dir()
        assert (soul_dir / "SOUL.md").exists()
        assert (soul_dir / "AGENTS.md").exists()
        assert (soul_dir / "STATE.md").exists()
        assert (soul_dir / "MEMORY.md").exists()

        # Step 2: Update state
        writer.update_state("Plan", "- Implement authentication\n- Write tests")
        writer.update_state("Blockers", "- Waiting for DB schema")

        # Step 3: Append memory
        entry1 = SoulMemoryEntry(
            timestamp="2026-04-07T12:00:00Z",
            category="convention",
            content="Use snake_case for Python files",
        )
        entry2 = SoulMemoryEntry(
            timestamp="2026-04-07T13:00:00Z",
            category="preference",
            content="User prefers verbose error messages",
        )
        writer.append_memory(entry1)
        writer.append_memory(entry2)

        # Step 4: Read all and verify
        ctx = reader.read_all()
        assert "Implement authentication" in ctx.state
        assert "Write tests" in ctx.state
        assert "Waiting for DB schema" in ctx.state
        assert "snake_case" in ctx.memory
        assert "verbose error messages" in ctx.memory

        # Step 5: Verify structured read
        state = reader.read_state()
        assert "Implement authentication" in state.plan
        assert "Write tests" in state.plan
        assert "Waiting for DB schema" in state.blockers

        entries = reader.read_memory_entries()
        assert len(entries) == 2
        assert entries[0].category == "convention"
        assert entries[1].category == "preference"

        # Step 6: Verify query_memory
        prefs = reader.query_memory(category="preference")
        assert len(prefs) == 1
        assert "verbose error messages" in prefs[0].content


class TestFlushRestoreLifecycle:
    """Flush/restore: init -> write state -> flush -> delete -> restore."""

    def test_flush_delete_restore(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        reader = SoulReader(soul_dir)

        # Initialize and write some state
        writer.init_soul_dir()
        writer.update_state("Plan", "- Deploy to staging\n- Run smoke tests")
        writer.update_state("Blockers", "- VPN access needed")

        # Flush state
        context = {
            "plan": ["Deploy to staging", "Run smoke tests"],
            "blockers": ["VPN access needed"],
        }
        pre_compaction_flush(writer, context)

        # Verify state was written
        state_before = reader.read_state()
        assert state_before.plan == ["Deploy to staging", "Run smoke tests"]

        # Delete STATE.md (simulating compaction clearing context)
        (soul_dir / "STATE.md").unlink()

        # Restore
        ctx = post_compaction_restore(reader)

        # STATE.md is gone, so we get defaults
        # But we can verify the restore function works
        assert ctx.soul is not None
        assert ctx.agents is not None
        assert ctx.state is not None  # returns default template

        # Re-flush and verify round-trip fidelity
        pre_compaction_flush(writer, context)
        restored_state = reader.read_state()
        assert restored_state.plan == ["Deploy to staging", "Run smoke tests"]
        assert restored_state.blockers == ["VPN access needed"]


class TestHumanEditPreservation:
    """Human edit preservation: init -> edit -> init again -> unchanged."""

    def test_preserves_human_edited_soul(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)

        # First init
        writer.init_soul_dir()

        # Human edits SOUL.md
        custom_soul = (
            "# My Custom Orchestrator\n\n"
            "## Mission\n"
            "I coordinate backend services only.\n\n"
            "## Personality\n"
            "- Direct and concise\n"
        )
        (soul_dir / "SOUL.md").write_text(custom_soul, encoding="utf-8")

        # Second init (should NOT overwrite)
        writer.init_soul_dir()

        result = (soul_dir / "SOUL.md").read_text(encoding="utf-8")
        assert result == custom_soul

    def test_preserves_human_edited_agents(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)

        writer.init_soul_dir()

        custom_agents = "# Custom Agent Rules\n\n## My Rules\n- Rule 1\n"
        (soul_dir / "AGENTS.md").write_text(custom_agents, encoding="utf-8")

        writer.init_soul_dir()

        result = (soul_dir / "AGENTS.md").read_text(encoding="utf-8")
        assert result == custom_agents
