"""Tests for DirDoc integration_points field, writer rendering, and reader backward compat."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lattice.shadow.schema import DirDoc
from lattice.shadow.writer import write_dir_doc, _doc_to_frontmatter_dict
from lattice.shadow.reader import parse_dir_doc

_NOW = datetime.now(timezone.utc)


def make_dir_doc(**kwargs) -> DirDoc:
    defaults = {
        "directory": "src/auth",
        "confidence": 0.8,
        "source": "agent",
        "confidence_factors": ["imports resolved"],
        "last_analyzed": _NOW,
    }
    defaults.update(kwargs)
    return DirDoc(**defaults)


class TestIntegrationPointsField:
    def test_integration_points_field_default(self):
        """DirDoc with no integration_points has empty list."""
        doc = make_dir_doc()
        assert doc.integration_points == []

    def test_integration_points_field_with_data(self):
        """DirDoc accepts integration_points with edge dicts."""
        points = [
            {
                "edge": "src/auth -> src/db",
                "status": "TESTED",
                "test_file": "test_auth.py",
            }
        ]
        doc = make_dir_doc(integration_points=points)
        assert doc.integration_points == points

    def test_model_copy_preserves_integration_points(self):
        """model_copy(update={"stale": True}) preserves existing integration_points."""
        points = [{"edge": "src/auth -> src/db", "status": "TESTED", "test_file": "test_auth.py"}]
        doc = make_dir_doc(integration_points=points)
        updated = doc.model_copy(update={"stale": True})
        assert updated.stale is True
        assert updated.integration_points == points


class TestWriterRendersIntegrationPoints:
    def test_writer_renders_integration_points_tested(self, tmp_path: Path):
        """write_dir_doc with TESTED integration point produces correct markdown."""
        points = [{"edge": "src/auth -> src/db", "status": "TESTED", "test_file": "test_auth.py"}]
        doc = make_dir_doc(integration_points=points)
        written = write_dir_doc(doc, tmp_path)
        content = written.read_text()
        assert "## Integration Points" in content
        assert "- [TESTED] `src/auth -> src/db` (test_auth.py)" in content

    def test_writer_renders_integration_points_untested(self, tmp_path: Path):
        """write_dir_doc with UNTESTED integration point (no test_file) renders correctly."""
        points = [{"edge": "src/api -> src/cache", "status": "UNTESTED", "test_file": None}]
        doc = make_dir_doc(integration_points=points)
        written = write_dir_doc(doc, tmp_path)
        content = written.read_text()
        assert "## Integration Points" in content
        assert "- [UNTESTED] `src/api -> src/cache`" in content

    def test_writer_skips_empty_integration_points(self, tmp_path: Path):
        """write_dir_doc with empty integration_points does NOT produce Integration Points header."""
        doc = make_dir_doc()
        written = write_dir_doc(doc, tmp_path)
        content = written.read_text()
        assert "## Integration Points" not in content

    def test_frontmatter_includes_integration_points(self):
        """_doc_to_frontmatter_dict includes integration_points only when non-empty."""
        points = [{"edge": "src/auth -> src/db", "status": "TESTED", "test_file": "test_auth.py"}]
        doc_with = make_dir_doc(integration_points=points)
        doc_without = make_dir_doc()

        fm_with = _doc_to_frontmatter_dict(doc_with)
        fm_without = _doc_to_frontmatter_dict(doc_without)

        assert "integration_points" in fm_with
        assert fm_with["integration_points"] == points
        assert "integration_points" not in fm_without


class TestReaderBackwardCompat:
    def test_backward_compat_empty_list(self, tmp_path: Path):
        """parse_dir_doc on a _dir.md WITHOUT integration_points returns DirDoc with []."""
        # Write a _dir.md manually without integration_points in frontmatter
        dir_path = tmp_path / "src" / "auth"
        dir_path.mkdir(parents=True)
        dir_md = dir_path / "_dir.md"
        dir_md.write_text(
            "---\n"
            "directory: src/auth\n"
            "confidence: 0.8\n"
            "source: agent\n"
            "confidence_factors:\n"
            "  - imports resolved\n"
            "stale: false\n"
            f"last_analyzed: {_NOW.isoformat()}\n"
            "static_analysis_limits:\n"
            "  dynamic_imports: 0\n"
            "  unresolved_paths: 0\n"
            "gap_summary:\n"
            "  untested_edges: 0\n"
            "  top_gaps: []\n"
            "---\n"
            "\n"
            "## Summary\n"
            "\n"
            "\n"
            "\n"
            "## Key Responsibilities\n"
            "\n"
            "\n"
            "\n"
            "## Developer Hints\n"
            "\n"
            "\n"
            "\n"
            "## Child Docs\n"
            "\n"
            "\n",
            encoding="utf-8",
        )
        doc = parse_dir_doc(dir_md)
        assert doc.integration_points == []

    def test_reader_parses_integration_points_from_frontmatter(self, tmp_path: Path):
        """parse_dir_doc reads integration_points from YAML frontmatter correctly."""
        points = [{"edge": "src/auth -> src/db", "status": "TESTED", "test_file": "test_auth.py"}]
        doc = make_dir_doc(integration_points=points)
        written = write_dir_doc(doc, tmp_path)
        parsed = parse_dir_doc(written)
        assert parsed.integration_points == points

    def test_reader_round_trip_preserves_multiple_points(self, tmp_path: Path):
        """Write/read round-trip preserves multiple integration_points entries."""
        points = [
            {"edge": "src/auth -> src/db", "status": "TESTED", "test_file": "test_auth.py"},
            {"edge": "src/auth -> src/cache", "status": "UNTESTED", "test_file": None},
        ]
        doc = make_dir_doc(integration_points=points)
        written = write_dir_doc(doc, tmp_path)
        parsed = parse_dir_doc(written)
        assert len(parsed.integration_points) == 2
        # TESTED entry preserved
        assert parsed.integration_points[0]["status"] == "TESTED"
        # UNTESTED entry preserved
        assert parsed.integration_points[1]["status"] == "UNTESTED"
