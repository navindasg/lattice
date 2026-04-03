"""Shared pytest fixtures for the lattice package tests.

Session-scoped fixtures provide:
- tmp_data_dir: A temporary .data/ directory for file-based persistence tests
- in_memory_duckdb: In-memory DuckDB connection string (avoids file locking in Docker)

configure_logging() is called at session scope so all tests share a consistent
log configuration.
"""
import pytest

from lattice.logging import configure_logging


@pytest.fixture(scope="session", autouse=True)
def setup_logging():
    """Configure structlog once per test session."""
    configure_logging(log_level="DEBUG")


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Temporary .data/ directory for file-based persistence tests.

    Automatically cleaned up after each test. Use this instead of hardcoded
    .data/ paths so tests are isolated and hermetic.

    Example:
        def test_index_persist(tmp_data_dir):
            index_path = tmp_data_dir / "faiss.index"
            save_index(index, str(index_path))
            assert index_path.exists()
    """
    data_dir = tmp_path / ".data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def in_memory_duckdb():
    """In-memory DuckDB connection string.

    Returns ":memory:" so tests never write to disk and can run concurrently
    without file-locking issues (e.g., when both host and container run tests).

    Example:
        def test_checkpointer(in_memory_duckdb):
            checkpointer = DuckDBSaver.from_conn_string(in_memory_duckdb)
            checkpointer.setup()
    """
    return ":memory:"
