"""Unit tests for SoulWriter.

Tests cover:
- update_state replaces target section content
- update_state preserves other sections unchanged
- update_state handles section at end of file
- update_full_state writes complete OrchestratorState
- append_memory adds entry to MEMORY.md
- append_memory preserves existing entries
- append_memory entry format
- init_soul_dir creates directory and all 4 files
- init_soul_dir preserves existing files
- Atomic write: no .tmp files left
- Concurrent writes: 10 simultaneous update_state calls don't corrupt
"""
from __future__ import annotations

import threading
from pathlib import Path

from lattice.orchestrator.soul_ecosystem.models import (
    InstanceAssignment,
    OrchestratorState,
    SoulMemoryEntry,
)
from lattice.orchestrator.soul_ecosystem.templates import (
    AGENTS_TEMPLATE,
    MEMORY_TEMPLATE,
    SOUL_TEMPLATE,
    STATE_TEMPLATE,
)
from lattice.orchestrator.soul_ecosystem.writer import SoulWriter


def _write_file(path: Path, content: str) -> None:
    """Write content to a file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestUpdateState:
    """update_state replaces target section content."""

    def test_replaces_target_section(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "STATE.md", STATE_TEMPLATE)

        writer = SoulWriter(soul_dir)
        writer.update_state("Plan", "- Build feature A\n- Test feature A")

        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "- Build feature A" in content
        assert "- Test feature A" in content

    def test_preserves_other_sections(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "STATE.md", STATE_TEMPLATE)

        writer = SoulWriter(soul_dir)
        writer.update_state("Plan", "- New plan item")

        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "## Instances" in content
        assert "_No active instances_" in content
        assert "## Decisions" in content
        assert "## Blockers" in content

    def test_handles_last_section(self, tmp_path: Path):
        """Blockers is the last section (no following ## header)."""
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "STATE.md", STATE_TEMPLATE)

        writer = SoulWriter(soul_dir)
        writer.update_state("Blockers", "- CI is failing\n- Need review")

        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "- CI is failing" in content
        assert "- Need review" in content

    def test_creates_state_file_if_missing(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.update_state("Plan", "- Step 1")

        assert (soul_dir / "STATE.md").exists()
        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "- Step 1" in content


class TestUpdateFullState:
    """update_full_state writes complete OrchestratorState."""

    def test_writes_full_state(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)

        state = OrchestratorState(
            instances=[
                InstanceAssignment(
                    instance_id="inst-001",
                    task_description="Build API",
                    status="active",
                    assigned_at="2026-04-07T12:00:00Z",
                )
            ],
            plan=["Finish API", "Write tests"],
            blockers=["Need DB access"],
        )
        writer.update_full_state(state)

        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "**inst-001**" in content
        assert "Build API" in content
        assert "Finish API" in content
        assert "Write tests" in content
        assert "Need DB access" in content


class TestAppendMemory:
    """append_memory adds entries to MEMORY.md."""

    def test_adds_entry(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "MEMORY.md", MEMORY_TEMPLATE)

        writer = SoulWriter(soul_dir)
        entry = SoulMemoryEntry(
            timestamp="2026-04-07T12:00:00Z",
            category="preference",
            content="User prefers concise output",
        )
        writer.append_memory(entry)

        content = (soul_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "- [2026-04-07T12:00:00Z] [preference] User prefers concise output" in content

    def test_preserves_existing_entries(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        initial = (
            "# Orchestrator Memory\n\n"
            "- [2026-04-07T10:00:00Z] [convention] Use snake_case\n"
        )
        _write_file(soul_dir / "MEMORY.md", initial)

        writer = SoulWriter(soul_dir)
        entry = SoulMemoryEntry(
            timestamp="2026-04-07T12:00:00Z",
            category="preference",
            content="Dark mode",
        )
        writer.append_memory(entry)

        content = (soul_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "[convention] Use snake_case" in content
        assert "[preference] Dark mode" in content

    def test_entry_format(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)

        entry = SoulMemoryEntry(
            timestamp="2026-04-07T12:00:00Z",
            category="pattern",
            content="Retry failures up to 3 times",
        )
        writer.append_memory(entry)

        content = (soul_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "- [2026-04-07T12:00:00Z] [pattern] Retry failures up to 3 times\n" in content

    def test_creates_memory_file_if_missing(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)

        entry = SoulMemoryEntry(
            timestamp="2026-04-07T12:00:00Z",
            category="preference",
            content="test",
        )
        writer.append_memory(entry)

        assert (soul_dir / "MEMORY.md").exists()


class TestInitSoulDir:
    """init_soul_dir creates directory and all 4 files."""

    def test_creates_all_files(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        assert (soul_dir / "SOUL.md").exists()
        assert (soul_dir / "AGENTS.md").exists()
        assert (soul_dir / "STATE.md").exists()
        assert (soul_dir / "MEMORY.md").exists()

    def test_files_contain_templates(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        assert (soul_dir / "SOUL.md").read_text(encoding="utf-8") == SOUL_TEMPLATE
        assert (soul_dir / "AGENTS.md").read_text(encoding="utf-8") == AGENTS_TEMPLATE
        assert (soul_dir / "STATE.md").read_text(encoding="utf-8") == STATE_TEMPLATE
        assert (soul_dir / "MEMORY.md").read_text(encoding="utf-8") == MEMORY_TEMPLATE

    def test_preserves_existing_files(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        custom_soul = "# My Custom Soul\n\nI am unique."
        _write_file(soul_dir / "SOUL.md", custom_soul)

        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        assert (soul_dir / "SOUL.md").read_text(encoding="utf-8") == custom_soul
        # Other files should be created with defaults
        assert (soul_dir / "AGENTS.md").exists()
        assert (soul_dir / "STATE.md").exists()
        assert (soul_dir / "MEMORY.md").exists()

    def test_creates_directory_if_missing(self, tmp_path: Path):
        soul_dir = tmp_path / "deep" / "nested" / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        assert soul_dir.is_dir()
        assert (soul_dir / "SOUL.md").exists()


class TestAtomicWrite:
    """Atomic write leaves no .tmp files."""

    def test_no_tmp_files_after_write(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        writer.update_state("Plan", "- Step 1")

        tmp_files = list(soul_dir.glob("*.tmp"))
        assert tmp_files == []

    def test_no_tmp_files_after_append_memory(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        entry = SoulMemoryEntry(
            timestamp="2026-04-07T12:00:00Z",
            category="test",
            content="test content",
        )
        writer.append_memory(entry)

        tmp_files = list(soul_dir.glob("*.tmp"))
        assert tmp_files == []


class TestConcurrentWrites:
    """10 simultaneous update_state calls don't corrupt file."""

    def test_concurrent_updates(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        errors: list[Exception] = []

        def update_plan(i: int) -> None:
            try:
                writer.update_state("Plan", f"- Task {i}")
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=update_plan, args=(i,))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent write errors: {errors}"

        # File should be readable and valid
        content = (soul_dir / "STATE.md").read_text(encoding="utf-8")
        assert "## Instances" in content
        assert "## Plan" in content
        assert "## Decisions" in content
        assert "## Blockers" in content
        # Should contain one of the task values (last writer wins)
        assert "- Task " in content
