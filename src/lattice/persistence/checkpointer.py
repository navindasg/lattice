"""DuckDB-backed LangGraph checkpointer factory.

Creates and initializes a DuckDBSaver with the required tables via .setup().
The .setup() call is idempotent — safe to call on every application startup.

Note: DuckDBSaver.from_conn_string() is a context manager, so we instantiate
directly via DuckDBSaver(conn) to get a long-lived checkpointer outside a
with-block. The caller owns the connection lifetime.
"""
from __future__ import annotations

import os

import duckdb
import structlog
from langgraph.checkpoint.duckdb import DuckDBSaver

log = structlog.get_logger(__name__)


def create_checkpointer(db_path: str = ".data/checkpoints.duckdb") -> DuckDBSaver:
    """Create a DuckDB-backed LangGraph checkpointer.

    Args:
        db_path: Path to DuckDB file, or ":memory:" for in-memory (tests).
                 Parent directory is created automatically for file-backed paths.

    Returns:
        A DuckDBSaver instance with tables initialized via .setup().
    """
    if db_path != ":memory:":
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    conn = duckdb.connect(db_path)
    checkpointer = DuckDBSaver(conn)
    checkpointer.setup()

    log.info("checkpointer_initialized", db_path=db_path)

    return checkpointer
