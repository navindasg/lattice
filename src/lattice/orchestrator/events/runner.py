"""UDS server lifecycle for the event channel.

EventServer manages a uvicorn instance serving the FastAPI app over a
Unix domain socket. Handles startup, shutdown, stale socket cleanup,
and spool drain on boot.
"""
from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import duckdb
import structlog
import uvicorn

from lattice.orchestrator.events.persistence import init_events_table
from lattice.orchestrator.events.server import create_app
from lattice.orchestrator.events.spool import drain_spool

log = structlog.get_logger(__name__)

_DEFAULT_SOCK_PATH = Path.home() / ".lattice" / "orchestrator.sock"


class EventServer:
    """Manages the lifecycle of the event channel UDS server.

    Args:
        db_conn: An open DuckDB connection for event persistence.
        sock_path: Unix socket path (default ~/.lattice/orchestrator.sock).
        spool_file: Override spool file path (for testing).
        approval_timeout: Seconds to wait for approval decisions (default 30).
    """

    def __init__(
        self,
        db_conn: duckdb.DuckDBPyConnection,
        sock_path: Path | None = None,
        *,
        spool_file: Path | None = None,
        approval_timeout: float = 30.0,
    ) -> None:
        self._db_conn = db_conn
        self._sock_path = sock_path or _DEFAULT_SOCK_PATH
        self._spool_file = spool_file
        self._approval_timeout = approval_timeout
        self._app = create_app(db_conn, approval_timeout=approval_timeout)
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task | None = None

    @property
    def app(self):
        """The underlying FastAPI application."""
        return self._app

    @property
    def is_serving(self) -> bool:
        """True if the background server task is still running."""
        if self._serve_task is None:
            return False
        return not self._serve_task.done()

    @property
    def serve_error(self) -> BaseException | None:
        """Return the exception from the server task, or None if healthy."""
        if self._serve_task is None or not self._serve_task.done():
            return None
        if self._serve_task.cancelled():
            return None
        return self._serve_task.exception()

    async def start(self) -> asyncio.Queue:
        """Start the UDS server and drain any spooled events.

        Removes stale socket files from previous crashes, drains the
        spool into the database and queue, then starts uvicorn.

        Returns:
            The event queue for orchestrator consumption.
        """
        # Clean stale socket
        if self._sock_path.exists():
            log.warning("removing_stale_socket", path=str(self._sock_path))
            self._sock_path.unlink()

        # Ensure parent dir exists
        self._sock_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure DB table exists
        init_events_table(self._db_conn)

        # Drain spool
        event_queue = self._app.state.event_queue
        drained = drain_spool(
            self._db_conn,
            event_queue,
            spool_file=self._spool_file,
        )
        if drained > 0:
            log.info("spool_events_recovered", count=drained)

        # Configure and start uvicorn
        config = uvicorn.Config(
            app=self._app,
            uds=str(self._sock_path),
            log_level="warning",
        )
        self._server = uvicorn.Server(config)

        # Run server in background task with error tracking
        self._serve_task = asyncio.create_task(self._server.serve())
        self._serve_task.add_done_callback(self._on_serve_done)

        # Wait briefly for the socket to appear
        for _ in range(50):
            if self._sock_path.exists():
                break
            await asyncio.sleep(0.05)

        log.info("event_server_started", socket=str(self._sock_path))
        return event_queue

    async def stop(self) -> None:
        """Shut down the server, remove the socket, and flush DuckDB.

        Safe to call multiple times.
        """
        if self._server is not None:
            self._server.should_exit = True
            # Give uvicorn a moment to finish
            await asyncio.sleep(0.1)
            self._server = None

        if self._sock_path.exists():
            self._sock_path.unlink()
            log.info("socket_removed", path=str(self._sock_path))

        if self._serve_task is not None and not self._serve_task.done():
            self._serve_task.cancel()
            self._serve_task = None

        log.info("event_server_stopped")

    @staticmethod
    def _on_serve_done(task: asyncio.Task) -> None:
        """Log errors from the background server task."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("event_server_crashed", error=str(exc))
