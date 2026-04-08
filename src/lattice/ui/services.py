"""Backend service layer for the Lattice TUI dashboard.

Provides async polling services that decouple UI widgets from direct
backend calls.  Each service method is a coroutine that can be called
from Textual workers or timers.

Thread safety: DuckDB connections are not thread-safe.  All DB access
is serialized through a threading.Lock and dispatched to the default
executor so the Textual event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import duckdb
import structlog

from lattice.orchestrator.events.persistence import get_history, init_events_table
from lattice.orchestrator.soul_ecosystem.models import (
    OrchestratorState,
    SoulMemoryEntry,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.terminal.models import CCInstance

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HealthSnapshot:
    """Point-in-time health metrics from the event server."""

    uptime_seconds: float = 0.0
    connected_sessions: int = 0
    pending_events: int = 0


@dataclass(frozen=True)
class DashboardSnapshot:
    """Complete dashboard state assembled from all backend sources.

    All collection fields use immutable types (tuple, MappingProxyType)
    to enforce the frozen contract at runtime.
    """

    instances: tuple[CCInstance, ...] = ()
    soul_state: OrchestratorState = field(default_factory=OrchestratorState)
    memory_entries: tuple[SoulMemoryEntry, ...] = ()
    recent_events: tuple[dict[str, Any], ...] = ()
    health: HealthSnapshot = field(default_factory=HealthSnapshot)
    captured_output: MappingProxyType[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )


class DashboardService:
    """Async service layer for polling all Lattice backend sources.

    Constructed with paths and optional db connection.  All public
    methods are async and safe to call from Textual workers.

    DuckDB access is serialized via threading.Lock and dispatched to
    a thread pool executor to avoid blocking the event loop.
    """

    def __init__(
        self,
        soul_dir: Path,
        db_path: str = ".lattice/orchestrator.duckdb",
    ) -> None:
        self._soul_dir = soul_dir
        self._db_path = db_path
        self._reader = SoulReader(soul_dir)
        self._db_conn: duckdb.DuckDBPyConnection | None = None
        self._backend: Any | None = None
        self._db_lock = threading.Lock()

    @property
    def db_conn(self) -> duckdb.DuckDBPyConnection | None:
        """Expose the DB connection for subsystems that need it."""
        return self._db_conn

    async def initialize(self) -> None:
        """Open database connection and initialize terminal backend.

        All blocking operations are dispatched to the executor so the
        event loop is never blocked.  Safe to call multiple times.
        """
        if self._db_conn is None:
            loop = asyncio.get_running_loop()

            def _open_db() -> duckdb.DuckDBPyConnection:
                db_file = Path(self._db_path)
                db_file.parent.mkdir(parents=True, exist_ok=True)
                conn = duckdb.connect(str(db_file))
                init_events_table(conn)
                return conn

            self._db_conn = await loop.run_in_executor(None, _open_db)
            log.info("dashboard_service.db_connected", path=self._db_path)

        if self._backend is None:
            try:
                from lattice.orchestrator.terminal.tmux import TmuxBackend
                self._backend = TmuxBackend()
            except RuntimeError:
                log.warning("dashboard_service.no_tmux")

    async def close(self) -> None:
        """Close database connection and release resources."""
        if self._db_conn is not None:
            loop = asyncio.get_running_loop()
            conn = self._db_conn
            self._db_conn = None
            await loop.run_in_executor(None, conn.close)

    async def detect_instances(self) -> list[CCInstance]:
        """Detect live CC instances from tmux."""
        if self._backend is None:
            return []
        try:
            return await self._backend.detect_cc_panes()
        except Exception as exc:
            log.error("dashboard_service.detect_failed", error=str(exc))
            return []

    async def capture_pane_output(
        self, pane_id: str, lines: int = 50
    ) -> list[str]:
        """Capture recent output from a tmux pane."""
        if self._backend is None:
            return []
        try:
            return await self._backend.capture_output(pane_id, lines=lines)
        except Exception as exc:
            log.error(
                "dashboard_service.capture_failed",
                pane_id=pane_id,
                error=str(exc),
            )
            return []

    async def read_soul_state(self) -> OrchestratorState:
        """Read and parse STATE.md into structured state."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._reader.read_state)

    async def read_memory(
        self, category: str | None = None
    ) -> list[SoulMemoryEntry]:
        """Read memory entries, optionally filtered by category."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._reader.query_memory, category
        )

    async def read_recent_events(
        self,
        limit: int = 50,
        session_id: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query recent events from DuckDB.

        Dispatched to executor with threading.Lock to avoid blocking
        the event loop and to serialize DuckDB access.
        """
        if self._db_conn is None:
            return []

        loop = asyncio.get_running_loop()

        def _query() -> list[dict[str, Any]]:
            with self._db_lock:
                return get_history(
                    self._db_conn,
                    session_id=session_id,
                    event_type=event_type,
                    limit=limit,
                )

        try:
            return await loop.run_in_executor(None, _query)
        except Exception as exc:
            log.error("dashboard_service.events_failed", error=str(exc))
            return []

    async def process_text_command(self, text: str) -> dict[str, Any]:
        """Process a text command through the voice pipeline.

        Lazily creates the pipeline on first call.  Reuses the same
        IntentRouter and VoicePipeline across calls to avoid per-call
        initialization overhead.

        Args:
            text: Command text to classify and route.

        Returns:
            Dict with action, detail, and success keys.
        """
        try:
            from lattice.orchestrator.voice.models import VoiceConfig
            from lattice.orchestrator.voice.pipeline import VoicePipeline
            from lattice.orchestrator.voice.router import IntentRouter

            if not hasattr(self, "_voice_pipeline") or self._voice_pipeline is None:
                router = IntentRouter(db_conn=self._db_conn)
                self._voice_pipeline = VoicePipeline(
                    config=VoiceConfig(), router=router
                )

            result = await self._voice_pipeline.process_text_async(text)
            return {
                "action": result.action,
                "detail": result.detail,
                "success": result.success,
            }
        except Exception as exc:
            return {
                "action": "error",
                "detail": str(exc),
                "success": False,
            }

    async def poll_full_snapshot(
        self, capture_lines: int = 40
    ) -> DashboardSnapshot:
        """Assemble a complete dashboard snapshot from all sources.

        Runs all polls concurrently for minimum latency.  Returns
        an immutable snapshot with tuple/MappingProxyType collections.
        """
        instances_task = asyncio.create_task(self.detect_instances())
        state_task = asyncio.create_task(self.read_soul_state())
        memory_task = asyncio.create_task(self.read_memory())
        events_task = asyncio.create_task(self.read_recent_events(limit=50))

        instances, state, memory, events = await asyncio.gather(
            instances_task, state_task, memory_task, events_task
        )

        captured: dict[str, tuple[str, ...]] = {}
        if instances:
            capture_tasks = [
                self.capture_pane_output(inst.pane_id, lines=capture_lines)
                for inst in instances
            ]
            capture_results = await asyncio.gather(*capture_tasks)
            for inst, output in zip(instances, capture_results):
                captured[inst.pane_id] = tuple(output)

        return DashboardSnapshot(
            instances=tuple(instances),
            soul_state=state,
            memory_entries=tuple(memory),
            recent_events=tuple(events),
            captured_output=MappingProxyType(captured),
        )
