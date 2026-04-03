"""Tests for the `lattice map:doc` CLI command.

Tests use Click's CliRunner to invoke the command without spawning a subprocess.
LLM calls in FleetDispatcher are mocked to avoid real API calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from lattice.cli.commands import cli


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_minimal_graph_json(tmp_path: Path) -> Path:
    """Write a minimal _graph.json to a tmp .agent-docs directory.

    Creates a simple file graph with 2 nodes and 1 edge so that plan_waves
    produces a valid WavePlan with at least 1 wave.
    """
    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir(parents=True, exist_ok=True)
    graph_path = agent_docs / "_graph.json"

    graph_data = {
        "metadata": {
            "file_count": 2,
            "languages": {"python": 2},
            "blind_spots": [],
        },
        "nodes": [
            {"id": "src/auth/session.py", "language": "python", "is_entry_point": False},
            {"id": "src/models/user.py", "language": "python", "is_entry_point": False},
        ],
        "edges": [
            {"source": "src/auth/session.py", "target": "src/models/user.py", "import_type": "standard"},
        ],
    }
    graph_path.write_text(json.dumps(graph_data, indent=2), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Task 2 Tests
# ---------------------------------------------------------------------------


def test_map_doc_missing_graph_json_prints_helpful_error(tmp_path: Path) -> None:
    """map:doc with no _graph.json prints error about running map:init first."""
    runner = CliRunner()

    result = runner.invoke(cli, ["map:doc", str(tmp_path)])

    assert result.exit_code != 0
    assert "map:init" in result.output


def test_map_doc_prints_wave_plan_before_dispatching(tmp_path: Path) -> None:
    """map:doc prints wave plan before any LLM calls are made."""
    target = _make_minimal_graph_json(tmp_path)
    runner = CliRunner()

    # Mock the FleetDispatcher so no real LLM calls happen
    mock_results = []

    with patch("lattice.cli.commands.FleetDispatcher") as MockDispatcher:
        mock_dispatcher_instance = MagicMock()
        mock_dispatcher_instance.dispatch_wave = AsyncMock(return_value=mock_results)
        MockDispatcher.return_value = mock_dispatcher_instance

        result = runner.invoke(cli, ["map:doc", str(target)])

    # Wave plan must appear in output (even if dispatch returns empty)
    assert "Wave Plan" in result.output or "wave" in result.output.lower()
    assert result.exit_code == 0


def test_map_doc_tier_silver_accepted(tmp_path: Path) -> None:
    """--tier silver is a valid option."""
    target = _make_minimal_graph_json(tmp_path)
    runner = CliRunner()

    with patch("lattice.cli.commands.FleetDispatcher") as MockDispatcher:
        mock_instance = MagicMock()
        mock_instance.dispatch_wave = AsyncMock(return_value=[])
        MockDispatcher.return_value = mock_instance

        result = runner.invoke(cli, ["map:doc", str(target), "--tier", "silver"])

    assert result.exit_code == 0


def test_map_doc_tier_bronze_accepted(tmp_path: Path) -> None:
    """--tier bronze is a valid option."""
    target = _make_minimal_graph_json(tmp_path)
    runner = CliRunner()

    with patch("lattice.cli.commands.FleetDispatcher") as MockDispatcher:
        mock_instance = MagicMock()
        mock_instance.dispatch_wave = AsyncMock(return_value=[])
        MockDispatcher.return_value = mock_instance

        result = runner.invoke(cli, ["map:doc", str(target), "--tier", "bronze"])

    assert result.exit_code == 0


def test_map_doc_tier_gold_rejected(tmp_path: Path) -> None:
    """--tier gold is NOT a valid option (only silver and bronze per user decision)."""
    target = _make_minimal_graph_json(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["map:doc", str(target), "--tier", "gold"])

    assert result.exit_code != 0
    assert "gold" in result.output.lower() or "invalid" in result.output.lower()


def test_map_doc_resume_flag_passes_run_id(tmp_path: Path) -> None:
    """--resume passes the provided run_id to FleetCheckpoint for resume logic."""
    target = _make_minimal_graph_json(tmp_path)
    runner = CliRunner()

    resume_run_id = "test-resume-run-abc123"
    captured_run_ids: list[str] = []

    mock_summary = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_estimated_cost": 0.0,
        "waves_complete": 0,
        "waves_partial": 0,
        "waves_pending": 0,
    }

    with patch("lattice.cli.commands.FleetDispatcher") as MockDispatcher:
        mock_instance = MagicMock()
        mock_instance.dispatch_wave = AsyncMock(return_value=[])
        MockDispatcher.return_value = mock_instance

        with patch("lattice.cli.commands.FleetCheckpoint") as MockCheckpoint:
            mock_cp = MagicMock()
            mock_cp.get_completed_waves.side_effect = (
                lambda run_id: captured_run_ids.append(run_id) or []
            )
            mock_cp.get_run_summary.return_value = mock_summary
            MockCheckpoint.return_value = mock_cp

            with patch("lattice.cli.commands.duckdb") as mock_duckdb:
                mock_conn = MagicMock()
                mock_duckdb.connect.return_value = mock_conn

                result = runner.invoke(
                    cli,
                    ["map:doc", str(target), "--resume", resume_run_id],
                )

    assert result.exit_code == 0
    # The resume run_id must have been queried from the checkpoint
    assert resume_run_id in captured_run_ids
