"""Live event stream panel for the dashboard sidebar.

Displays recent CCEvents from the event server in a scrollable log
with color-coded event types and timestamps.
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import RichLog, Static


_EVENT_TYPE_COLORS: dict[str, str] = {
    "PreToolUse": "cyan",
    "PostToolUse": "green",
    "SessionStart": "magenta",
    "Stop": "red",
    "Notification": "yellow",
}


class EventLog(Widget):
    """Sidebar panel showing a live stream of orchestrator events.

    Events are displayed with color-coded types, timestamps, and
    truncated details.
    """

    DEFAULT_CSS = """
    EventLog {
        height: auto;
        max-height: 30%;
        width: 100%;
        padding: 0;
    }

    EventLog .events-header {
        dock: top;
        height: 1;
        background: $warning-darken-2;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    EventLog .events-content {
        height: 1fr;
        min-height: 4;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(" \u26a1 Events", classes="events-header")
        yield VerticalScroll(
            RichLog(
                id="event-log",
                highlight=True,
                markup=True,
                wrap=True,
                max_lines=200,
            ),
            classes="events-content",
        )

    @property
    def event_log(self) -> RichLog:
        """Access the event log widget."""
        return self.query_one("#event-log", RichLog)

    def update_events(self, events: list[dict[str, Any]]) -> None:
        """Replace displayed events with a fresh list.

        Args:
            events: List of event dicts from DuckDB history query.
                    Expected keys: event_type, tool_name, session_id,
                    timestamp, tool_input.
        """
        log = self.event_log
        log.clear()

        if not events:
            log.write("[dim]No events recorded[/dim]")
            return

        for event in events[-30:]:
            event_type = event.get("event_type", "unknown")
            color = _EVENT_TYPE_COLORS.get(event_type, "white")
            tool = event.get("tool_name", "")
            session = event.get("session_id", "")[:8]
            timestamp = str(event.get("timestamp", ""))[:19]

            tool_display = f" [bold]{tool}[/bold]" if tool else ""
            log.write(
                f"[dim]{timestamp}[/dim] "
                f"[{color}]{event_type}[/{color}]"
                f"{tool_display} "
                f"[dim]({session})[/dim]"
            )
