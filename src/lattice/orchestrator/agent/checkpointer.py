"""DuckDB-backed checkpoint saver for LangGraph.

Persists agent state (messages, instances, pending approvals) to DuckDB
so the orchestrator can resume from the last checkpoint after a crash
or restart. Uses a single table with JSON-serialized state.
"""
from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from typing import Any

import duckdb
import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

logger = structlog.get_logger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS orchestrator_checkpoints (
    thread_id      VARCHAR NOT NULL,
    checkpoint_ns  VARCHAR NOT NULL DEFAULT '',
    checkpoint_id  VARCHAR NOT NULL,
    parent_id      VARCHAR,
    checkpoint     JSON NOT NULL,
    metadata       JSON NOT NULL DEFAULT '{}',
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
)
"""

_CREATE_WRITES_TABLE = """
CREATE TABLE IF NOT EXISTS orchestrator_checkpoint_writes (
    thread_id       VARCHAR NOT NULL,
    checkpoint_ns   VARCHAR NOT NULL DEFAULT '',
    checkpoint_id   VARCHAR NOT NULL,
    task_id         VARCHAR NOT NULL,
    idx             INTEGER NOT NULL,
    channel         VARCHAR NOT NULL,
    type            VARCHAR,
    value           JSON,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
)
"""


class DuckDBCheckpointer(BaseCheckpointSaver):
    """LangGraph checkpoint saver backed by DuckDB.

    Stores checkpoint state as JSON in a single DuckDB table.
    Supports get, put, and list operations required by LangGraph.

    Args:
        conn: An open DuckDB connection.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        super().__init__()
        self._conn = conn
        self._init_tables()

    def _init_tables(self) -> None:
        """Create checkpoint tables if they don't exist."""
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_WRITES_TABLE)
        logger.debug("duckdb_checkpointer.tables_initialized")

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Get a checkpoint tuple by config.

        Args:
            config: RunnableConfig with thread_id and optional checkpoint_id.

        Returns:
            CheckpointTuple or None if not found.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id")

        if checkpoint_id:
            rows = self._conn.execute(
                """SELECT checkpoint_id, parent_id, checkpoint, metadata
                   FROM orchestrator_checkpoints
                   WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?""",
                [thread_id, checkpoint_ns, checkpoint_id],
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT checkpoint_id, parent_id, checkpoint, metadata
                   FROM orchestrator_checkpoints
                   WHERE thread_id = ? AND checkpoint_ns = ?
                   ORDER BY created_at DESC LIMIT 1""",
                [thread_id, checkpoint_ns],
            ).fetchall()

        if not rows:
            return None

        row = rows[0]
        cp_id, parent_id, checkpoint_json, metadata_json = row

        checkpoint_data = json.loads(checkpoint_json) if isinstance(checkpoint_json, str) else checkpoint_json
        metadata_data = json.loads(metadata_json) if isinstance(metadata_json, str) else metadata_json

        result_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": cp_id,
            }
        }

        parent_config = None
        if parent_id:
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_id,
                }
            }

        # Fetch pending writes
        write_rows = self._conn.execute(
            """SELECT task_id, channel, value
               FROM orchestrator_checkpoint_writes
               WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
               ORDER BY idx""",
            [thread_id, checkpoint_ns, cp_id],
        ).fetchall()

        pending_writes = [
            (task_id, channel, json.loads(value) if isinstance(value, str) else value)
            for task_id, channel, value in write_rows
        ]

        return CheckpointTuple(
            config=result_config,
            checkpoint=checkpoint_data,
            metadata=metadata_data,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints for a thread.

        Args:
            config: RunnableConfig with thread_id.
            filter: Optional metadata filter.
            before: Only return checkpoints before this one.
            limit: Maximum number of results.

        Yields:
            CheckpointTuple instances.
        """
        if config is None:
            return

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")

        query = """SELECT checkpoint_id, parent_id, checkpoint, metadata
                   FROM orchestrator_checkpoints
                   WHERE thread_id = ? AND checkpoint_ns = ?"""
        params: list[Any] = [thread_id, checkpoint_ns]

        if before:
            before_id = before["configurable"].get("checkpoint_id")
            if before_id:
                query += " AND created_at < (SELECT created_at FROM orchestrator_checkpoints WHERE checkpoint_id = ?)"
                params.append(before_id)

        query += " ORDER BY created_at DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        rows = self._conn.execute(query, params).fetchall()

        for row in rows:
            cp_id, parent_id, checkpoint_json, metadata_json = row
            checkpoint_data = json.loads(checkpoint_json) if isinstance(checkpoint_json, str) else checkpoint_json
            metadata_data = json.loads(metadata_json) if isinstance(metadata_json, str) else metadata_json

            result_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": cp_id,
                }
            }

            parent_config = None
            if parent_id:
                parent_config = {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_id,
                    }
                }

            yield CheckpointTuple(
                config=result_config,
                checkpoint=checkpoint_data,
                metadata=metadata_data,
                parent_config=parent_config,
            )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, int | str] | None = None,
    ) -> RunnableConfig:
        """Save a checkpoint.

        Args:
            config: RunnableConfig with thread_id.
            checkpoint: The checkpoint data to save.
            metadata: Checkpoint metadata.
            new_versions: Channel version updates.

        Returns:
            Updated RunnableConfig with the new checkpoint_id.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")

        checkpoint_json = json.dumps(checkpoint, default=str)
        metadata_json = json.dumps(metadata, default=str)

        self._conn.execute(
            """INSERT OR REPLACE INTO orchestrator_checkpoints
               (thread_id, checkpoint_ns, checkpoint_id, parent_id, checkpoint, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                parent_id,
                checkpoint_json,
                metadata_json,
                datetime.now(timezone.utc).isoformat(),
            ],
        )

        logger.debug(
            "duckdb_checkpointer.put",
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
    ) -> None:
        """Save pending writes for a checkpoint.

        Args:
            config: RunnableConfig with checkpoint details.
            writes: Sequence of (channel, value) tuples.
            task_id: The task that produced these writes.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        for idx, (channel, value) in enumerate(writes):
            value_json = json.dumps(value, default=str)
            self._conn.execute(
                """INSERT OR REPLACE INTO orchestrator_checkpoint_writes
                   (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, value)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, value_json],
            )
