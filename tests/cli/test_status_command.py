"""Tests for the map:status CLI command core logic."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lattice.cli.status import _map_status_impl


def _make_dir_md(
    directory: str,
    confidence: float,
    source: str = "agent",
    confidence_factors: list[str] | None = None,
) -> str:
    """Build a minimal _dir.md file string."""
    factors = confidence_factors or ["factor one", "factor two"]
    factors_yaml = "\n".join(f"  - {f}" for f in factors)
    now = datetime.now(timezone.utc).isoformat()
    return f"""---
directory: {directory}
confidence: {confidence}
source: {source}
confidence_factors:
{factors_yaml}
stale: false
last_analyzed: "{now}"
static_analysis_limits:
  dynamic_imports: 0
  unresolved_paths: 0
gap_summary:
  untested_edges: 0
  top_gaps: []
---

## Summary

Test directory.

## Key Responsibilities

- does things

## Developer Hints

## Child Docs
"""


class TestMapStatusImpl:
    """Tests for _map_status_impl core logic."""

    def test_cold_start_no_agent_docs(self, tmp_path: Path) -> None:
        """Cold start with no .agent-docs/ returns zeroed dict without error."""
        result = _map_status_impl(tmp_path)

        assert result["passes_complete"]["init"] is False
        assert result["passes_complete"]["gaps"] is False
        assert result["passes_complete"]["doc"] is False
        assert result["passes_complete"]["cross"] is False
        assert result["directories_documented"] == 0
        assert result["confidence_distribution"]["low"] == 0
        assert result["confidence_distribution"]["medium"] == 0
        assert result["confidence_distribution"]["high"] == 0
        assert result["confidence_distribution"]["developer_verified"] == 0
        assert result["active_run_id"] is None
        assert result["token_summary"]["total_input_tokens"] == 0
        assert result["token_summary"]["total_output_tokens"] == 0
        assert result["token_summary"]["total_estimated_cost"] == 0.0

    def test_init_pass_detected(self, tmp_path: Path) -> None:
        """passes_complete['init'] is True when _graph.json exists."""
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir()
        (agent_docs / "_graph.json").write_text("{}", encoding="utf-8")

        result = _map_status_impl(tmp_path)

        assert result["passes_complete"]["init"] is True
        assert result["passes_complete"]["gaps"] is False
        assert result["passes_complete"]["doc"] is False
        assert result["passes_complete"]["cross"] is False

    def test_gaps_pass_detected(self, tmp_path: Path) -> None:
        """passes_complete['gaps'] is True when _test_coverage.json exists."""
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir()
        (agent_docs / "_graph.json").write_text("{}", encoding="utf-8")
        (agent_docs / "_test_coverage.json").write_text("{}", encoding="utf-8")

        result = _map_status_impl(tmp_path)

        assert result["passes_complete"]["gaps"] is True

    def test_doc_pass_detected(self, tmp_path: Path) -> None:
        """passes_complete['doc'] is True when any _dir.md file exists."""
        agent_docs = tmp_path / ".agent-docs"
        subdir = agent_docs / "src"
        subdir.mkdir(parents=True)
        (subdir / "_dir.md").write_text("---\n---\n", encoding="utf-8")

        result = _map_status_impl(tmp_path)

        assert result["passes_complete"]["doc"] is True

    def test_cross_pass_detected(self, tmp_path: Path) -> None:
        """passes_complete['cross'] is True when _project.md exists."""
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir()
        (agent_docs / "_project.md").write_text("---\n---\n", encoding="utf-8")

        result = _map_status_impl(tmp_path)

        assert result["passes_complete"]["cross"] is True

    def test_confidence_buckets(self, tmp_path: Path) -> None:
        """Confidence distribution bucketing is correct."""
        agent_docs = tmp_path / ".agent-docs"

        # Create subdirectories with _dir.md files at different confidence levels
        for i, (dirname, confidence, source) in enumerate([
            ("src/low", 0.3, "agent"),          # low: < 0.5
            ("src/medium", 0.6, "agent"),         # medium: 0.5 <= c < 0.8
            ("src/high", 0.9, "agent"),           # high: 0.8 <= c < 1.0
            ("src/verified", 1.0, "developer"),   # developer_verified: c==1.0 or source=="developer"
        ]):
            subdir = agent_docs / dirname
            subdir.mkdir(parents=True)
            factors = ["f1", "f2"] if source == "agent" else []
            (subdir / "_dir.md").write_text(
                _make_dir_md(dirname, confidence, source, factors or None),
                encoding="utf-8",
            )

        result = _map_status_impl(tmp_path)

        dist = result["confidence_distribution"]
        assert dist["low"] == 1, f"Expected 1 low, got {dist['low']}"
        assert dist["medium"] == 1, f"Expected 1 medium, got {dist['medium']}"
        assert dist["high"] == 1, f"Expected 1 high, got {dist['high']}"
        assert dist["developer_verified"] == 1, f"Expected 1 developer_verified, got {dist['developer_verified']}"

    def test_directories_documented_count(self, tmp_path: Path) -> None:
        """directories_documented equals number of _dir.md files found."""
        agent_docs = tmp_path / ".agent-docs"
        for dirname in ("src/a", "src/b", "src/c"):
            subdir = agent_docs / dirname
            subdir.mkdir(parents=True)
            (subdir / "_dir.md").write_text(
                _make_dir_md(dirname, 0.7),
                encoding="utf-8",
            )

        result = _map_status_impl(tmp_path)

        assert result["directories_documented"] == 3

    def test_no_fleet_duckdb_created(self, tmp_path: Path) -> None:
        """Calling _map_status_impl on a cold dir does NOT create fleet.duckdb."""
        result = _map_status_impl(tmp_path)

        duckdb_path = tmp_path / ".agent-docs" / "fleet.duckdb"
        assert not duckdb_path.exists(), "fleet.duckdb must not be created by map:status"

    def test_no_fleet_duckdb_created_with_agent_docs(self, tmp_path: Path) -> None:
        """Even with .agent-docs/ present but no fleet.duckdb, none is created."""
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir()
        (agent_docs / "_graph.json").write_text("{}", encoding="utf-8")

        _map_status_impl(tmp_path)

        duckdb_path = agent_docs / "fleet.duckdb"
        assert not duckdb_path.exists(), "fleet.duckdb must not be created by map:status"

    def test_token_summary_from_duckdb(self, tmp_path: Path) -> None:
        """Token summary reflects data in existing fleet.duckdb."""
        import duckdb

        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir()
        db_path = agent_docs / "fleet.duckdb"

        # Seed database with token usage rows
        conn = duckdb.connect(str(db_path))
        conn.execute("""
            CREATE TABLE fleet_token_usage (
                run_id TEXT NOT NULL,
                wave_index INTEGER NOT NULL,
                directory TEXT NOT NULL,
                tier TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                estimated_cost_usd REAL DEFAULT 0.0,
                recorded_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (run_id, directory)
            )
        """)
        conn.execute("""
            CREATE TABLE fleet_waves (
                run_id TEXT NOT NULL,
                wave_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                completed_dirs INTEGER DEFAULT 0,
                failed_dirs INTEGER DEFAULT 0,
                total_dirs INTEGER NOT NULL,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                PRIMARY KEY (run_id, wave_index)
            )
        """)
        run_id = "test-run-001"
        conn.execute(
            "INSERT INTO fleet_token_usage VALUES (?, 0, 'src/a', 'bronze', 1000, 500, 0.003, NOW())",
            [run_id],
        )
        conn.execute(
            "INSERT INTO fleet_token_usage VALUES (?, 0, 'src/b', 'bronze', 2000, 800, 0.006, NOW())",
            [run_id],
        )
        conn.execute(
            "INSERT INTO fleet_waves VALUES (?, 0, 'complete', 2, 0, 2, NOW(), NOW())",
            [run_id],
        )
        conn.close()

        result = _map_status_impl(tmp_path)

        assert result["token_summary"]["total_input_tokens"] == 3000
        assert result["token_summary"]["total_output_tokens"] == 1300
        assert result["token_summary"]["total_estimated_cost"] == pytest.approx(0.009)

    def test_active_run_id_from_duckdb(self, tmp_path: Path) -> None:
        """active_run_id reflects the most recent pending run in fleet.duckdb."""
        import duckdb

        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir()
        db_path = agent_docs / "fleet.duckdb"

        conn = duckdb.connect(str(db_path))
        conn.execute("""
            CREATE TABLE fleet_token_usage (
                run_id TEXT NOT NULL,
                wave_index INTEGER NOT NULL,
                directory TEXT NOT NULL,
                tier TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                estimated_cost_usd REAL DEFAULT 0.0,
                recorded_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (run_id, directory)
            )
        """)
        conn.execute("""
            CREATE TABLE fleet_waves (
                run_id TEXT NOT NULL,
                wave_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                completed_dirs INTEGER DEFAULT 0,
                failed_dirs INTEGER DEFAULT 0,
                total_dirs INTEGER NOT NULL,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                PRIMARY KEY (run_id, wave_index)
            )
        """)
        run_id = "pending-run-999"
        conn.execute(
            "INSERT INTO fleet_waves VALUES (?, 0, 'pending', 0, 0, 5, NOW(), NULL)",
            [run_id],
        )
        conn.close()

        result = _map_status_impl(tmp_path)

        assert result["active_run_id"] == run_id
