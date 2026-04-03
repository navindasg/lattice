"""Fleet checkpoint tables for wave progress and token tracking.

Creates custom DuckDB tables alongside LangGraph checkpoint tables on the
same connection (no second connection per Pitfall 7 from RESEARCH.md).

Tables:
    fleet_waves       — wave progress (pending/complete/partial)
    fleet_token_usage — per-directory actual token usage and cost

Public API:
    FleetCheckpoint — checkpoint manager class
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import structlog

log = structlog.get_logger(__name__)

_CREATE_WAVES_TABLE = """
    CREATE TABLE IF NOT EXISTS fleet_waves (
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
"""

_CREATE_TOKEN_TABLE = """
    CREATE TABLE IF NOT EXISTS fleet_token_usage (
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
"""


class FleetCheckpoint:
    """Manages wave progress and token tracking in DuckDB.

    Uses the same DuckDB connection as the LangGraph DuckDBSaver checkpointer
    to avoid file-locking issues (Pitfall 7).

    Args:
        conn: An open duckdb.DuckDBPyConnection instance.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self._create_tables()

    def _create_tables(self) -> None:
        """Create fleet tables idempotently (safe to call multiple times)."""
        self._conn.execute(_CREATE_WAVES_TABLE)
        self._conn.execute(_CREATE_TOKEN_TABLE)

    def record_wave_start(
        self,
        run_id: str,
        wave_index: int,
        total_dirs: int,
    ) -> None:
        """Insert a pending wave record.

        Args:
            run_id: Stable run identifier.
            wave_index: Zero-based wave index.
            total_dirs: Total number of directories in this wave.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO fleet_waves
                (run_id, wave_index, status, completed_dirs, failed_dirs, total_dirs, started_at)
            VALUES (?, ?, 'pending', 0, 0, ?, ?)
            """,
            [run_id, wave_index, total_dirs, now],
        )

    def record_wave_complete(
        self,
        run_id: str,
        wave_index: int,
        completed_dirs: int,
        failed_dirs: int,
    ) -> None:
        """Update wave status to 'complete' (all succeeded) or 'partial' (some failed).

        Args:
            run_id: Stable run identifier.
            wave_index: Zero-based wave index.
            completed_dirs: Number of successfully completed directories.
            failed_dirs: Number of failed directories.
        """
        status = "complete" if failed_dirs == 0 else "partial"
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE fleet_waves
            SET status = ?,
                completed_dirs = ?,
                failed_dirs = ?,
                completed_at = ?
            WHERE run_id = ? AND wave_index = ?
            """,
            [status, completed_dirs, failed_dirs, now, run_id, wave_index],
        )

    def record_token_usage(
        self,
        run_id: str,
        wave_index: int,
        directory: str,
        tier: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
    ) -> None:
        """Insert a per-directory token usage record.

        Args:
            run_id: Stable run identifier.
            wave_index: Zero-based wave index this directory belonged to.
            directory: Relative directory path.
            tier: Model tier used ('silver' or 'bronze').
            input_tokens: Actual input tokens consumed.
            output_tokens: Actual output tokens consumed.
            estimated_cost_usd: Estimated cost in USD.
        """
        self._conn.execute(
            """
            INSERT OR REPLACE INTO fleet_token_usage
                (run_id, wave_index, directory, tier, input_tokens, output_tokens, estimated_cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [run_id, wave_index, directory, tier, input_tokens, output_tokens, estimated_cost_usd],
        )

    def get_completed_waves(self, run_id: str) -> list[int]:
        """Return wave indices with status='complete' for this run.

        Only fully-complete waves are returned. Partial waves (some failures)
        are not included so they can be retried.

        Args:
            run_id: Stable run identifier.

        Returns:
            Sorted list of completed wave indices.
        """
        rows = self._conn.execute(
            """
            SELECT wave_index
            FROM fleet_waves
            WHERE run_id = ? AND status = 'complete'
            ORDER BY wave_index
            """,
            [run_id],
        ).fetchall()
        return [row[0] for row in rows]

    def get_run_summary(self, run_id: str) -> dict:
        """Return aggregated run statistics for display.

        Args:
            run_id: Stable run identifier.

        Returns:
            Dict with total_input_tokens, total_output_tokens, total_estimated_cost,
            waves_complete, waves_partial, waves_pending.
        """
        token_row = self._conn.execute(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0) AS total_input,
                COALESCE(SUM(output_tokens), 0) AS total_output,
                COALESCE(SUM(estimated_cost_usd), 0.0) AS total_cost
            FROM fleet_token_usage
            WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()

        wave_rows = self._conn.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM fleet_waves
            WHERE run_id = ?
            GROUP BY status
            """,
            [run_id],
        ).fetchall()

        wave_counts: dict[str, int] = {"complete": 0, "partial": 0, "pending": 0}
        for status, cnt in wave_rows:
            wave_counts[status] = cnt

        return {
            "total_input_tokens": int(token_row[0]) if token_row else 0,
            "total_output_tokens": int(token_row[1]) if token_row else 0,
            "total_estimated_cost": float(token_row[2]) if token_row else 0.0,
            "waves_complete": wave_counts["complete"],
            "waves_partial": wave_counts["partial"],
            "waves_pending": wave_counts["pending"],
        }
