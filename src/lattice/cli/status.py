"""Core logic for the map:status CLI command.

Reads the .agent-docs/ directory of a project and returns a status dict
describing pipeline progress, confidence distribution, and token cost.

Exports:
    _map_status_impl — main implementation, separated for testability.
"""
from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Zero-value templates
# ---------------------------------------------------------------------------

_ZERO_STATUS: dict = {
    "passes_complete": {
        "init": False,
        "gaps": False,
        "doc": False,
        "cross": False,
    },
    "directories_documented": 0,
    "confidence_distribution": {
        "low": 0,
        "medium": 0,
        "high": 0,
        "developer_verified": 0,
    },
    "active_run_id": None,
    "token_summary": {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_estimated_cost": 0.0,
    },
}


def _map_status_impl(target: Path) -> dict:
    """Return pipeline status for the project at *target*.

    Behaviour:
    - Cold start (no .agent-docs/): returns a zeroed status dict without error.
    - Never creates fleet.duckdb if it does not already exist (Pitfall 4).
    - Reads confidence distribution from _dir.md files via traverse().
    - Reads token summary from fleet.duckdb if present.

    Args:
        target: Path to the project root directory.

    Returns:
        Status dict with passes_complete, directories_documented,
        confidence_distribution, active_run_id, and token_summary.
    """
    agent_docs = target / ".agent-docs"

    if not agent_docs.exists():
        return _deep_copy_zero()

    # --- passes_complete ---------------------------------------------------
    passes_complete = {
        "init": (agent_docs / "_graph.json").exists(),
        "gaps": (agent_docs / "_test_coverage.json").exists(),
        "doc": any(agent_docs.rglob("_dir.md")),
        "cross": (agent_docs / "_project.md").exists(),
    }

    # --- confidence distribution -------------------------------------------
    # Buckets: "low": 0, "medium": 0, "high": 0, "developer_verified": 0
    confidence_distribution = {
        "low": 0,
        "medium": 0,
        "high": 0,
        "developer_verified": 0,
    }
    dir_docs = _load_dir_docs(agent_docs, target)
    directories_documented = len(dir_docs)

    for doc in dir_docs:
        confidence = doc.confidence
        source = doc.source
        if source == "developer" or confidence == 1.0:
            confidence_distribution["developer_verified"] += 1
        elif confidence >= 0.8:
            confidence_distribution["high"] += 1
        elif confidence >= 0.5:
            confidence_distribution["medium"] += 1
        else:
            confidence_distribution["low"] += 1

    # --- token summary (only if fleet.duckdb already exists) ---------------
    db_path = agent_docs / "fleet.duckdb"
    token_summary, active_run_id = _read_fleet_data(db_path)

    # --- queue status (reads _queue.json, counts pending entries and stale docs) ---
    from lattice.cli.queue import _read_queue
    queue_path = agent_docs / "_queue.json"
    queue_data = _read_queue(queue_path)
    pending_entries = [e for e in queue_data.get("entries", []) if e.get("status") == "pending"]
    stale_docs = [d for d in dir_docs if d.stale]
    queue_status = {
        "pending_count": len(pending_entries),
        "stale_count": len(stale_docs),
        "pending_entries": pending_entries,
        "stale_directories": [d.directory for d in stale_docs],
    }

    return {
        "passes_complete": passes_complete,
        "directories_documented": directories_documented,
        "confidence_distribution": confidence_distribution,
        "active_run_id": active_run_id,
        "token_summary": token_summary,
        "queue_status": queue_status,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _deep_copy_zero() -> dict:
    """Return a fresh copy of the zero-value status dict."""
    return {
        "passes_complete": {
            "init": False,
            "gaps": False,
            "doc": False,
            "cross": False,
        },
        "directories_documented": 0,
        "confidence_distribution": {
            "low": 0,
            "medium": 0,
            "high": 0,
            "developer_verified": 0,
        },
        "active_run_id": None,
        "token_summary": {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_estimated_cost": 0.0,
        },
        "queue_status": {
            "pending_count": 0,
            "stale_count": 0,
            "pending_entries": [],
            "stale_directories": [],
        },
    }


def _load_dir_docs(agent_docs: Path, project_root: Path) -> list:
    """Load all DirDoc objects, skipping corrupt files."""
    from lattice.shadow import traverse

    try:
        return traverse(agent_docs, project_root)
    except Exception as exc:
        log.warning("failed to traverse shadow tree", error=str(exc))
        return []


def _read_fleet_data(db_path: Path) -> tuple[dict, str | None]:
    """Read token summary and active run_id from fleet.duckdb.

    CRITICAL: checks db_path.exists() BEFORE connecting (Pitfall 4).
    Never creates the database file.

    Args:
        db_path: Path to the fleet.duckdb file.

    Returns:
        Tuple of (token_summary dict, active_run_id or None).
    """
    zero_token_summary = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_estimated_cost": 0.0,
    }

    if not db_path.exists():
        return zero_token_summary, None

    try:
        import duckdb
        from lattice.fleet.checkpoint import FleetCheckpoint

        conn = duckdb.connect(str(db_path), read_only=True)

        # Aggregate all token usage across all runs
        token_row = conn.execute("""
            SELECT
                COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(output_tokens), 0),
                COALESCE(SUM(estimated_cost_usd), 0.0)
            FROM fleet_token_usage
        """).fetchone()

        # Active run = most recent row with status pending or partial
        active_row = conn.execute("""
            SELECT run_id
            FROM fleet_waves
            WHERE status IN ('pending', 'partial')
            ORDER BY started_at DESC NULLS LAST
            LIMIT 1
        """).fetchone()

        conn.close()

        token_summary = {
            "total_input_tokens": int(token_row[0]) if token_row else 0,
            "total_output_tokens": int(token_row[1]) if token_row else 0,
            "total_estimated_cost": float(token_row[2]) if token_row else 0.0,
        }
        active_run_id = active_row[0] if active_row else None

        return token_summary, active_run_id

    except Exception as exc:
        log.warning("failed to read fleet.duckdb", error=str(exc))
        return zero_token_summary, None
