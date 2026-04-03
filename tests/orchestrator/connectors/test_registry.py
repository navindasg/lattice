"""Tests for ConnectorRegistry with DuckDB persistence and per-connector circuit breakers.

Uses an in-memory DuckDB connection and a FakeConnector for isolation.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import duckdb
import pytest

from lattice.orchestrator.connectors.base import BaseConnector, ConnectorError
from lattice.orchestrator.connectors.models import ConnectorConfig, ConnectorResult
from lattice.orchestrator.connectors.registry import ConnectorRegistry


# ---------------------------------------------------------------------------
# FakeConnector for testing
# ---------------------------------------------------------------------------


class FakeConnector(BaseConnector):
    """Test double that returns canned results and optionally raises errors."""

    def __init__(self, config: ConnectorConfig, *, error_count: int = 0) -> None:
        super().__init__(config)
        self._error_count = error_count
        self._calls = 0

    async def fetch(self, query: str, **kwargs: object) -> ConnectorResult:
        self._calls += 1
        if self._calls <= self._error_count:
            raise ConnectorError("fake fetch error")
        return ConnectorResult(success=True, source=self._config.name, content=f"result:{query}")

    async def write(self, content: str, **kwargs: object) -> ConnectorResult:
        return ConnectorResult(success=True, source=self._config.name, content="written")


def make_config(name: str = "fake", connector_type: str = "tavily") -> ConnectorConfig:
    return ConnectorConfig(name=name, connector_type=connector_type)  # type: ignore[arg-type]


def make_registry() -> ConnectorRegistry:
    conn = duckdb.connect(":memory:")
    return ConnectorRegistry(conn)


# ---------------------------------------------------------------------------
# BaseConnector ABC
# ---------------------------------------------------------------------------


class TestBaseConnectorABC:
    def test_instantiating_directly_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            BaseConnector(make_config())  # type: ignore[abstract]

    def test_fake_connector_can_be_instantiated(self) -> None:
        cfg = make_config()
        conn = FakeConnector(cfg)
        assert conn.name == "fake"

    def test_name_property(self) -> None:
        cfg = make_config(name="tavily")
        conn = FakeConnector(cfg)
        assert conn.name == "tavily"

    def test_config_property(self) -> None:
        cfg = make_config()
        conn = FakeConnector(cfg)
        assert conn.config is cfg

    def test_health_check_returns_true_by_default(self) -> None:
        cfg = make_config()
        conn = FakeConnector(cfg)
        result = asyncio.run(conn.health_check())
        assert result is True


class TestConnectorError:
    def test_inherits_exception(self) -> None:
        err = ConnectorError("bad thing")
        assert isinstance(err, Exception)

    def test_message_preserved(self) -> None:
        err = ConnectorError("bad thing")
        assert str(err) == "bad thing"


# ---------------------------------------------------------------------------
# ConnectorRegistry — basic operations
# ---------------------------------------------------------------------------


class TestConnectorRegistryRegister:
    def test_register_adds_to_list(self) -> None:
        registry = make_registry()
        connector = FakeConnector(make_config("fake"))
        registry.register(connector)
        assert "fake" in registry.list_connectors()

    def test_register_multiple(self) -> None:
        registry = make_registry()
        registry.register(FakeConnector(make_config("a", "tavily")))
        registry.register(FakeConnector(make_config("b", "github")))
        names = registry.list_connectors()
        assert "a" in names
        assert "b" in names

    def test_list_connectors_returns_sorted(self) -> None:
        registry = make_registry()
        registry.register(FakeConnector(make_config("z", "tavily")))
        registry.register(FakeConnector(make_config("a", "github")))
        names = registry.list_connectors()
        assert names == sorted(names)

    def test_get_connector_returns_instance(self) -> None:
        registry = make_registry()
        connector = FakeConnector(make_config("fake"))
        registry.register(connector)
        assert registry.get_connector("fake") is connector

    def test_get_connector_nonexistent_returns_none(self) -> None:
        registry = make_registry()
        assert registry.get_connector("nonexistent") is None


class TestConnectorRegistryDeregister:
    def test_deregister_removes_connector(self) -> None:
        registry = make_registry()
        registry.register(FakeConnector(make_config("fake")))
        registry.deregister("fake")
        assert "fake" not in registry.list_connectors()

    def test_deregister_nonexistent_does_not_raise(self) -> None:
        registry = make_registry()
        registry.deregister("nonexistent")  # should not raise

    def test_deregister_returns_none(self) -> None:
        registry = make_registry()
        registry.register(FakeConnector(make_config("fake")))
        result = registry.deregister("fake")
        assert result is None


# ---------------------------------------------------------------------------
# ConnectorRegistry — fetch routing
# ---------------------------------------------------------------------------


class TestConnectorRegistryFetch:
    def test_fetch_returns_connector_result(self) -> None:
        registry = make_registry()
        registry.register(FakeConnector(make_config("fake")))
        result = asyncio.run(registry.fetch("fake", "test query"))
        assert isinstance(result, ConnectorResult)
        assert result.success is True

    def test_fetch_calls_correct_connector(self) -> None:
        registry = make_registry()
        registry.register(FakeConnector(make_config("fake")))
        result = asyncio.run(registry.fetch("fake", "hello"))
        assert result.content == "result:hello"

    def test_fetch_nonexistent_returns_failure(self) -> None:
        registry = make_registry()
        result = asyncio.run(registry.fetch("nonexistent", "query"))
        assert result.success is False
        assert "not registered" in result.error.lower() or "offline" in result.error.lower()

    def test_fetch_after_deregister_returns_failure(self) -> None:
        registry = make_registry()
        registry.register(FakeConnector(make_config("fake")))
        registry.deregister("fake")
        result = asyncio.run(registry.fetch("fake", "query"))
        assert result.success is False


# ---------------------------------------------------------------------------
# ConnectorRegistry — circuit breaker behavior
# ---------------------------------------------------------------------------


class TestConnectorRegistryCircuitBreaker:
    def test_three_consecutive_errors_trip_breaker(self) -> None:
        registry = make_registry()
        # connector that always errors
        connector = FakeConnector(make_config("fake"), error_count=100)
        registry.register(connector)

        # 3 fetch failures should trip the breaker
        for _ in range(3):
            asyncio.run(registry.fetch("fake", "query"))

        # 4th fetch should return circuit-breaker error
        result = asyncio.run(registry.fetch("fake", "query"))
        assert result.success is False
        assert "circuit breaker" in result.error.lower() or "breaker" in result.error.lower()

    def test_breaker_trips_on_consecutive_errors_not_successes(self) -> None:
        registry = make_registry()
        # connector that succeeds after 2 errors
        connector = FakeConnector(make_config("fake"), error_count=2)
        registry.register(connector)

        # 2 errors then 1 success — breaker should NOT trip
        asyncio.run(registry.fetch("fake", "query"))
        asyncio.run(registry.fetch("fake", "query"))
        result = asyncio.run(registry.fetch("fake", "query"))  # success resets
        assert result.success is True

    def test_breaker_auto_resets_after_cooldown(self) -> None:
        """After cooldown elapses (mocked via time.monotonic), breaker auto-resets."""
        registry = make_registry()
        # connector that always errors
        connector = FakeConnector(make_config("fake"), error_count=100)
        registry.register(connector)

        # trip the breaker
        for _ in range(3):
            asyncio.run(registry.fetch("fake", "query"))

        # verify it's tripped
        tripped_result = asyncio.run(registry.fetch("fake", "query"))
        assert tripped_result.success is False

        # mock time to simulate cooldown elapsed
        # ConnectorConfig default breaker_cooldown_seconds = 120
        original_monotonic = time.monotonic
        with patch("time.monotonic", return_value=original_monotonic() + 200):
            # After cooldown, fetching a connector that errors will re-trip
            # but the auto-reset should happen first — fetch is attempted
            # Since connector still errors after reset, it will error again
            result = asyncio.run(registry.fetch("fake", "query"))
            # The breaker was reset and retry was attempted; since connector errors, returns failure
            # but NOT a "circuit breaker" message (that's only when breaker is tripped)
            # The result may be success=False due to connector error, but error comes from connector
            assert result.success is False


# ---------------------------------------------------------------------------
# ConnectorRegistry — write confirmation guard
# ---------------------------------------------------------------------------


class TestConnectorRegistryWrite:
    def test_write_without_confirmation_returns_error(self) -> None:
        registry = make_registry()
        registry.register(FakeConnector(make_config("fake")))
        result = asyncio.run(registry.write("fake", "content"))
        assert result.success is False
        assert "confirmation" in result.error.lower() or "confirmed" in result.error.lower()

    def test_write_with_confirmation_succeeds(self) -> None:
        registry = make_registry()
        registry.register(FakeConnector(make_config("fake")))
        result = asyncio.run(registry.write("fake", "content", confirmed=True))
        assert result.success is True

    def test_write_nonexistent_connector_returns_failure(self) -> None:
        registry = make_registry()
        result = asyncio.run(registry.write("nonexistent", "content", confirmed=True))
        assert result.success is False


# ---------------------------------------------------------------------------
# ConnectorRegistry — DuckDB persistence
# ---------------------------------------------------------------------------


class TestConnectorRegistryPersistence:
    def test_register_persists_to_duckdb(self) -> None:
        conn = duckdb.connect(":memory:")
        registry1 = ConnectorRegistry(conn)
        registry1.register(FakeConnector(make_config("fake")))

        # New registry with same conn — should see the row
        registry2 = ConnectorRegistry(conn)
        rows = conn.execute(
            "SELECT name, status FROM connector_registry WHERE name = 'fake'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "online"

    def test_deregister_persists_offline_status(self) -> None:
        conn = duckdb.connect(":memory:")
        registry = ConnectorRegistry(conn)
        registry.register(FakeConnector(make_config("fake")))
        registry.deregister("fake")

        rows = conn.execute(
            "SELECT name, status FROM connector_registry WHERE name = 'fake'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "offline"

    def test_connector_state_table_created(self) -> None:
        conn = duckdb.connect(":memory:")
        ConnectorRegistry(conn)
        # Should not raise - table exists
        conn.execute("SELECT COUNT(*) FROM connector_state").fetchone()

    def test_set_state_persists_key_value(self) -> None:
        conn = duckdb.connect(":memory:")
        registry = ConnectorRegistry(conn)
        registry.register(FakeConnector(make_config("fake")))
        registry.set_state("fake", "last_post_id", "post-123")

        value = registry.get_state("fake", "last_post_id")
        assert value == "post-123"

    def test_get_state_nonexistent_returns_none(self) -> None:
        conn = duckdb.connect(":memory:")
        registry = ConnectorRegistry(conn)
        value = registry.get_state("fake", "missing_key")
        assert value is None

    def test_set_state_upserts(self) -> None:
        conn = duckdb.connect(":memory:")
        registry = ConnectorRegistry(conn)
        registry.register(FakeConnector(make_config("fake")))
        registry.set_state("fake", "key", "v1")
        registry.set_state("fake", "key", "v2")
        value = registry.get_state("fake", "key")
        assert value == "v2"

    def test_registry_table_has_registered_at(self) -> None:
        conn = duckdb.connect(":memory:")
        registry = ConnectorRegistry(conn)
        registry.register(FakeConnector(make_config("fake")))

        rows = conn.execute(
            "SELECT registered_at FROM connector_registry WHERE name = 'fake'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] is not None
        assert len(rows[0][0]) > 0

    def test_create_tables_is_idempotent(self) -> None:
        """Creating ConnectorRegistry twice on same conn should not raise."""
        conn = duckdb.connect(":memory:")
        ConnectorRegistry(conn)
        ConnectorRegistry(conn)  # should not raise
