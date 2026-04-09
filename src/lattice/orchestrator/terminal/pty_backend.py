"""Async TerminalBackend adapter wrapping PTYManager.

Provides the same async interface as TmuxBackend but uses direct PTY
management instead of requiring an external tmux server.  All blocking
PTYManager operations are dispatched to a thread-pool executor so the
async event loop is never stalled.

Usage::

    from lattice.ui.pty_manager import PTYManager
    from lattice.orchestrator.terminal.pty_backend import PTYBackend

    pty_mgr = PTYManager(on_output=..., on_exit=...)
    backend = PTYBackend(pty_mgr)
    pane_id = await backend.spawn_pane("claude", name="cc-1")
    await backend.send_text(pane_id, "hello")
"""
from __future__ import annotations

import asyncio
import shlex
from typing import Any

import structlog

from lattice.orchestrator.terminal.base import TerminalBackend
from lattice.orchestrator.terminal.models import CCInstance, PaneInfo
from lattice.ui.pty_manager import PTYManager, TerminalStatus

log = structlog.get_logger(__name__)


class PTYBackend(TerminalBackend):
    """Async terminal backend backed by PTYManager.

    Wraps the synchronous PTYManager with async methods matching
    the TerminalBackend interface.  Maintains stable user-facing
    numbering for CC instances, mirroring TmuxBackend behavior.
    """

    def __init__(self, pty_manager: PTYManager) -> None:
        self._pty = pty_manager
        self._pane_commands: dict[str, str] = {}
        self._pane_number_map: dict[str, int] = {}
        self._next_number: int = 1
        log.info("pty_backend.init")

    @property
    def pty_manager(self) -> PTYManager:
        """Access the underlying PTYManager (e.g. for callbacks)."""
        return self._pty

    async def _run(self, fn: Any, *args: Any) -> Any:
        """Run a blocking function in the thread-pool executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)

    async def send_text(self, pane_id: str, text: str) -> None:
        """Send literal text to a terminal's PTY as typed input."""
        await self._run(self._pty.write, pane_id, text.encode("utf-8"))
        log.debug("pty_backend.send_text", pane_id=pane_id, length=len(text))

    async def send_enter(self, pane_id: str) -> None:
        """Send Enter key (newline) to a terminal."""
        await self._run(self._pty.write, pane_id, b"\n")
        log.debug("pty_backend.send_enter", pane_id=pane_id)

    async def send_interrupt(self, pane_id: str) -> None:
        """Send Ctrl+C (SIGINT character) to a terminal."""
        await self._run(self._pty.write, pane_id, b"\x03")
        log.debug("pty_backend.send_interrupt", pane_id=pane_id)

    async def capture_output(self, pane_id: str, lines: int = 50) -> list[str]:
        """Capture output is not supported for PTY terminals.

        PTY output is delivered via push callbacks, not polling.
        Returns an empty list for compatibility.
        """
        return []

    async def list_panes(self) -> list[PaneInfo]:
        """List all managed PTY terminals as PaneInfo objects."""
        terminals = await self._run(self._pty.list_terminals)
        return [
            PaneInfo(
                pane_id=t.pane_id,
                session_name="pty",
                window_name="pty",
                pane_index=0,
                running_command=" ".join(t.cmd),
                cwd=t.cwd,
            )
            for t in terminals
        ]

    async def spawn_pane(
        self, command: str, name: str | None = None
    ) -> str:
        """Spawn a new terminal running the given shell command.

        Parses the command string into a list for subprocess.Popen.
        If the command contains ``cd <dir> && <cmd>``, extracts the
        directory as cwd and runs only the final command.

        Args:
            command: Shell command string (e.g. ``cd /proj && claude``).
            name: Optional label (stored but not displayed).

        Returns:
            The pane_id of the new terminal.
        """
        cwd = None
        cmd_str = command

        # Parse "cd /path && actual_command" pattern
        if "&&" in command:
            parts = command.split("&&", 1)
            cd_part = parts[0].strip()
            cmd_str = parts[1].strip()
            if cd_part.startswith("cd "):
                raw_path = cd_part[3:].strip()
                # Remove shell quoting
                try:
                    cwd = shlex.split(raw_path)[0]
                except ValueError:
                    cwd = raw_path.strip("'\"")

        try:
            cmd_list = shlex.split(cmd_str)
        except ValueError:
            cmd_list = [cmd_str]

        pane_id = await self._run(
            self._pty.spawn, cmd_list, cwd
        )

        self._pane_commands[pane_id] = command
        log.info(
            "pty_backend.spawn_pane",
            pane_id=pane_id,
            command=command,
            name=name,
        )
        return pane_id

    async def close_pane(self, pane_id: str) -> None:
        """Kill a terminal and remove it from tracking."""
        await self._run(self._pty.kill, pane_id)
        self._pane_commands.pop(pane_id, None)
        self._pane_number_map.pop(pane_id, None)
        log.info("pty_backend.close_pane", pane_id=pane_id)

    async def detect_cc_panes(self) -> list[CCInstance]:
        """Detect terminals running Claude Code.

        Checks all managed terminals for commands containing "claude".
        Maintains stable user_number assignments across calls.
        """
        terminals = await self._run(self._pty.list_terminals)
        instances: list[CCInstance] = []

        for t in terminals:
            if t.status != TerminalStatus.ALIVE:
                continue

            cmd_str = " ".join(t.cmd)
            # Also check the original spawn command
            original_cmd = self._pane_commands.get(t.pane_id, "")

            if "claude" not in cmd_str.lower() and "claude" not in original_cmd.lower():
                continue

            user_number = self._pane_number_map.get(t.pane_id)
            if user_number is None:
                user_number = self._next_number
                self._pane_number_map[t.pane_id] = user_number
                self._next_number += 1

            instances.append(
                CCInstance(
                    pane_id=t.pane_id,
                    session_name="pty",
                    window_name="pty",
                    user_number=user_number,
                    running_command=cmd_str,
                    cwd=t.cwd,
                )
            )

        log.debug("pty_backend.detect_cc_panes", count=len(instances))
        return instances

    def shutdown(self) -> None:
        """Shut down the underlying PTYManager."""
        self._pty.shutdown()
