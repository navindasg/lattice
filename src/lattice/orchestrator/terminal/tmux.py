"""Tmux terminal backend using libtmux.

.. deprecated::
    The TmuxBackend is deprecated in favor of :class:`lattice.ui.pty_manager.PTYManager`,
    which provides direct PTY management without requiring an external tmux server.
    TmuxBackend will be removed in a future release.

All public methods are async.  Synchronous libtmux calls are dispatched
to a thread-pool executor so the event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import re
import warnings
from functools import partial
from typing import Any

import structlog

from lattice.orchestrator.terminal.base import TerminalBackend
from lattice.orchestrator.terminal.models import CCInstance, PaneInfo

log = structlog.get_logger(__name__)

_PANE_ID_RE = re.compile(r"^%\d+$")


def _validate_pane_id(pane_id: str) -> None:
    """Validate that pane_id matches the tmux ``%N`` format."""
    if not _PANE_ID_RE.match(pane_id):
        raise ValueError(f"Invalid tmux pane_id: {pane_id!r}")


def _get_server() -> Any:
    """Create a libtmux Server, raising RuntimeError when unavailable."""
    import libtmux  # late import so the module is importable without tmux

    try:
        server = libtmux.Server()
        # Force a connection check — list_sessions raises if no server.
        server.sessions  # noqa: B018
    except Exception as exc:
        raise RuntimeError(
            "No tmux server found. Start tmux first."
        ) from exc
    return server


class TmuxBackend(TerminalBackend):
    """Tmux implementation of :class:`TerminalBackend` via *libtmux*.

    .. deprecated::
        Use :class:`lattice.ui.pty_manager.PTYManager` instead.
        TmuxBackend will be removed in a future release.

    Maintains a stable numbering map so user-facing CC instance numbers
    persist across successive ``detect_cc_panes`` calls.  Numbers are
    never reused until :meth:`rescan` is called explicitly.
    """

    def __init__(self) -> None:
        warnings.warn(
            "TmuxBackend is deprecated. Use lattice.ui.pty_manager.PTYManager instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._server = _get_server()
        self._pane_number_map: dict[str, int] = {}
        self._next_number: int = 1
        log.info("tmux_backend.init", sessions=len(self._server.sessions))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_in_executor(self, fn: Any, *args: Any) -> Any:
        """Schedule *fn* on the default executor and return a coroutine."""
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(None, partial(fn, *args))

    def _find_pane(self, pane_id: str) -> Any:
        """Locate a pane object by its tmux pane id (e.g. ``%0``)."""
        for session in self._server.sessions:
            for window in session.windows:
                for pane in window.panes:
                    if pane.pane_id == pane_id:
                        return pane
        raise ValueError(f"Pane not found: {pane_id}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_text(self, pane_id: str, text: str) -> None:
        """Send literal text to *pane_id* via ``tmux send-keys -l``."""
        _validate_pane_id(pane_id)
        proc = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", "-t", pane_id, "-l", text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"tmux send-keys failed for {pane_id}: {stderr.decode().strip()}"
            )
        log.debug("tmux.send_text", pane_id=pane_id, length=len(text))

    async def send_enter(self, pane_id: str) -> None:
        """Send Enter key to *pane_id*."""
        _validate_pane_id(pane_id)
        proc = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", "-t", pane_id, "Enter",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"tmux send-keys Enter failed for {pane_id}: "
                f"{stderr.decode().strip()}"
            )
        log.debug("tmux.send_enter", pane_id=pane_id)

    async def send_interrupt(self, pane_id: str) -> None:
        """Send Ctrl+C to *pane_id*."""
        _validate_pane_id(pane_id)
        proc = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", "-t", pane_id, "C-c",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"tmux send-keys C-c failed for {pane_id}: "
                f"{stderr.decode().strip()}"
            )
        log.debug("tmux.send_interrupt", pane_id=pane_id)

    async def capture_output(
        self, pane_id: str, lines: int = 50
    ) -> list[str]:
        """Capture the last *lines* lines from *pane_id*."""
        _validate_pane_id(pane_id)

        def _capture() -> list[str]:
            pane = self._find_pane(pane_id)
            captured: list[str] = pane.capture_pane()
            return captured[-lines:]

        result: list[str] = await self._run_in_executor(_capture)
        log.debug(
            "tmux.capture_output",
            pane_id=pane_id,
            requested=lines,
            returned=len(result),
        )
        return result

    async def list_panes(self) -> list[PaneInfo]:
        """List all panes across every tmux session and window."""
        def _list() -> list[PaneInfo]:
            panes: list[PaneInfo] = []
            for session in self._server.sessions:
                for window in session.windows:
                    for pane in window.panes:
                        panes.append(
                            PaneInfo(
                                pane_id=pane.pane_id,
                                session_name=session.session_name,
                                window_name=window.window_name,
                                pane_index=int(pane.pane_index),
                                running_command=pane.pane_current_command,
                                cwd=pane.pane_current_path,
                            )
                        )
            return panes

        result = await self._run_in_executor(_list)
        log.debug("tmux.list_panes", count=len(result))
        return result

    async def spawn_pane(
        self, command: str, name: str | None = None
    ) -> str:
        """Split a new pane running *command* and return its pane_id.

        Uses ``tmux split-window`` via subprocess with ``-P -F '#{pane_id}'``
        to reliably capture the new pane's id even for short-lived commands.
        """
        args = [
            "tmux", "split-window",
            "-P", "-F", "#{pane_id}",
            command,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"tmux split-window failed: {stderr.decode().strip()}"
            )
        pane_id = stdout.decode().strip()

        if name is not None:
            await asyncio.create_subprocess_exec(
                "tmux", "rename-window", "-t", pane_id, name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

        log.info("tmux.spawn_pane", pane_id=pane_id, command=command, name=name)
        return pane_id

    async def close_pane(self, pane_id: str) -> None:
        """Kill the pane identified by *pane_id*."""
        _validate_pane_id(pane_id)
        proc = await asyncio.create_subprocess_exec(
            "tmux", "kill-pane", "-t", pane_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"tmux kill-pane failed for {pane_id}: "
                f"{stderr.decode().strip()}"
            )
        log.info("tmux.close_pane", pane_id=pane_id)

    async def detect_cc_panes(self) -> list[CCInstance]:
        """Detect panes whose running command contains ``claude``."""
        panes = await self.list_panes()
        instances: list[CCInstance] = []
        for pane in panes:
            if "claude" not in pane.running_command.lower():
                continue
            user_number = self._pane_number_map.get(pane.pane_id)
            if user_number is None:
                user_number = self._next_number
                self._pane_number_map[pane.pane_id] = user_number
                self._next_number += 1
            instances.append(
                CCInstance(
                    pane_id=pane.pane_id,
                    session_name=pane.session_name,
                    window_name=pane.window_name,
                    user_number=user_number,
                    running_command=pane.running_command,
                    cwd=pane.cwd,
                )
            )
        log.debug("tmux.detect_cc_panes", count=len(instances))
        return instances

    def rescan(self) -> None:
        """Clear the stable numbering map so numbers are reassigned."""
        self._pane_number_map.clear()
        self._next_number = 1
        log.info("tmux.rescan", message="numbering map cleared")
