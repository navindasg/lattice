"""Tests for the map:hint CLI command core logic."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from lattice.cli.hints import _map_hint_impl


class TestMapHintImpl:
    """Tests for _map_hint_impl core logic."""

    def test_hint_creates_hints_json(self, tmp_path: Path) -> None:
        """_map_hint_impl creates .agent-docs/_hints.json with correct structure."""
        result = _map_hint_impl(tmp_path, "src/auth", "handles OAuth")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        assert hints_path.exists(), "_hints.json should be created"

        data = json.loads(hints_path.read_text())
        assert "src/auth" in data
        assert len(data["src/auth"]) == 1
        assert data["src/auth"][0]["text"] == "handles OAuth"

    def test_hint_return_value(self, tmp_path: Path) -> None:
        """_map_hint_impl returns dict with directory and hint_count."""
        result = _map_hint_impl(tmp_path, "src/auth", "handles OAuth")

        assert result["directory"] == "src/auth"
        assert result["hint_count"] == 1

    def test_hint_appends_to_existing(self, tmp_path: Path) -> None:
        """Storing two hints for the same directory appends (does not overwrite)."""
        _map_hint_impl(tmp_path, "src/auth", "handles OAuth")
        result = _map_hint_impl(tmp_path, "src/auth", "also handles SAML")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())

        assert len(data["src/auth"]) == 2
        assert result["hint_count"] == 2
        texts = [entry["text"] for entry in data["src/auth"]]
        assert "handles OAuth" in texts
        assert "also handles SAML" in texts

    def test_hint_multiple_directories(self, tmp_path: Path) -> None:
        """Hints for different directories coexist under separate keys."""
        _map_hint_impl(tmp_path, "src/auth", "auth hint")
        _map_hint_impl(tmp_path, "src/db", "db hint")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())

        assert "src/auth" in data
        assert "src/db" in data
        assert data["src/auth"][0]["text"] == "auth hint"
        assert data["src/db"][0]["text"] == "db hint"

    def test_hint_entry_has_stored_at(self, tmp_path: Path) -> None:
        """Each hint entry has a 'stored_at' ISO 8601 string."""
        _map_hint_impl(tmp_path, "src/auth", "OAuth hint")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())

        entry = data["src/auth"][0]
        assert "stored_at" in entry
        # Must be parseable as ISO 8601
        stored_at = datetime.fromisoformat(entry["stored_at"])
        assert stored_at is not None

    def test_hint_creates_agent_docs_dir(self, tmp_path: Path) -> None:
        """_map_hint_impl creates .agent-docs/ if it does not exist."""
        assert not (tmp_path / ".agent-docs").exists()

        _map_hint_impl(tmp_path, "src/api", "API hint")

        assert (tmp_path / ".agent-docs").exists()

    def test_hint_atomic_write(self, tmp_path: Path) -> None:
        """Temporary file should not persist after write."""
        _map_hint_impl(tmp_path, "src/auth", "OAuth hint")

        agent_docs = tmp_path / ".agent-docs"
        tmp_file = agent_docs / "_hints.json.tmp"
        assert not tmp_file.exists(), "Temp file should be removed after atomic write"

    def test_hint_text_preserved_exactly(self, tmp_path: Path) -> None:
        """Hint text is stored exactly as provided, including special characters."""
        special_text = 'handles "OAuth 2.0" & SAML <tokens>'
        _map_hint_impl(tmp_path, "src/auth", special_text)

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())

        assert data["src/auth"][0]["text"] == special_text

    def test_hint_stores_type_field(self, tmp_path: Path) -> None:
        """Default hint type stores 'type': 'hint' in entry."""
        _map_hint_impl(tmp_path, "src/auth", "handles OAuth", hint_type="hint")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())

        entry = data["src/auth"][0]
        assert entry["type"] == "hint"
        assert entry["text"] == "handles OAuth"

    def test_idk_flag(self, tmp_path: Path) -> None:
        """_map_hint_impl with hint_type='idk' stores entry with type=idk, no text field."""
        result = _map_hint_impl(tmp_path, "src/auth", None, hint_type="idk")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())

        assert "src/auth" in data
        assert len(data["src/auth"]) == 1
        entry = data["src/auth"][0]
        assert entry["type"] == "idk"
        assert "text" not in entry
        assert "stored_at" in entry
        assert result["hint_count"] == 1

    def test_expand_type_stores_type_and_text(self, tmp_path: Path) -> None:
        """hint_type='expand' stores entry with type=expand and text."""
        _map_hint_impl(tmp_path, "src/auth", "expand this", hint_type="expand")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())

        entry = data["src/auth"][0]
        assert entry["type"] == "expand"
        assert entry["text"] == "expand this"

    def test_deduplication(self, tmp_path: Path) -> None:
        """Calling _map_hint_impl twice with same text returns same hint_count (no duplication)."""
        result1 = _map_hint_impl(tmp_path, "src/auth", "handles OAuth", hint_type="hint")
        result2 = _map_hint_impl(tmp_path, "src/auth", "handles OAuth", hint_type="hint")

        assert result1["hint_count"] == 1
        assert result2["hint_count"] == 1

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())
        assert len(data["src/auth"]) == 1

    def test_idk_upsert_replaces_existing_idk(self, tmp_path: Path) -> None:
        """Adding idk twice only keeps one idk entry (upsert behavior)."""
        _map_hint_impl(tmp_path, "src/auth", None, hint_type="idk")
        _map_hint_impl(tmp_path, "src/auth", None, hint_type="idk")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())

        idk_entries = [e for e in data["src/auth"] if e["type"] == "idk"]
        assert len(idk_entries) == 1

    def test_idk_cleared_by_hint(self, tmp_path: Path) -> None:
        """Adding a non-IDK hint clears existing IDK entries for that directory."""
        _map_hint_impl(tmp_path, "src/auth", None, hint_type="idk")
        _map_hint_impl(tmp_path, "src/auth", "handles OAuth", hint_type="hint")

        hints_path = tmp_path / ".agent-docs" / "_hints.json"
        data = json.loads(hints_path.read_text())

        idk_entries = [e for e in data["src/auth"] if e["type"] == "idk"]
        assert len(idk_entries) == 0

        hint_entries = [e for e in data["src/auth"] if e["type"] == "hint"]
        assert len(hint_entries) == 1

    def test_backward_compat_entries_without_type(self, tmp_path: Path) -> None:
        """Entries without 'type' field are treated as type='hint' during reads."""
        # Pre-seed _hints.json with an entry that has no 'type' field (old format)
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir(parents=True)
        hints_path = agent_docs / "_hints.json"
        old_data = {
            "src/auth": [
                {"text": "legacy hint", "stored_at": "2024-01-01T00:00:00+00:00"}
            ]
        }
        hints_path.write_text(json.dumps(old_data), encoding="utf-8")

        # Adding a new hint should append (not deduplicate against legacy entry)
        result = _map_hint_impl(tmp_path, "src/auth", "new hint", hint_type="hint")

        data = json.loads(hints_path.read_text())
        assert len(data["src/auth"]) == 2
        assert result["hint_count"] == 2

    def test_backward_compat_idk_does_not_clear_legacy(self, tmp_path: Path) -> None:
        """Legacy entries (no type) treated as hint — adding IDK does not clear them."""
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir(parents=True)
        hints_path = agent_docs / "_hints.json"
        old_data = {
            "src/auth": [
                {"text": "legacy hint", "stored_at": "2024-01-01T00:00:00+00:00"}
            ]
        }
        hints_path.write_text(json.dumps(old_data), encoding="utf-8")

        # Adding IDK shouldn't remove the legacy entry
        _map_hint_impl(tmp_path, "src/auth", None, hint_type="idk")

        data = json.loads(hints_path.read_text())
        texts = [e.get("text") for e in data["src/auth"]]
        assert "legacy hint" in texts
