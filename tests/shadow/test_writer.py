"""Tests for shadow tree writer — write_dir_doc."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from lattice.shadow.schema import DirDoc
from lattice.shadow.writer import write_dir_doc

_NOW = datetime.now(timezone.utc)


def make_dir_doc(**kwargs) -> DirDoc:
    defaults = {
        "directory": "src/lattice",
        "confidence": 0.8,
        "source": "agent",
        "confidence_factors": ["imports resolved", "all functions documented"],
        "last_analyzed": _NOW,
        "summary": "Codebase intelligence module.",
        "responsibilities": ["Parse imports", "Build graph"],
        "developer_hints": ["Use model_copy for updates"],
        "child_refs": ["src/lattice/models"],
    }
    defaults.update(kwargs)
    return DirDoc(**defaults)


class TestWriteDirDoc:
    def test_writes_to_correct_shadow_path(self, tmp_path: Path):
        doc = make_dir_doc()
        written = write_dir_doc(doc, tmp_path)
        expected = tmp_path / "src" / "lattice" / "_dir.md"
        assert written == expected
        assert written.exists()

    def test_creates_parent_directories(self, tmp_path: Path):
        doc = make_dir_doc(directory="deep/nested/dir")
        written = write_dir_doc(doc, tmp_path)
        assert written.exists()
        assert (tmp_path / "deep" / "nested" / "dir" / "_dir.md").exists()

    def test_written_file_has_frontmatter(self, tmp_path: Path):
        doc = make_dir_doc()
        written = write_dir_doc(doc, tmp_path)
        content = written.read_text()
        assert content.startswith("---")
        assert "confidence:" in content
        assert "source:" in content
        assert "directory:" in content

    def test_written_file_contains_markdown_sections(self, tmp_path: Path):
        doc = make_dir_doc()
        written = write_dir_doc(doc, tmp_path)
        content = written.read_text()
        assert "## Summary" in content
        assert "## Key Responsibilities" in content
        assert "## Developer Hints" in content
        assert "## Child Docs" in content

    def test_last_analyzed_stored_as_isoformat_string(self, tmp_path: Path):
        doc = make_dir_doc()
        written = write_dir_doc(doc, tmp_path)
        content = written.read_text()
        assert _NOW.isoformat() in content

    def test_rejects_invalid_doc_never_writes(self, tmp_path: Path):
        """ValidationError raised before any disk write when confidence is invalid."""
        with pytest.raises(ValidationError):
            DirDoc(
                directory="src/lattice",
                confidence=1.5,
                source="agent",
                confidence_factors=["factors"],
                last_analyzed=_NOW,
            )
        # Confirm nothing was written
        assert not list(tmp_path.rglob("_dir.md"))
