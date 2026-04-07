"""Abstract interface for terminal multiplexer backends.

Concrete implementations exist for tmux (via libtmux).  Future backends
for iTerm2 and Kitty can be added behind this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from lattice.orchestrator.terminal.models import CCInstance, PaneInfo


class TerminalBackend(ABC):
    """Abstract interface for terminal multiplexer backends.

    All methods are async so implementations can offload blocking I/O
    to a thread pool without stalling the event loop.
    """

    @abstractmethod
    async def send_text(self, pane_id: str, text: str) -> None:
        """Send text as typed input to a terminal pane."""
        ...

    @abstractmethod
    async def send_enter(self, pane_id: str) -> None:
        """Send Enter key to a terminal pane."""
        ...

    @abstractmethod
    async def send_interrupt(self, pane_id: str) -> None:
        """Send Ctrl+C interrupt to a terminal pane."""
        ...

    @abstractmethod
    async def capture_output(self, pane_id: str, lines: int = 50) -> list[str]:
        """Capture the last *lines* lines of visible output from a pane."""
        ...

    @abstractmethod
    async def list_panes(self) -> list[PaneInfo]:
        """List all panes across all sessions and windows."""
        ...

    @abstractmethod
    async def spawn_pane(
        self, command: str, name: str | None = None
    ) -> str:
        """Create a new pane running *command*.  Returns pane_id."""
        ...

    @abstractmethod
    async def close_pane(self, pane_id: str) -> None:
        """Kill a terminal pane."""
        ...

    @abstractmethod
    async def detect_cc_panes(self) -> list[CCInstance]:
        """Detect panes running Claude Code instances."""
        ...
