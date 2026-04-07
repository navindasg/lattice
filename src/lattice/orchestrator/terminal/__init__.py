"""Terminal backend: abstract interface and tmux implementation.

Provides :func:`create_backend` to auto-detect the running terminal
multiplexer and return the appropriate :class:`TerminalBackend`.
"""
from __future__ import annotations

import os

from lattice.orchestrator.terminal.base import TerminalBackend
from lattice.orchestrator.terminal.models import CCInstance, PaneInfo
from lattice.orchestrator.terminal.tmux import TmuxBackend


def create_backend() -> TerminalBackend:
    """Auto-detect and create the appropriate terminal backend.

    Checks ``$TMUX`` for tmux.  Future: ``$TERM_PROGRAM`` for iTerm2/Kitty.
    Raises :class:`RuntimeError` when no supported multiplexer is detected.
    """
    if os.environ.get("TMUX"):
        return TmuxBackend()
    raise RuntimeError(
        "No supported terminal multiplexer detected. "
        "Start a tmux session first: tmux new-session -s lattice"
    )


__all__ = [
    "TerminalBackend",
    "TmuxBackend",
    "PaneInfo",
    "CCInstance",
    "create_backend",
]
