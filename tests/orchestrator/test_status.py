"""Unit tests for orchestrator status query functions.

Tests cover:
- get_instance_status returns full row when instance exists
- get_instance_status returns zero-value dict for unknown instance
- get_all_instance_status returns all rows sorted by utilization
- get_all_instance_status returns empty list on empty table
- get_all_instance_status returns empty list when table does not exist
- get_connector_status returns connector rows from connector_registry table
- get_connector_status returns empty list when table is absent
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pytest

from lattice.orchestrator.status import (
    get_all_instance_status,
    get_connector_status,
    get_instance_status,
)


_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS context_utilization (
        instance_id TEXT PRIMARY KEY,
        bytes_sent INTEGER DEFAULT 0,
        bytes_received INTEGER DEFAULT 0,
        utilization_pct REAL DEFAULT 0.0,
        compaction_count INTEGER DEFAULT 0,
        last_updated TEXT NOT NULL
    )
"""


@pytest.fixture
def conn():
    """In-memory DuckDB connection with context_utilization table created."""
    c = duckdb.connect(":memory:")
    c.execute(_CREATE_TABLE_SQL)
    yield c
    c.close()


@pytest.fixture
def empty_conn():
    """In-memory DuckDB connection with NO context_utilization table."""
    c = duckdb.connect(":memory:")
    yield c
    c.close()


_NOW = datetime.now(timezone.utc).isoformat()


def _insert(conn: duckdb.DuckDBPyConnection, instance_id: str, bytes_sent: int, bytes_received: int, utilization_pct: float, compaction_count: int) -> None:
    conn.execute(
        "INSERT INTO context_utilization "
        "(instance_id, bytes_sent, bytes_received, utilization_pct, compaction_count, last_updated) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [instance_id, bytes_sent, bytes_received, utilization_pct, compaction_count, _NOW],
    )


class TestGetInstanceStatus:
    """Tests for get_instance_status query function."""

    def test_returns_row_when_instance_exists(self, conn):
        """get_instance_status returns full dict when the instance row is present."""
        _insert(conn, "inst-1", 1024, 512, 45.5, 2)

        result = get_instance_status(conn, "inst-1")

        assert result["instance_id"] == "inst-1"
        assert result["bytes_sent"] == 1024
        assert result["bytes_received"] == 512
        assert result["utilization_pct"] == pytest.approx(45.5)
        assert result["compaction_count"] == 2
        assert result["last_updated"] == _NOW

    def test_returns_zero_value_dict_for_unknown_instance(self, conn):
        """get_instance_status returns zero-value dict when instance has no row."""
        result = get_instance_status(conn, "unknown-instance")

        assert result["instance_id"] == "unknown-instance"
        assert result["bytes_sent"] == 0
        assert result["bytes_received"] == 0
        assert result["utilization_pct"] == pytest.approx(0.0)
        assert result["compaction_count"] == 0
        assert result["last_updated"] is None

    def test_returns_zero_value_when_table_does_not_exist(self, empty_conn):
        """get_instance_status returns zero-value dict (no error) when table is absent."""
        result = get_instance_status(empty_conn, "inst-missing")

        assert result["instance_id"] == "inst-missing"
        assert result["bytes_sent"] == 0
        assert result["bytes_received"] == 0
        assert result["utilization_pct"] == pytest.approx(0.0)
        assert result["compaction_count"] == 0
        assert result["last_updated"] is None

    def test_numeric_types_are_correct(self, conn):
        """bytes_sent, bytes_received, compaction_count are int; utilization_pct is float."""
        _insert(conn, "inst-types", 100, 200, 12.5, 3)

        result = get_instance_status(conn, "inst-types")

        assert isinstance(result["bytes_sent"], int)
        assert isinstance(result["bytes_received"], int)
        assert isinstance(result["utilization_pct"], float)
        assert isinstance(result["compaction_count"], int)


