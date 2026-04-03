"""ConnectorRegistry: runtime registry with DuckDB persistence and per-connector circuit breakers.

Design decisions:
- Constructor creates tables (same pattern as ProcessRegistry / FleetCheckpoint)
- INSERT OR REPLACE for idempotent upserts
- Per-connector CircuitBreaker instances in _breakers dict
- Auto-reset after breaker_cooldown_seconds via time.monotonic comparison
- Write operations require explicit confirmed=True to prevent accidental writes
- connector_state table provides per-connector key-value storage (e.g. last_post_id)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import duckdb
import structlog

from lattice.orchestrator.breaker import CircuitBreaker
from lattice.orchestrator.connectors.base import BaseConnector
from lattice.orchestrator.connectors.models import ConnectorResult

log = structlog.get_logger(__name__)


class ConnectorRegistry:
    """Runtime registry for MCP connectors with DuckDB persistence.

    Registers and deregisters connectors at runtime. Each connector gets a
    per-connector CircuitBreaker instance. Registry state persists to DuckDB
    so status survives orchestrator restarts.

    Args:
        conn: An open duckdb.DuckDBPyConnection instance.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self._connectors: dict[str, BaseConnector] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._trip_times: dict[str, float] = {}
        self._create_tables()

    def _create_tables(self) -> None:
        """Create connector_registry and connector_state tables idempotently."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS connector_registry (
                name TEXT PRIMARY KEY,
                connector_type TEXT NOT NULL,
                status TEXT NOT NULL,
                last_used TEXT,
                trip_time TEXT,
                registered_at TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS connector_state (
                name TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (name, key)
            )
        """)

    def register(self, connector: BaseConnector) -> None:
        """Register a connector and create its circuit breaker.

        Adds to in-memory dicts and upserts DuckDB row with status="online".

        Args:
            connector: The BaseConnector implementation to register.
        """
        name = connector.name
        self._connectors[name] = connector
        self._breakers[name] = CircuitBreaker(
            instance_id=name,
            config=connector.config.breaker,
        )
        self._trip_times.pop(name, None)

        registered_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO connector_registry "
            "(name, connector_type, status, last_used, trip_time, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [name, connector.config.connector_type, "online", None, None, registered_at],
        )

        log.info("connector_registered", name=name, connector_type=connector.config.connector_type)

    def deregister(self, name: str) -> None:
        """Remove a connector from the registry.

        Pops from in-memory dicts and upserts DuckDB status="offline".
        Does not raise if the connector is not registered.

        Args:
            name: The connector name to remove.
        """
        connector = self._connectors.pop(name, None)
        self._breakers.pop(name, None)
        self._trip_times.pop(name, None)

        if connector is not None:
            # Update the existing row's status to offline
            self._conn.execute(
                "UPDATE connector_registry SET status = 'offline' WHERE name = ?",
                [name],
            )
        else:
            # Upsert even if not in memory — may exist from a prior session
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT OR REPLACE INTO connector_registry "
                "(name, connector_type, status, last_used, trip_time, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [name, "unknown", "offline", None, None, now],
            )

        log.info("connector_deregistered", name=name)

    def list_connectors(self) -> list[str]:
        """Return sorted list of registered connector names.

        Returns:
            Sorted list of connector name strings.
        """
        return sorted(self._connectors.keys())

    def get_connector(self, name: str) -> BaseConnector | None:
        """Return the connector by name.

        Args:
            name: Connector name to look up.

        Returns:
            BaseConnector if registered, None otherwise.
        """
        return self._connectors.get(name)

    async def fetch(self, connector_name: str, query: str, **kwargs: Any) -> ConnectorResult:
        """Fetch from a connector with circuit breaker protection.

        Checks breaker state before delegating to connector.fetch().
        Auto-resets breaker if cooldown has elapsed.
        Records success/error and updates last_used in DuckDB on success.

        Args:
            connector_name: Name of the registered connector.
            query: The query or resource identifier to fetch.
            **kwargs: Passed through to connector.fetch().

        Returns:
            ConnectorResult from the connector, or failure result on error/missing.
        """
        connector = self._connectors.get(connector_name)
        if connector is None:
            return ConnectorResult(
                success=False,
                source=connector_name,
                error="Connector not registered or offline",
            )

        breaker = self._breakers[connector_name]

        # Auto-reset if cooldown has elapsed
        if breaker.is_tripped:
            cooldown = connector.config.breaker_cooldown_seconds
            trip_time = self._trip_times.get(connector_name, 0.0)
            if time.monotonic() - trip_time >= cooldown:
                breaker.reset()
                self._trip_times.pop(connector_name, None)
                log.info("connector_breaker_auto_reset", name=connector_name)
            else:
                return ConnectorResult(
                    success=False,
                    source=connector_name,
                    error=f"Circuit breaker tripped for connector '{connector_name}'",
                )

        try:
            result = await connector.fetch(query, **kwargs)
            breaker.record_success()
            # Update last_used in DuckDB
            last_used = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE connector_registry SET last_used = ? WHERE name = ?",
                [last_used, connector_name],
            )
            return result
        except Exception as exc:
            state = breaker.record_error()
            if state.tripped:
                self._trip_times[connector_name] = time.monotonic()
                # Update trip_time in DuckDB
                trip_time_str = datetime.now(timezone.utc).isoformat()
                self._conn.execute(
                    "UPDATE connector_registry SET status = 'tripped', trip_time = ? WHERE name = ?",
                    [trip_time_str, connector_name],
                )
                log.warning("connector_breaker_tripped", name=connector_name)
            log.error("connector_fetch_error", name=connector_name, error=str(exc))
            return ConnectorResult(
                success=False,
                source=connector_name,
                error=str(exc),
            )

    async def write(
        self,
        connector_name: str,
        content: str,
        confirmed: bool = False,
        **kwargs: Any,
    ) -> ConnectorResult:
        """Write via a connector with confirmation guard and circuit breaker protection.

        Write operations are irreversible and require explicit confirmation.

        Args:
            connector_name: Name of the registered connector.
            content: Content to write.
            confirmed: Must be True to allow the write. Prevents accidental writes.
            **kwargs: Passed through to connector.write().

        Returns:
            ConnectorResult from the connector, or failure result if unconfirmed/missing.
        """
        if not confirmed:
            return ConnectorResult(
                success=False,
                source=connector_name,
                error="Write operation requires operator confirmation (pass confirmed=True)",
            )

        connector = self._connectors.get(connector_name)
        if connector is None:
            return ConnectorResult(
                success=False,
                source=connector_name,
                error="Connector not registered or offline",
            )

        breaker = self._breakers[connector_name]

        # Auto-reset if cooldown has elapsed
        if breaker.is_tripped:
            cooldown = connector.config.breaker_cooldown_seconds
            trip_time = self._trip_times.get(connector_name, 0.0)
            if time.monotonic() - trip_time >= cooldown:
                breaker.reset()
                self._trip_times.pop(connector_name, None)
                log.info("connector_breaker_auto_reset", name=connector_name)
            else:
                return ConnectorResult(
                    success=False,
                    source=connector_name,
                    error=f"Circuit breaker tripped for connector '{connector_name}'",
                )

        try:
            result = await connector.write(content, **kwargs)
            breaker.record_success()
            return result
        except Exception as exc:
            state = breaker.record_error()
            if state.tripped:
                self._trip_times[connector_name] = time.monotonic()
                trip_time_str = datetime.now(timezone.utc).isoformat()
                self._conn.execute(
                    "UPDATE connector_registry SET status = 'tripped', trip_time = ? WHERE name = ?",
                    [trip_time_str, connector_name],
                )
                log.warning("connector_breaker_tripped", name=connector_name)
            log.error("connector_write_error", name=connector_name, error=str(exc))
            return ConnectorResult(
                success=False,
                source=connector_name,
                error=str(exc),
            )

    def get_state(self, name: str, key: str) -> str | None:
        """Read a per-connector key-value state entry.

        Args:
            name: Connector name.
            key: State key (e.g. "last_post_id").

        Returns:
            Stored value string, or None if not found.
        """
        rows = self._conn.execute(
            "SELECT value FROM connector_state WHERE name = ? AND key = ?",
            [name, key],
        ).fetchall()
        if rows:
            return rows[0][0]
        return None

    def set_state(self, name: str, key: str, value: str) -> None:
        """Write a per-connector key-value state entry.

        Uses INSERT OR REPLACE for idempotent upserts.

        Args:
            name: Connector name.
            key: State key (e.g. "last_post_id").
            value: Value to store.
        """
        updated_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO connector_state (name, key, value, updated_at) "
            "VALUES (?, ?, ?, ?)",
            [name, key, value, updated_at],
        )
