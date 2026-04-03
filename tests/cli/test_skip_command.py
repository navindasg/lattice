"""Tests for _map_skip_impl core logic."""
import json
from pathlib import Path

import pytest

from lattice.cli.hints import _map_skip_impl


class TestMapSkipImpl:
    """Tests for _map_skip_impl."""

    def test_stores_skip_entry(self, tmp_path: Path) -> None:
        """_map_skip_impl stores a type=skip entry in _hints.json."""
        result = _map_skip_impl(tmp_path, "src/vendor")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        assert hints_path.exists()

        data = json.loads(hints_path.read_text())
        assert "src/vendor" in data
        skip_entries = [e for e in data["src/vendor"] if e.get("type") == "skip"]
        assert len(skip_entries) == 1
        assert "text" not in skip_entries[0]
        assert "stored_at" in skip_entries[0]

    def test_returns_skipped_true(self, tmp_path: Path) -> None:
        """_map_skip_impl returns {"directory": ..., "skipped": True}."""
        result = _map_skip_impl(tmp_path, "src/vendor")

        assert result["directory"] == "src/vendor"
        assert result["skipped"] is True

    def test_skip_upsert_replaces_existing(self, tmp_path: Path) -> None:
        """Calling _map_skip_impl twice only keeps one skip entry (upsert)."""
        _map_skip_impl(tmp_path, "src/vendor")
        _map_skip_impl(tmp_path, "src/vendor")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())
        skip_entries = [e for e in data["src/vendor"] if e.get("type") == "skip"]
        assert len(skip_entries) == 1

    def test_skip_creates_agent_docs_dir(self, tmp_path: Path) -> None:
        """_map_skip_impl creates .agent-docs/ if it does not exist."""
        assert not (tmp_path / ".agent-docs").exists()
        _map_skip_impl(tmp_path, "src/vendor")
        assert (tmp_path / ".agent-docs").exists()
