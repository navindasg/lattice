"""Tests for the TerminalBackend abstract base class.

Covers:
- ABC cannot be instantiated directly
- Concrete subclass implementing all methods can be instantiated
- Subclass missing a method cannot be instantiated
"""
from __future__ import annotations

import pytest

from lattice.orchestrator.terminal.base import TerminalBackend
from lattice.orchestrator.terminal.models import CCInstance, PaneInfo


class TestTerminalBackendABC:
    """TerminalBackend ABC contract tests."""

    def test_cannot_instantiate_abc(self):
        """Direct instantiation of the ABC raises TypeError."""
        with pytest.raises(TypeError):
            TerminalBackend()  # type: ignore[abstract]

    def test_complete_subclass_can_instantiate(self):
        """A subclass implementing every abstract method is valid."""

        class StubBackend(TerminalBackend):
            async def send_text(self, pane_id: str, text: str) -> None:
                pass

            async def send_enter(self, pane_id: str) -> None:
                pass

            async def send_interrupt(self, pane_id: str) -> None:
                pass

            async def capture_output(
                self, pane_id: str, lines: int = 50
            ) -> list[str]:
                return []

            async def list_panes(self) -> list[PaneInfo]:
                return []

            async def spawn_pane(
                self, command: str, name: str | None = None
            ) -> str:
                return "%0"

            async def close_pane(self, pane_id: str) -> None:
                pass

            async def detect_cc_panes(self) -> list[CCInstance]:
                return []

        backend = StubBackend()
        assert isinstance(backend, TerminalBackend)

    def test_incomplete_subclass_cannot_instantiate(self):
        """A subclass missing at least one method raises TypeError."""

        class PartialBackend(TerminalBackend):
            async def send_text(self, pane_id: str, text: str) -> None:
                pass

            # Missing all other abstract methods

        with pytest.raises(TypeError):
            PartialBackend()  # type: ignore[abstract]
