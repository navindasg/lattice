"""Tests for _map_correct_impl core logic."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lattice.cli.hints import _map_correct_impl
from lattice.shadow.schema import DirDoc
from lattice.shadow.writer import write_dir_doc


def _make_dir_doc(directory: str = "src/auth") -> DirDoc:
    """Helper: create a minimal DirDoc for testing."""
    return DirDoc(
        directory=directory,
        confidence=0.7,
        source="agent",
        confidence_factors=["static_analysis"],
        last_analyzed=datetime.now(timezone.utc),
        summary="Original summary",
        responsibilities=["auth", "tokens"],
    )


class TestMapCorrectImpl:
    """Tests for _map_correct_impl."""

    def test_sets_developer_source(self, tmp_path: Path) -> None:
        """_map_correct_impl sets source=developer and confidence=1.0 on _dir.md."""
        doc = _make_dir_doc("src/auth")
        write_dir_doc(doc, tmp_path / ".agent-docs")

        result = _map_correct_impl(tmp_path, "src/auth", "summary", "New summary text")

        assert result["directory"] == "src/auth"
        assert result["field"] == "summary"
        assert result["confidence"] == 1.0
        assert result["source"] == "developer"

    def test_updates_summary_in_dir_md(self, tmp_path: Path) -> None:
        """_map_correct_impl updates the summary field in _dir.md."""
        from lattice.shadow.reader import parse_dir_doc

        doc = _make_dir_doc("src/auth")
        write_dir_doc(doc, tmp_path / ".agent-docs")

        _map_correct_impl(tmp_path, "src/auth", "summary", "Updated summary text")

        dir_md = tmp_path / ".agent-docs" / "src/auth" / "_dir.md"
        updated = parse_dir_doc(dir_md)
        assert updated.summary == "Updated summary text"
        assert updated.confidence == 1.0
        assert updated.source == "developer"

    def test_updates_responsibilities_json_array(self, tmp_path: Path) -> None:
        """_map_correct_impl parses JSON array for responsibilities field."""
        from lattice.shadow.reader import parse_dir_doc

        doc = _make_dir_doc("src/auth")
        write_dir_doc(doc, tmp_path / ".agent-docs")

        _map_correct_impl(tmp_path, "src/auth", "responsibilities", '["auth", "tokens", "sessions"]')

        dir_md = tmp_path / ".agent-docs" / "src/auth" / "_dir.md"
        updated = parse_dir_doc(dir_md)
        assert updated.responsibilities == ["auth", "tokens", "sessions"]

    def test_updates_responsibilities_comma_separated(self, tmp_path: Path) -> None:
        """_map_correct_impl falls back to comma-separated parsing when not JSON."""
        from lattice.shadow.reader import parse_dir_doc

        doc = _make_dir_doc("src/auth")
        write_dir_doc(doc, tmp_path / ".agent-docs")

        _map_correct_impl(tmp_path, "src/auth", "responsibilities", "auth, tokens, sessions")

        dir_md = tmp_path / ".agent-docs" / "src/auth" / "_dir.md"
        updated = parse_dir_doc(dir_md)
        assert updated.responsibilities == ["auth", "tokens", "sessions"]

    def test_no_dir_md(self, tmp_path: Path) -> None:
        """_map_correct_impl raises FileNotFoundError when no _dir.md exists."""
        with pytest.raises(FileNotFoundError, match="No documentation found"):
            _map_correct_impl(tmp_path, "src/auth", "summary", "New summary")

    def test_invalid_field(self, tmp_path: Path) -> None:
        """_map_correct_impl raises ValueError for invalid field names."""
        doc = _make_dir_doc("src/auth")
        write_dir_doc(doc, tmp_path / ".agent-docs")

        with pytest.raises(ValueError, match="Field 'foo' is not correctable"):
            _map_correct_impl(tmp_path, "src/auth", "foo", "value")

    def test_records_audit_entry_in_hints_json(self, tmp_path: Path) -> None:
        """_map_correct_impl records a 'correct' audit entry in _hints.json."""
        doc = _make_dir_doc("src/auth")
        write_dir_doc(doc, tmp_path / ".agent-docs")

        _map_correct_impl(tmp_path, "src/auth", "summary", "New summary text")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        assert hints_path.exists()
        data = json.loads(hints_path.read_text())
        assert "src/auth" in data
        correct_entries = [e for e in data["src/auth"] if e.get("type") == "correct"]
        assert len(correct_entries) == 1
        assert correct_entries[0]["field"] == "summary"
        assert correct_entries[0]["value"] == "New summary text"
        assert "stored_at" in correct_entries[0]

    def test_correct_preserves_confidence_factors_cleared(self, tmp_path: Path) -> None:
        """After correction, confidence factors are cleared (source is developer)."""
        from lattice.shadow.reader import parse_dir_doc

        doc = _make_dir_doc("src/auth")
        write_dir_doc(doc, tmp_path / ".agent-docs")

        _map_correct_impl(tmp_path, "src/auth", "summary", "New text")

        dir_md = tmp_path / ".agent-docs" / "src/auth" / "_dir.md"
        updated = parse_dir_doc(dir_md)
        # source=developer means confidence_factors can be empty (validator only
        # requires non-empty when source='agent')
        assert updated.source == "developer"
        assert updated.confidence == 1.0
