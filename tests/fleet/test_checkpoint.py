"""Tests for FleetCheckpoint DuckDB wave progress and token tracking tables.

TDD RED phase: tests are written before full implementation verification.
"""
from __future__ import annotations

import duckdb
import pytest

from lattice.fleet.checkpoint import FleetCheckpoint


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    """In-memory DuckDB connection for test isolation."""
    return duckdb.connect(":memory:")


@pytest.fixture()
def checkpoint(conn):
    """FleetCheckpoint with in-memory DuckDB."""
    return FleetCheckpoint(conn)


# ---------------------------------------------------------------------------
# Task 2 Tests
# ---------------------------------------------------------------------------


def test_tables_created_idempotently(conn):
    """Calling FleetCheckpoint constructor twice doesn't error."""
    fc1 = FleetCheckpoint(conn)
    fc2 = FleetCheckpoint(conn)  # Second call — idempotent
    # Both should succeed; verify tables exist
    tables = conn.execute("SHOW TABLES").fetchall()
    table_names = {row[0] for row in tables}
    assert "fleet_waves" in table_names
    assert "fleet_token_usage" in table_names


def test_record_wave_start_inserts_pending_record(checkpoint, conn):
    """record_wave_start inserts a pending wave record."""
    checkpoint.record_wave_start(run_id="run-001", wave_index=0, total_dirs=5)

    rows = conn.execute(
        "SELECT run_id, wave_index, status, total_dirs FROM fleet_waves WHERE run_id = ?",
        ["run-001"],
    ).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "run-001"
    assert row[1] == 0
    assert row[2] == "pending"
    assert row[3] == 5


def test_record_wave_complete_updates_status_to_complete(checkpoint, conn):
    """record_wave_start + record_wave_complete round-trip updates status."""
    checkpoint.record_wave_start(run_id="run-002", wave_index=1, total_dirs=3)
    checkpoint.record_wave_complete(
        run_id="run-002", wave_index=1, completed_dirs=3, failed_dirs=0
    )

    row = conn.execute(
        "SELECT status, completed_dirs, failed_dirs FROM fleet_waves WHERE run_id = ? AND wave_index = ?",
        ["run-002", 1],
    ).fetchone()

    assert row is not None
    assert row[0] == "complete"
    assert row[1] == 3
    assert row[2] == 0


def test_record_wave_complete_status_partial_when_failures(checkpoint, conn):
    """record_wave_complete sets status to 'partial' when some dirs failed."""
    checkpoint.record_wave_start(run_id="run-003", wave_index=0, total_dirs=4)
    checkpoint.record_wave_complete(
        run_id="run-003", wave_index=0, completed_dirs=3, failed_dirs=1
    )

    row = conn.execute(
        "SELECT status, failed_dirs FROM fleet_waves WHERE run_id = ? AND wave_index = ?",
        ["run-003", 0],
    ).fetchone()

    assert row is not None
    assert row[0] == "partial"
    assert row[1] == 1


def test_get_completed_waves_returns_only_complete_indices(checkpoint):
    """get_completed_waves returns only wave indices with status='complete'."""
    checkpoint.record_wave_start(run_id="run-004", wave_index=0, total_dirs=2)
    checkpoint.record_wave_complete(run_id="run-004", wave_index=0, completed_dirs=2, failed_dirs=0)

    checkpoint.record_wave_start(run_id="run-004", wave_index=1, total_dirs=3)
    checkpoint.record_wave_complete(run_id="run-004", wave_index=1, completed_dirs=2, failed_dirs=1)

    checkpoint.record_wave_start(run_id="run-004", wave_index=2, total_dirs=1)
    # Wave 2 left as 'pending' — not completed yet

    completed = checkpoint.get_completed_waves("run-004")

    assert completed == [0]  # Only wave 0 is 'complete'; wave 1 is 'partial', wave 2 'pending'


