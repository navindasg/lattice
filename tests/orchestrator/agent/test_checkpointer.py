"""Tests for DuckDBCheckpointer: checkpoint save, restore, and resume."""
from __future__ import annotations

import duckdb
import pytest

from lattice.orchestrator.agent.checkpointer import DuckDBCheckpointer


@pytest.fixture
def db_conn() -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection."""
    return duckdb.connect(":memory:")


@pytest.fixture
def checkpointer(db_conn: duckdb.DuckDBPyConnection) -> DuckDBCheckpointer:
    """Create a DuckDBCheckpointer with in-memory connection."""
    return DuckDBCheckpointer(db_conn)


def _make_config(thread_id: str = "test-thread", checkpoint_id: str | None = None) -> dict:
    """Create a minimal RunnableConfig."""
    config: dict = {"configurable": {"thread_id": thread_id}}
    if checkpoint_id:
        config["configurable"]["checkpoint_id"] = checkpoint_id
    return config


def _make_checkpoint(checkpoint_id: str = "cp-001") -> dict:
    """Create a minimal checkpoint dict."""
    return {
        "id": checkpoint_id,
        "v": 1,
        "ts": "2026-04-07T12:00:00Z",
        "channel_values": {"messages": []},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


class TestDuckDBCheckpointer:
    def test_tables_created(self, checkpointer: DuckDBCheckpointer, db_conn: duckdb.DuckDBPyConnection) -> None:
        """Initialization creates checkpoint tables."""
        tables = db_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'orchestrator_%'"
        ).fetchall()
        table_names = {row[0] for row in tables}
        assert "orchestrator_checkpoints" in table_names
        assert "orchestrator_checkpoint_writes" in table_names

    def test_put_and_get(self, checkpointer: DuckDBCheckpointer) -> None:
        """Put a checkpoint and retrieve it."""
        config = _make_config()
        checkpoint = _make_checkpoint("cp-001")
        metadata = {"source": "test", "step": 1}

        result_config = checkpointer.put(config, checkpoint, metadata)
        assert result_config["configurable"]["checkpoint_id"] == "cp-001"

        retrieved = checkpointer.get_tuple(_make_config())
        assert retrieved is not None
        assert retrieved.checkpoint["id"] == "cp-001"
        assert retrieved.metadata["source"] == "test"

    def test_get_returns_none_when_empty(self, checkpointer: DuckDBCheckpointer) -> None:
        """Get returns None when no checkpoints exist."""
        result = checkpointer.get_tuple(_make_config())
        assert result is None

    def test_get_specific_checkpoint(self, checkpointer: DuckDBCheckpointer) -> None:
        """Get with checkpoint_id returns that specific checkpoint."""
        config = _make_config()
        checkpointer.put(config, _make_checkpoint("cp-001"), {"step": 1})

        config2 = _make_config(checkpoint_id="cp-001")
        checkpointer.put(config2, _make_checkpoint("cp-002"), {"step": 2})

        result = checkpointer.get_tuple(_make_config(checkpoint_id="cp-001"))
        assert result is not None
        assert result.checkpoint["id"] == "cp-001"

    def test_get_latest_checkpoint(self, checkpointer: DuckDBCheckpointer) -> None:
        """Get without checkpoint_id returns the latest checkpoint."""
        config = _make_config()
        checkpointer.put(config, _make_checkpoint("cp-001"), {"step": 1})

        config2 = _make_config(checkpoint_id="cp-001")
        checkpointer.put(config2, _make_checkpoint("cp-002"), {"step": 2})

        result = checkpointer.get_tuple(_make_config())
        assert result is not None
        assert result.checkpoint["id"] == "cp-002"

    def test_parent_config_tracked(self, checkpointer: DuckDBCheckpointer) -> None:
        """Parent checkpoint_id is tracked."""
        config = _make_config()
        checkpointer.put(config, _make_checkpoint("cp-001"), {"step": 1})

        config2 = _make_config(checkpoint_id="cp-001")
        checkpointer.put(config2, _make_checkpoint("cp-002"), {"step": 2})

        result = checkpointer.get_tuple(_make_config(checkpoint_id="cp-002"))
        assert result is not None
        assert result.parent_config is not None
        assert result.parent_config["configurable"]["checkpoint_id"] == "cp-001"

    def test_list_checkpoints(self, checkpointer: DuckDBCheckpointer) -> None:
        """List returns all checkpoints for a thread."""
        config = _make_config()
        checkpointer.put(config, _make_checkpoint("cp-001"), {"step": 1})

        config2 = _make_config(checkpoint_id="cp-001")
        checkpointer.put(config2, _make_checkpoint("cp-002"), {"step": 2})

        results = list(checkpointer.list(_make_config()))
        assert len(results) == 2

    def test_list_with_limit(self, checkpointer: DuckDBCheckpointer) -> None:
        """List with limit returns at most N checkpoints."""
        config = _make_config()
        checkpointer.put(config, _make_checkpoint("cp-001"), {"step": 1})

        config2 = _make_config(checkpoint_id="cp-001")
        checkpointer.put(config2, _make_checkpoint("cp-002"), {"step": 2})

        results = list(checkpointer.list(_make_config(), limit=1))
        assert len(results) == 1

    def test_list_none_config(self, checkpointer: DuckDBCheckpointer) -> None:
        """List with None config returns empty."""
        results = list(checkpointer.list(None))
        assert len(results) == 0

    def test_put_writes(self, checkpointer: DuckDBCheckpointer) -> None:
        """put_writes stores pending writes."""
        config = _make_config()
        checkpointer.put(config, _make_checkpoint("cp-001"), {"step": 1})

        write_config = _make_config(checkpoint_id="cp-001")
        checkpointer.put_writes(
            write_config,
            [("messages", {"content": "hello"})],
            task_id="task-1",
        )

        result = checkpointer.get_tuple(_make_config(checkpoint_id="cp-001"))
        assert result is not None
        assert len(result.pending_writes) == 1
        assert result.pending_writes[0][1] == "messages"

    def test_multiple_threads_isolated(self, checkpointer: DuckDBCheckpointer) -> None:
        """Checkpoints from different threads are isolated."""
        config_a = _make_config("thread-A")
        checkpointer.put(config_a, _make_checkpoint("cp-A1"), {"thread": "A"})

        config_b = _make_config("thread-B")
        checkpointer.put(config_b, _make_checkpoint("cp-B1"), {"thread": "B"})

        result_a = checkpointer.get_tuple(_make_config("thread-A"))
        assert result_a is not None
        assert result_a.checkpoint["id"] == "cp-A1"

        result_b = checkpointer.get_tuple(_make_config("thread-B"))
        assert result_b is not None
        assert result_b.checkpoint["id"] == "cp-B1"
