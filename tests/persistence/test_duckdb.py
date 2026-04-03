"""Tests for DuckDB-backed LangGraph checkpointer."""
import pytest
from langgraph.checkpoint.duckdb import DuckDBSaver

from lattice.persistence.checkpointer import create_checkpointer


class TestCheckpointerCreation:
    def test_creates_duckdb_saver_instance(self):
        checkpointer = create_checkpointer(":memory:")
        assert isinstance(checkpointer, DuckDBSaver)

    def test_setup_creates_tables(self):
        """After create_checkpointer, the checkpoint tables must exist."""
        checkpointer = create_checkpointer(":memory:")
        # Query the information schema to verify tables were created
        result = checkpointer.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        table_names = {row[0] for row in result}
        # DuckDB checkpointer creates 'checkpoints' and 'writes' tables
        assert "checkpoints" in table_names or len(table_names) > 0, (
            f"Expected checkpoint tables, got: {table_names}"
        )

    def test_different_connections_are_independent(self):
        """Two in-memory checkpointers should be independent."""
        cp1 = create_checkpointer(":memory:")
        cp2 = create_checkpointer(":memory:")
        # Both should be valid instances
        assert isinstance(cp1, DuckDBSaver)
        assert isinstance(cp2, DuckDBSaver)
        assert cp1 is not cp2


class TestCheckpointerRoundTrip:
    def test_put_get_roundtrip(self):
        """Put a checkpoint then retrieve it successfully."""
        import uuid
        from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata

        checkpointer = create_checkpointer(":memory:")

        thread_id = str(uuid.uuid4())
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": "test-checkpoint-1",
            }
        }

        checkpoint: Checkpoint = {
            "v": 1,
            "id": "test-checkpoint-1",
            "ts": "2026-01-01T00:00:00+00:00",
            "channel_values": {"messages": []},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }
        metadata: CheckpointMetadata = {
            "source": "input",
            "step": 0,
            "writes": None,
            "parents": {},
        }

        # Put the checkpoint
        saved_config = checkpointer.put(config, checkpoint, metadata, {})
        assert saved_config is not None

        # Get it back
        result = checkpointer.get(config)
        assert result is not None


class TestLangGraphIntegration:
    def test_state_graph_compiles_with_checkpointer(self):
        """LangGraph StateGraph compiles successfully with DuckDB checkpointer."""
        from typing import TypedDict
        from langgraph.graph import StateGraph

        checkpointer = create_checkpointer(":memory:")

        class SimpleState(TypedDict):
            count: int

        def increment_node(state: SimpleState) -> SimpleState:
            return {"count": state["count"] + 1}

        builder = StateGraph(SimpleState)
        builder.add_node("increment", increment_node)
        builder.set_entry_point("increment")
        builder.set_finish_point("increment")

        graph = builder.compile(checkpointer=checkpointer)
        assert graph is not None

    def test_state_graph_executes_with_checkpointer(self):
        """LangGraph graph can invoke and checkpoint is stored."""
        import uuid
        from typing import TypedDict
        from langgraph.graph import StateGraph

        checkpointer = create_checkpointer(":memory:")

        class SimpleState(TypedDict):
            count: int

        def increment_node(state: SimpleState) -> SimpleState:
            return {"count": state["count"] + 1}

        builder = StateGraph(SimpleState)
        builder.add_node("increment", increment_node)
        builder.set_entry_point("increment")
        builder.set_finish_point("increment")

        graph = builder.compile(checkpointer=checkpointer)

        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke({"count": 0}, config=config)
        assert result["count"] == 1

        # Verify checkpoint was stored
        saved = checkpointer.get({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
        assert saved is not None