def test_record_token_usage_stores_correctly(checkpoint, conn):
    """record_token_usage stores per-directory token data."""
    checkpoint.record_token_usage(
        run_id="run-005",
        wave_index=0,
        directory="src/auth",
        tier="silver",
        input_tokens=500,
        output_tokens=200,
        estimated_cost_usd=0.015,
    )

    row = conn.execute(
        """SELECT wave_index, directory, tier, input_tokens, output_tokens, estimated_cost_usd
           FROM fleet_token_usage WHERE run_id = ? AND directory = ?""",
        ["run-005", "src/auth"],
    ).fetchone()

    assert row is not None
    assert row[0] == 0  # wave_index
    assert row[1] == "src/auth"
    assert row[2] == "silver"
    assert row[3] == 500
    assert row[4] == 200
    assert abs(row[5] - 0.015) < 1e-6


def test_get_run_summary_aggregates_tokens_and_cost(checkpoint):
    """get_run_summary returns aggregated token totals and wave status counts."""
    checkpoint.record_wave_start(run_id="run-006", wave_index=0, total_dirs=2)
    checkpoint.record_wave_complete(run_id="run-006", wave_index=0, completed_dirs=2, failed_dirs=0)

    checkpoint.record_wave_start(run_id="run-006", wave_index=1, total_dirs=1)
    checkpoint.record_wave_complete(run_id="run-006", wave_index=1, completed_dirs=0, failed_dirs=1)

    checkpoint.record_token_usage(
        run_id="run-006",
        wave_index=0,
        directory="src/auth",
        tier="silver",
        input_tokens=400,
        output_tokens=150,
        estimated_cost_usd=0.01,
    )
    checkpoint.record_token_usage(
        run_id="run-006",
        wave_index=0,
        directory="src/utils",
        tier="silver",
        input_tokens=300,
        output_tokens=100,
        estimated_cost_usd=0.008,
    )

    summary = checkpoint.get_run_summary("run-006")

    assert summary["total_input_tokens"] == 700
    assert summary["total_output_tokens"] == 250
    assert abs(summary["total_estimated_cost"] - 0.018) < 1e-6
    assert summary["waves_complete"] == 1
    assert summary["waves_partial"] == 1
    assert summary["waves_pending"] == 0


def test_resume_scenario_completed_waves_returns_correct_indices(checkpoint):
    """Resume scenario: record waves 0 and 1 as complete, verify get_completed_waves returns [0, 1]."""
    for wave_idx in range(3):
        checkpoint.record_wave_start(run_id="run-007", wave_index=wave_idx, total_dirs=2)

    # Complete waves 0 and 1
    checkpoint.record_wave_complete(run_id="run-007", wave_index=0, completed_dirs=2, failed_dirs=0)
    checkpoint.record_wave_complete(run_id="run-007", wave_index=1, completed_dirs=2, failed_dirs=0)
    # Wave 2 remains pending

    completed = checkpoint.get_completed_waves("run-007")
    assert completed == [0, 1]


def test_get_run_summary_empty_run(checkpoint):
    """get_run_summary returns zero values for an unknown run_id."""
    summary = checkpoint.get_run_summary("nonexistent-run")

    assert summary["total_input_tokens"] == 0
    assert summary["total_output_tokens"] == 0
    assert summary["total_estimated_cost"] == 0.0
    assert summary["waves_complete"] == 0
    assert summary["waves_partial"] == 0
    assert summary["waves_pending"] == 0


def test_get_completed_waves_different_run_ids_isolated(checkpoint):
    """Completed waves from one run_id don't appear in another run_id's results."""
    checkpoint.record_wave_start(run_id="run-A", wave_index=0, total_dirs=2)
    checkpoint.record_wave_complete(run_id="run-A", wave_index=0, completed_dirs=2, failed_dirs=0)

    checkpoint.record_wave_start(run_id="run-B", wave_index=0, total_dirs=1)
    # run-B wave 0 is still pending

    completed_a = checkpoint.get_completed_waves("run-A")
    completed_b = checkpoint.get_completed_waves("run-B")

    assert completed_a == [0]
    assert completed_b == []
