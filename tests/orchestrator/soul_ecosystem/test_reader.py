"""Unit tests for SoulReader.

Tests cover:
- read_all returns SoulContext with all 4 fields populated
- read_all returns default content for missing files
- read_all returns default content when soul_dir doesn't exist
- build_system_prompt includes SOUL.md, AGENTS.md, STATE.md content
- build_system_prompt does NOT include MEMORY.md content
- build_system_prompt has clear section headers
- read_state returns OrchestratorState from STATE.md
- read_memory_entries parses entries with timestamp and category
- query_memory filters by category
"""
from __future__ import annotations

from pathlib import Path

from lattice.orchestrator.soul_ecosystem.models import (
    OrchestratorState,
    SoulContext,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.soul_ecosystem.templates import (
    AGENTS_TEMPLATE,
    MEMORY_TEMPLATE,
    SOUL_TEMPLATE,
    STATE_TEMPLATE,
)


def _write_file(path: Path, content: str) -> None:
    """Write content to a file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestReadAll:
    """read_all returns SoulContext with all 4 fields."""

    def test_returns_soul_context(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "SOUL.md", "Custom soul")
        _write_file(soul_dir / "AGENTS.md", "Custom agents")
        _write_file(soul_dir / "STATE.md", "Custom state")
        _write_file(soul_dir / "MEMORY.md", "Custom memory")

        reader = SoulReader(soul_dir)
        ctx = reader.read_all()

        assert isinstance(ctx, SoulContext)
        assert ctx.soul == "Custom soul"
        assert ctx.agents == "Custom agents"
        assert ctx.state == "Custom state"
        assert ctx.memory == "Custom memory"

    def test_returns_defaults_for_missing_files(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()

        reader = SoulReader(soul_dir)
        ctx = reader.read_all()

        assert ctx.soul == SOUL_TEMPLATE
        assert ctx.agents == AGENTS_TEMPLATE
        assert ctx.state == STATE_TEMPLATE
        assert ctx.memory == MEMORY_TEMPLATE

    def test_returns_defaults_when_soul_dir_missing(self, tmp_path: Path):
        soul_dir = tmp_path / "nonexistent"

        reader = SoulReader(soul_dir)
        ctx = reader.read_all()

        assert ctx.soul == SOUL_TEMPLATE
        assert ctx.agents == AGENTS_TEMPLATE
        assert ctx.state == STATE_TEMPLATE
        assert ctx.memory == MEMORY_TEMPLATE

    def test_partial_files_returns_mix_of_custom_and_default(
        self, tmp_path: Path
    ):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "SOUL.md", "My soul")

        reader = SoulReader(soul_dir)
        ctx = reader.read_all()

        assert ctx.soul == "My soul"
        assert ctx.agents == AGENTS_TEMPLATE


class TestBuildSystemPrompt:
    """build_system_prompt assembles files into prompt."""

    def test_includes_soul_content(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "SOUL.md", "I am the orchestrator")

        reader = SoulReader(soul_dir)
        prompt = reader.build_system_prompt()

        assert "I am the orchestrator" in prompt

    def test_includes_agents_content(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "AGENTS.md", "Approval rules here")

        reader = SoulReader(soul_dir)
        prompt = reader.build_system_prompt()

        assert "Approval rules here" in prompt

    def test_includes_state_content(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "STATE.md", "Current state data")

        reader = SoulReader(soul_dir)
        prompt = reader.build_system_prompt()

        assert "Current state data" in prompt

    def test_does_not_include_memory_content(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "MEMORY.md", "SECRET_MEMORY_CONTENT_XYZ")

        reader = SoulReader(soul_dir)
        prompt = reader.build_system_prompt()

        assert "SECRET_MEMORY_CONTENT_XYZ" not in prompt

    def test_has_identity_section_header(self, tmp_path: Path):
        reader = SoulReader(tmp_path / "soul")
        prompt = reader.build_system_prompt()
        assert "=== IDENTITY ===" in prompt

    def test_has_procedures_section_header(self, tmp_path: Path):
        reader = SoulReader(tmp_path / "soul")
        prompt = reader.build_system_prompt()
        assert "=== PROCEDURES ===" in prompt

    def test_has_current_state_section_header(self, tmp_path: Path):
        reader = SoulReader(tmp_path / "soul")
        prompt = reader.build_system_prompt()
        assert "=== CURRENT STATE ===" in prompt


class TestReadState:
    """read_state returns OrchestratorState from STATE.md."""

    def test_parses_populated_state(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        state = OrchestratorState(
            plan=["Do thing 1", "Do thing 2"],
            blockers=["Need credentials"],
        )
        _write_file(soul_dir / "STATE.md", state.to_markdown())

        reader = SoulReader(soul_dir)
        result = reader.read_state()

        assert result.plan == ["Do thing 1", "Do thing 2"]
        assert result.blockers == ["Need credentials"]

    def test_returns_empty_state_for_missing_file(self, tmp_path: Path):
        reader = SoulReader(tmp_path / "soul")
        result = reader.read_state()

        assert result.instances == []
        assert result.plan == []


class TestReadMemoryEntries:
    """read_memory_entries parses entries with timestamp and category."""

    def test_parses_entries(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        content = (
            "# Orchestrator Memory\n\n"
            "- [2026-04-07T12:00:00Z] [preference] User prefers concise output\n"
            "- [2026-04-07T13:00:00Z] [convention] Use snake_case for Python\n"
        )
        _write_file(soul_dir / "MEMORY.md", content)

        reader = SoulReader(soul_dir)
        entries = reader.read_memory_entries()

        assert len(entries) == 2
        assert entries[0].timestamp == "2026-04-07T12:00:00Z"
        assert entries[0].category == "preference"
        assert entries[0].content == "User prefers concise output"
        assert entries[1].category == "convention"

    def test_returns_empty_for_no_entries(self, tmp_path: Path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        _write_file(soul_dir / "MEMORY.md", MEMORY_TEMPLATE)

        reader = SoulReader(soul_dir)
        entries = reader.read_memory_entries()

        assert entries == []


class TestQueryMemory:
    """query_memory filters by category."""

    def _setup_memory(self, tmp_path: Path) -> SoulReader:
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        content = (
            "# Orchestrator Memory\n\n"
            "- [2026-04-07T12:00:00Z] [preference] Concise output\n"
            "- [2026-04-07T13:00:00Z] [convention] Use snake_case\n"
            "- [2026-04-07T14:00:00Z] [preference] Dark mode\n"
        )
        _write_file(soul_dir / "MEMORY.md", content)
        return SoulReader(soul_dir)

    def test_query_all(self, tmp_path: Path):
        reader = self._setup_memory(tmp_path)
        entries = reader.query_memory()
        assert len(entries) == 3

    def test_query_by_category(self, tmp_path: Path):
        reader = self._setup_memory(tmp_path)
        entries = reader.query_memory(category="preference")
        assert len(entries) == 2
        assert all(e.category == "preference" for e in entries)

    def test_query_nonexistent_category(self, tmp_path: Path):
        reader = self._setup_memory(tmp_path)
        entries = reader.query_memory(category="nonexistent")
        assert entries == []
