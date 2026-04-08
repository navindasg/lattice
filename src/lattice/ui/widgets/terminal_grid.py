"""Configurable N-column grid of TerminalPane widgets.

Auto-populates from detected CC instances and handles dynamic
add/remove as instances appear or disappear.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from lattice.orchestrator.terminal.models import CCInstance
from lattice.ui.widgets.terminal_pane import TerminalPane


class TerminalGrid(Widget):
    """Grid layout of TerminalPane widgets for live CC instance monitoring.

    Attributes:
        columns: Number of columns in the grid layout.
    """

    DEFAULT_CSS = """
    TerminalGrid {
        layout: grid;
        height: 1fr;
        width: 1fr;
        padding: 0;
    }

    TerminalGrid .grid-empty {
        content-align: center middle;
        height: 1fr;
        width: 1fr;
        color: $text-muted;
        text-style: italic;
    }
    """

    columns: reactive[int] = reactive(3)

    def __init__(self, columns: int = 3, **kwargs) -> None:
        super().__init__(**kwargs)
        self.columns = columns
        self._pane_map: dict[str, TerminalPane] = {}

    def compose(self) -> ComposeResult:
        yield Static(
            "No CC instances detected. Start Claude Code in tmux.",
            classes="grid-empty",
            id="grid-placeholder",
        )

    def watch_columns(self) -> None:
        """Update CSS grid template when column count changes."""
        self.styles.grid_size_columns = self.columns

    def on_mount(self) -> None:
        """Set initial grid columns on mount."""
        self.styles.grid_size_columns = self.columns

    def sync_instances(
        self,
        instances: list[CCInstance],
        captured_output: dict[str, list[str]],
    ) -> None:
        """Synchronize displayed panes with live instance list.

        Adds new panes, removes stale ones, and updates output for
        all active panes.

        Args:
            instances: Current list of detected CC instances.
            captured_output: Map of pane_id -> captured terminal lines.
        """
        current_ids = {inst.pane_id for inst in instances}
        existing_ids = set(self._pane_map.keys())

        # Remove placeholder if we have instances
        placeholder = self.query("#grid-placeholder")
        if instances and placeholder:
            for p in placeholder:
                p.remove()

        # Remove stale panes
        stale_ids = existing_ids - current_ids
        for pane_id in stale_ids:
            pane = self._pane_map.pop(pane_id, None)
            if pane is not None:
                pane.remove()

        # Add new panes
        for inst in instances:
            if inst.pane_id not in self._pane_map:
                pane = TerminalPane(
                    pane_id=inst.pane_id,
                    user_number=inst.user_number,
                    cwd=inst.cwd,
                    id=f"pane-{inst.pane_id.replace('%', 'p')}",
                )
                self._pane_map[inst.pane_id] = pane
                self.mount(pane)

        # Update output for all panes
        for pane_id, pane in self._pane_map.items():
            lines = captured_output.get(pane_id, [])
            pane.update_output(lines)

        # Re-show placeholder if no instances
        if not instances and not self.query("#grid-placeholder"):
            self.mount(
                Static(
                    "No CC instances detected. Start Claude Code in tmux.",
                    classes="grid-empty",
                    id="grid-placeholder",
                )
            )

    def find_pane_by_number(self, user_number: int) -> TerminalPane | None:
        """Look up a TerminalPane by its user-facing instance number.

        Args:
            user_number: The stable CC instance number (1-9).

        Returns:
            The matching TerminalPane, or None if not found.
        """
        for pane in self._pane_map.values():
            if pane.user_number == user_number:
                return pane
        return None
