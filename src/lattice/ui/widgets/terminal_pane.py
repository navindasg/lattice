"""Single CC instance terminal output viewer widget.

Displays captured tmux pane output with ANSI rendering in a scrollable
container.  Updated externally by the app's polling loop.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class TerminalPane(Widget):
    """Displays captured terminal output for a single CC instance.

    Attributes:
        pane_id: The tmux pane identifier (e.g. ``%0``).
        user_number: Stable user-facing instance number (1-9).
        instance_cwd: Working directory of the CC instance.
    """

    DEFAULT_CSS = """
    TerminalPane {
        border: solid $surface-lighten-2;
        height: 1fr;
        width: 1fr;
        overflow-y: auto;
    }

    TerminalPane.--focused {
        border: solid $accent;
    }

    TerminalPane .pane-header {
        dock: top;
        height: 1;
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    TerminalPane .pane-output {
        padding: 0 1;
    }
    """

    pane_id: reactive[str] = reactive("")
    user_number: reactive[int] = reactive(0)
    instance_cwd: reactive[str] = reactive("")

    def __init__(
        self,
        pane_id: str,
        user_number: int,
        cwd: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.pane_id = pane_id
        self.user_number = user_number
        self.instance_cwd = cwd

    def compose(self) -> ComposeResult:
        yield Static(self._header_text(), classes="pane-header")
        yield VerticalScroll(
            Static("Waiting for output...", id="pane-content"),
            classes="pane-output",
        )

    def _header_text(self) -> str:
        """Build the header label from instance metadata."""
        cwd_display = self.instance_cwd.split("/")[-1] if self.instance_cwd else "..."
        return f" CC #{self.user_number}  {self.pane_id}  {cwd_display}"

    def update_output(self, lines: list[str]) -> None:
        """Replace displayed output with new captured lines.

        Args:
            lines: Raw terminal output lines (may contain ANSI escapes).
        """
        content = self.query_one("#pane-content", Static)
        rendered = "\n".join(lines) if lines else "(no output)"
        content.update(rendered)

    def watch_user_number(self) -> None:
        """Refresh header when user_number changes."""
        try:
            header = self.query_one(".pane-header", Static)
            header.update(self._header_text())
        except Exception:
            pass

    def watch_instance_cwd(self) -> None:
        """Refresh header when cwd changes."""
        try:
            header = self.query_one(".pane-header", Static)
            header.update(self._header_text())
        except Exception:
            pass