class TestGetAllInstanceStatus:
    """Tests for get_all_instance_status query function."""

    def test_returns_list_of_all_rows(self, conn):
        """get_all_instance_status returns all rows when data exists."""
        _insert(conn, "inst-a", 100, 50, 10.0, 0)
        _insert(conn, "inst-b", 200, 100, 30.0, 1)
        _insert(conn, "inst-c", 500, 250, 75.0, 2)

        result = get_all_instance_status(conn)

        assert len(result) == 3
        instance_ids = {r["instance_id"] for r in result}
        assert instance_ids == {"inst-a", "inst-b", "inst-c"}

    def test_returns_rows_sorted_by_utilization_descending(self, conn):
        """get_all_instance_status returns rows ordered by utilization_pct DESC."""
        _insert(conn, "low",  100,  50, 10.0, 0)
        _insert(conn, "high", 500, 250, 75.0, 2)
        _insert(conn, "mid",  200, 100, 30.0, 1)

        result = get_all_instance_status(conn)

        utilizations = [r["utilization_pct"] for r in result]
        assert utilizations == sorted(utilizations, reverse=True)

    def test_returns_empty_list_when_table_is_empty(self, conn):
        """get_all_instance_status returns [] when table exists but has no rows."""
        result = get_all_instance_status(conn)

        assert result == []

    def test_returns_empty_list_when_table_does_not_exist(self, empty_conn):
        """get_all_instance_status returns [] (no error) when table is absent."""
        result = get_all_instance_status(empty_conn)

        assert result == []

    def test_row_fields_have_correct_types(self, conn):
        """Each returned row has correct field types."""
        _insert(conn, "inst-x", 1000, 500, 55.5, 3)

        result = get_all_instance_status(conn)

        assert len(result) == 1
        row = result[0]
        assert isinstance(row["instance_id"], str)
        assert isinstance(row["bytes_sent"], int)
        assert isinstance(row["bytes_received"], int)
        assert isinstance(row["utilization_pct"], float)
        assert isinstance(row["compaction_count"], int)


# ---------------------------------------------------------------------------
# get_connector_status tests
# ---------------------------------------------------------------------------

_CONNECTOR_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS connector_registry (
        name TEXT PRIMARY KEY,
        connector_type TEXT NOT NULL,
        status TEXT NOT NULL,
        last_used TEXT,
        trip_time TEXT,
        registered_at TEXT NOT NULL
    )
"""


@pytest.fixture
def connector_conn():
    """In-memory DuckDB connection with connector_registry table created."""
    c = duckdb.connect(":memory:")
    c.execute(_CONNECTOR_TABLE_SQL)
    yield c
    c.close()


@pytest.fixture
def empty_connector_conn():
    """In-memory DuckDB connection with NO connector_registry table."""
    c = duckdb.connect(":memory:")
    yield c
    c.close()


def _insert_connector(conn, name: str, connector_type: str, status: str = "online") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO connector_registry (name, connector_type, status, last_used, trip_time, registered_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [name, connector_type, status, None, None, now],
    )


class TestGetConnectorStatus:
    """Tests for get_connector_status query function."""

    def test_returns_list_of_all_connectors(self, connector_conn) -> None:
        """get_connector_status returns all rows when connectors are registered."""
        _insert_connector(connector_conn, "tavily", "tavily")
        _insert_connector(connector_conn, "github", "github")

        result = get_connector_status(connector_conn)

        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"tavily", "github"}

    def test_returns_correct_field_schema(self, connector_conn) -> None:
        """Each row has name, connector_type, status, last_used, trip_time, registered_at."""
        _insert_connector(connector_conn, "mattermost", "mattermost", "online")

        result = get_connector_status(connector_conn)

        assert len(result) == 1
        row = result[0]
        assert row["name"] == "mattermost"
        assert row["connector_type"] == "mattermost"
        assert row["status"] == "online"
        assert "last_used" in row
        assert "trip_time" in row
        assert "registered_at" in row

    def test_returns_empty_list_when_table_is_empty(self, connector_conn) -> None:
        """get_connector_status returns [] when table exists but has no rows."""
        result = get_connector_status(connector_conn)
        assert result == []

    def test_returns_empty_list_when_table_does_not_exist(
        self, empty_connector_conn
    ) -> None:
        """get_connector_status returns [] (no error) when table is absent."""
        result = get_connector_status(empty_connector_conn)
        assert result == []

    def test_returns_rows_sorted_by_name(self, connector_conn) -> None:
        """get_connector_status returns rows ordered by name."""
        _insert_connector(connector_conn, "tavily", "tavily")
        _insert_connector(connector_conn, "github", "github")
        _insert_connector(connector_conn, "mattermost", "mattermost")

        result = get_connector_status(connector_conn)

        names = [r["name"] for r in result]
        assert names == sorted(names)
