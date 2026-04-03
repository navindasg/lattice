"""Query functions for orchestrator instance and connector status.

Reads from the context_utilization and connector_registry DuckDB tables.
Separated from CLI for testability.

Exports:
    get_instance_status     — Return utilization status for a single instance.
    get_all_instance_status — Return utilization status for all tracked instances.
    get_connector_status    — Return connector registry status rows.
"""
from __future__ import annotations

from typing import Any

import duckdb
import structlog

log = structlog.get_logger(__name__)


def get_instance_status(conn: duckdb.DuckDBPyConnection, instance_id: str) -> dict[str, Any]:
    """Return utilization status for a single instance.

    Queries the context_utilization table for the given instance_id.
    Returns a zero-value dict if the instance has no utilization data or
    the table does not exist yet (ContextManager not initialized).

    Args:
        conn: Open DuckDB connection.
        instance_id: The CC instance identifier to query.

    Returns:
        Dict with keys: instance_id, bytes_sent, bytes_received,
        utilization_pct, compaction_count, last_updated.
        All numeric fields are 0 / 0.0 and last_updated is None when
        no data exists for the instance.
    """
    try:
        row = conn.execute(
            "SELECT instance_id, bytes_sent, bytes_received, utilization_pct, "
            "compaction_count, last_updated "
            "FROM context_utilization WHERE instance_id = ?",
            [instance_id],
        ).fetchone()
    except duckdb.CatalogException:
        row = None

    if row is None:
        return {
            "instance_id": instance_id,
            "bytes_sent": 0,
            "bytes_received": 0,
            "utilization_pct": 0.0,
            "compaction_count": 0,
            "last_updated": None,
        }

    return {
        "instance_id": row[0],
        "bytes_sent": int(row[1]),
        "bytes_received": int(row[2]),
        "utilization_pct": float(row[3]),
        "compaction_count": int(row[4]),
        "last_updated": row[5],
    }


def get_all_instance_status(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Return utilization status for all tracked instances.

    Queries the context_utilization table and returns rows sorted by
    utilization_pct descending so the highest-utilization instances
    appear first.

    Args:
        conn: Open DuckDB connection.

    Returns:
        List of dicts (same schema as get_instance_status return value).
        Returns empty list if table is empty or does not exist.
    """
    try:
        rows = conn.execute(
            "SELECT instance_id, bytes_sent, bytes_received, utilization_pct, "
            "compaction_count, last_updated "
            "FROM context_utilization ORDER BY utilization_pct DESC"
        ).fetchall()
    except duckdb.CatalogException:
        return []

    return [
        {
            "instance_id": r[0],
            "bytes_sent": int(r[1]),
            "bytes_received": int(r[2]),
            "utilization_pct": float(r[3]),
            "compaction_count": int(r[4]),
            "last_updated": r[5],
        }
        for r in rows
    ]


def get_connector_status(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Return status for all registered connectors.

    Queries the connector_registry table created by ConnectorRegistry.
    Returns rows sorted by name. Returns empty list if table is missing or empty.

    Args:
        conn: Open DuckDB connection.

    Returns:
        List of dicts with keys: name, connector_type, status,
        last_used, trip_time, registered_at.
        Returns empty list when table is absent (CatalogException).
    """
    try:
        rows = conn.execute(
            "SELECT name, connector_type, status, last_used, trip_time, registered_at "
            "FROM connector_registry ORDER BY name"
        ).fetchall()
    except duckdb.CatalogException:
        return []

    return [
        {
            "name": r[0],
            "connector_type": r[1],
            "status": r[2],
            "last_used": r[3],
            "trip_time": r[4],
            "registered_at": r[5],
        }
        for r in rows
    ]
