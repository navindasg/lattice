"""Backend service layer for the Lattice TUI dashboard.

Provides async polling services that decouple UI widgets from direct
backend calls.  Each service method is a coroutine that can be called
from Textual workers or timers.

Event data is fetched via HTTP over the orchestrator's Unix domain
socket (EventServer API), not by opening DuckDB directly.  This avoids
lock conflicts with the running orchestrator process.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx
import structlog

from lattice.orchestrator.soul_ecosystem.models import (
    OrchestratorState,
    SoulMemoryEntry,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.terminal.models import CCInstance

log = structlog.get_logger(__name__)

_DEFAULT_SOCK_PATH = Path.home() / ".lattice" / "orchestrator.sock"


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

    Reads events from the orchestrator's EventServer via HTTP over
    its Unix domain socket.  Reads soul state from the filesystem.
    Reads terminal state from tmux via the TmuxBackend.
    """

    def __init__(
        self,
        soul_dir: Path,
        sock_path: Path | None = None,
    ) -> None:
        self._soul_dir = soul_dir
        self._sock_path = sock_path or _DEFAULT_SOCK_PATH
        self._reader = SoulReader(soul_dir)
        self._backend: Any | None = None
        self._http: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        """Initialize HTTP client and terminal backend.

        The HTTP client connects to the orchestrator's UDS socket
        for event queries.  If the socket isn't available yet, events
        will gracefully return empty until it appears.

        Safe to call multiple times — idempotent.
        """
        if self._http is None:
            transport = httpx.AsyncHTTPTransport(uds=str(self._sock_path))
            self._http = httpx.AsyncClient(
                transport=transport,
                base_url="http://lattice-orchestrator",
                timeout=5.0,
            )
            log.info(
                "dashboard_service.http_client_ready",
                socket=str(self._sock_path),
            )

        if self._backend is None:
            # Import and construct TmuxBackend in the executor thread.
            # libtmux reads sys.stdout.encoding at import time, which
            # fails under Textual's _PrintCapture wrapper on the main
            # thread.  Running in the executor avoids this.
            def _init_tmux():
                from lattice.orchestrator.terminal.tmux import TmuxBackend
                return TmuxBackend()

            try:
                loop = asyncio.get_running_loop()
                self._backend = await loop.run_in_executor(None, _init_tmux)
            except (RuntimeError, AttributeError):
                log.warning("dashboard_service.no_tmux")

    async def close(self) -> None:
        """Close HTTP client and release resources."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

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
        """Query recent events via the EventServer HTTP API over UDS.

        Hits GET /events/history on the orchestrator's Unix domain socket.
        Returns empty list if the orchestrator isn't running or the socket
        doesn't exist yet.
        """
        if self._http is None:
            return []

        params: dict[str, str | int] = {"limit": limit}
        if session_id is not None:
            params["session_id"] = session_id
        if event_type is not None:
            params["event_type"] = event_type

        try:
            resp = await self._http.get("/events/history", params=params)
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.TimeoutException):
            # Orchestrator not running yet — this is expected during startup
            return []
        except Exception as exc:
            log.error("dashboard_service.events_failed", error=str(exc))
            return []

    async def read_health(self) -> HealthSnapshot:
        """Query health metrics via the EventServer HTTP API over UDS."""
        if self._http is None:
            return HealthSnapshot()

        try:
            resp = await self._http.get("/health")
            resp.raise_for_status()
            data = resp.json()
            return HealthSnapshot(
                uptime_seconds=data.get("uptime_seconds", 0.0),
                connected_sessions=data.get("connected_sessions", 0),
                pending_events=data.get("pending_events", 0),
            )
        except (httpx.ConnectError, httpx.TimeoutException):
            return HealthSnapshot()
        except Exception as exc:
            log.error("dashboard_service.health_failed", error=str(exc))
            return HealthSnapshot()

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
                router = IntentRouter()
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
        health_task = asyncio.create_task(self.read_health())

        instances, state, memory, events, health = await asyncio.gather(
            instances_task, state_task, memory_task, events_task, health_task
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
            health=health,
            captured_output=MappingProxyType(captured),
        )
