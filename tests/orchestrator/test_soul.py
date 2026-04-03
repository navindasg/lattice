"""Unit tests for SoulFile model, lifecycle, and atomic write.

Tests cover:
- SoulFile.to_markdown() produces five section headers
- SoulFile.from_markdown() round-trips all fields
- SoulFile.from_markdown() raises ValueError on missing sections
- CurrentState markdown rendering
- MemoryEntry rendering
- SoulFile is frozen (immutable)
- write_soul_atomically uses temp+rename (atomic write)
- ContextManagerConfig defaults
- ContextManagerConfig loadable from LatticeSettings
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from lattice.orchestrator.models import ContextManagerConfig
from lattice.orchestrator.soul import (
    CurrentState,
    MemoryEntry,
    SoulFile,
    write_soul_atomically,
)


class TestSoulFileMarkdownSections:
    """SoulFile.to_markdown() produces five required section headers."""

    def test_to_markdown_contains_all_five_headers(self):
        soul = SoulFile(
            instance_id="inst-001",
            identity="A Python developer assistant.",
            project_context="See .agent-docs/ for context.",
            preferences="Concise answers preferred.",
        )
        md = soul.to_markdown()
        assert "## Identity" in md
        assert "## Project Context" in md
        assert "## Current State" in md
        assert "## Preferences" in md
        assert "## Conversation Memory (Compacted)" in md

    def test_to_markdown_has_exactly_five_h2_headers(self):
        soul = SoulFile(
            instance_id="inst-001",
            identity="A Python developer assistant.",
            project_context="See .agent-docs/",
            preferences="Concise.",
        )
        md = soul.to_markdown()
        headers = re.findall(r"^## .+$", md, re.MULTILINE)
        assert len(headers) == 5


class TestSoulFileRoundTrip:
    """SoulFile.from_markdown() round-trips all fields."""

    def _make_soul(self) -> SoulFile:
        return SoulFile(
            instance_id="inst-abc",
            identity="Expert Go engineer.",
            project_context="See .agent-docs/architecture.md",
            current_state=CurrentState(
                completed=["Set up CI"],
                in_progress=["Implementing auth"],
                blocked_on=["Waiting for DB credentials"],
            ),
            preferences="Use stdlib when possible.",
            memory=[
                MemoryEntry(timestamp="14:22", content="fixed auth bug"),
                MemoryEntry(timestamp="15:00", content="added tests"),
            ],
            compaction_count=2,
        )

    def test_round_trip_all_fields(self):
        original = self._make_soul()
        md = original.to_markdown()
        restored = SoulFile.from_markdown("inst-abc", md)

        assert restored.instance_id == original.instance_id
        assert restored.identity == original.identity
        assert restored.project_context == original.project_context
        assert restored.preferences == original.preferences

    def test_round_trip_current_state(self):
        original = self._make_soul()
        md = original.to_markdown()
        restored = SoulFile.from_markdown("inst-abc", md)

        assert "Set up CI" in restored.current_state.completed
        assert "Implementing auth" in restored.current_state.in_progress
        assert "Waiting for DB credentials" in restored.current_state.blocked_on

    def test_round_trip_memory_entries(self):
        original = self._make_soul()
        md = original.to_markdown()
        restored = SoulFile.from_markdown("inst-abc", md)

        assert len(restored.memory) == 2
        assert restored.memory[0].timestamp == "14:22"
        assert restored.memory[0].content == "fixed auth bug"
        assert restored.memory[1].timestamp == "15:00"
        assert restored.memory[1].content == "added tests"


class TestSoulFileFromMarkdownValidation:
    """SoulFile.from_markdown() raises ValueError when sections are missing."""

    def test_raises_value_error_missing_identity_section(self):
        incomplete_md = (
            "## Project Context\nSee .agent-docs/\n\n"
            "## Current State\n\n"
            "## Preferences\nBe concise.\n\n"
            "## Conversation Memory (Compacted)\n"
        )
        with pytest.raises(ValueError, match="missing sections"):
            SoulFile.from_markdown("inst-001", incomplete_md)

    def test_raises_value_error_missing_memory_section(self):
        incomplete_md = (
            "## Identity\nDeveloper assistant.\n\n"
            "## Project Context\nSee .agent-docs/\n\n"
            "## Current State\n\n"
            "## Preferences\nBe concise.\n"
        )
        with pytest.raises(ValueError, match="missing sections"):
            SoulFile.from_markdown("inst-001", incomplete_md)

    def test_raises_value_error_empty_string(self):
        with pytest.raises(ValueError, match="missing sections"):
            SoulFile.from_markdown("inst-001", "")


class TestCurrentStateMarkdown:
    """CurrentState renders lists with correct prefixes."""

    def test_current_state_completed_prefix(self):
        soul = SoulFile(
            instance_id="inst-001",
            identity="Dev.",
            project_context="Docs.",
            current_state=CurrentState(completed=["Deployed API"]),
            preferences="",
        )
        md = soul.to_markdown()
        assert "- Completed: Deployed API" in md

    def test_current_state_in_progress_prefix(self):
        soul = SoulFile(
            instance_id="inst-001",
            identity="Dev.",
            project_context="Docs.",
            current_state=CurrentState(in_progress=["Writing tests"]),
            preferences="",
        )
        md = soul.to_markdown()
        assert "- In progress: Writing tests" in md

    def test_current_state_blocked_on_prefix(self):
        soul = SoulFile(
            instance_id="inst-001",
            identity="Dev.",
            project_context="Docs.",
            current_state=CurrentState(blocked_on=["Awaiting review"]),
            preferences="",
        )
        md = soul.to_markdown()
        assert "- Blocked on: Awaiting review" in md

    def test_current_state_defaults_to_empty_lists(self):
        state = CurrentState()
        assert state.completed == []
        assert state.in_progress == []
        assert state.blocked_on == []


class TestMemoryEntryRendering:
    """MemoryEntry renders as [timestamp] content."""

    def test_memory_entry_renders_correctly(self):
        soul = SoulFile(
            instance_id="inst-001",
            identity="Dev.",
            project_context="Docs.",
            preferences="",
            memory=[MemoryEntry(timestamp="14:22", content="fixed auth bug")],
        )
        md = soul.to_markdown()
        assert "- [14:22] fixed auth bug" in md

    def test_memory_entry_model_fields(self):
        entry = MemoryEntry(timestamp="09:00", content="reviewed PR")
        assert entry.timestamp == "09:00"
        assert entry.content == "reviewed PR"


class TestSoulFileImmutability:
    """SoulFile is frozen — mutation raises ValidationError."""

    def test_frozen_raises_on_field_assignment(self):
        soul = SoulFile(
            instance_id="inst-001",
            identity="Dev.",
            project_context="Docs.",
            preferences="",
        )
        with pytest.raises((ValidationError, TypeError)):
            soul.identity = "new identity"  # type: ignore[misc]

    def test_memory_entry_is_frozen(self):
        entry = MemoryEntry(timestamp="10:00", content="did something")
        with pytest.raises((ValidationError, TypeError)):
            entry.content = "changed"  # type: ignore[misc]

    def test_current_state_is_frozen(self):
        state = CurrentState(completed=["task1"])
        with pytest.raises((ValidationError, TypeError)):
            state.completed = []  # type: ignore[misc]


class TestWriteSoulAtomically:
    """write_soul_atomically uses temp file + rename for atomicity."""

    def test_writes_content_to_path(self, tmp_path):
        target = tmp_path / "test-soul.md"
        content = "## Identity\nDeveloper assistant."
        write_soul_atomically(target, content)
        assert target.exists()
        assert target.read_text() == content

    def test_no_temp_file_after_write(self, tmp_path):
        target = tmp_path / "test-soul.md"
        content = "## Identity\nDeveloper assistant."
        write_soul_atomically(target, content)
        tmp_file = target.with_suffix(".tmp")
        assert not tmp_file.exists()

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / "soul.md"
        target.write_text("old content")
        write_soul_atomically(target, "new content")
        assert target.read_text() == "new content"

    def test_creates_parent_directories_not_required(self, tmp_path):
        # File in existing directory — should work normally
        target = tmp_path / "soul.md"
        write_soul_atomically(target, "content")
        assert target.exists()


class TestContextManagerConfig:
    """ContextManagerConfig has correct defaults."""

    def test_default_compaction_threshold(self):
        config = ContextManagerConfig()
        assert config.compaction_threshold == 55.0

    def test_default_window_tokens(self):
        config = ContextManagerConfig()
        assert config.window_tokens == 128_000

    def test_default_verification_enabled(self):
        config = ContextManagerConfig()
        assert config.verification_enabled is True

    def test_custom_values(self):
        config = ContextManagerConfig(
            compaction_threshold=70.0,
            window_tokens=200_000,
            verification_enabled=False,
        )
        assert config.compaction_threshold == 70.0
        assert config.window_tokens == 200_000
        assert config.verification_enabled is False


class TestContextManagerConfigInLatticeSettings:
    """ContextManagerConfig is loadable from LatticeSettings."""

    def test_lattice_settings_has_context_manager_field(self):
        from lattice.llm.config import LatticeSettings

        settings = LatticeSettings()
        assert hasattr(settings, "context_manager")

    def test_lattice_settings_context_manager_threshold(self):
        from lattice.llm.config import LatticeSettings

        settings = LatticeSettings()
        assert settings.context_manager.compaction_threshold == 55.0

    def test_lattice_settings_context_manager_window_tokens(self):
        from lattice.llm.config import LatticeSettings

        settings = LatticeSettings()
        assert settings.context_manager.window_tokens == 128_000


class TestSoulFileProjectId:
    """SoulFile carries optional project_id and round-trips via markdown comment."""

    def test_soul_project_id_defaults_none(self):
        """SoulFile.project_id defaults to None for backward compatibility."""
        soul = SoulFile(
            instance_id="inst-001",
            identity="Worker",
            project_context="See docs.",
            preferences="",
        )
        assert soul.project_id is None

    def test_soul_project_id_roundtrip(self):
        """SoulFile.project_id round-trips through to_markdown/from_markdown."""
        soul = SoulFile(
            instance_id="inst-abc",
            identity="Worker",
            project_context="See docs.",
            preferences="",
            project_id="project_alpha",
        )
        md = soul.to_markdown()
        assert "project_alpha" in md  # project_id embedded in markdown
        restored = SoulFile.from_markdown("inst-abc", md)
        assert restored.project_id == "project_alpha"

    def test_soul_from_markdown_no_project_id_backward_compat(self):
        """SoulFile.from_markdown still works on existing soul files without project_id."""
        old_soul = SoulFile(
            instance_id="inst-old",
            identity="Old worker",
            project_context="See docs.",
            preferences="",
        )
        # to_markdown without project_id (None) should not include comment
        md = old_soul.to_markdown()
        assert "<!-- project_id:" not in md
        # from_markdown on soul without project_id comment must succeed
        restored = SoulFile.from_markdown("inst-old", md)
        assert restored.project_id is None
        assert restored.identity == "Old worker"

    def test_soul_project_id_in_to_markdown(self):
        """to_markdown embeds project_id as HTML comment when set."""
        soul = SoulFile(
            instance_id="inst-x",
            identity="Worker X",
            project_context="docs",
            preferences="",
            project_id="my-project",
        )
        md = soul.to_markdown()
        assert "<!-- project_id: my-project -->" in md

    def test_soul_project_id_none_no_comment_in_markdown(self):
        """to_markdown does NOT include project_id comment when project_id is None."""
        soul = SoulFile(
            instance_id="inst-x",
            identity="Worker X",
            project_context="docs",
            preferences="",
            project_id=None,
        )
        md = soul.to_markdown()
        assert "<!-- project_id:" not in md
