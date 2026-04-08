"""Main Textual application for the Lattice TUI dashboard.

Composes all widgets into a two-column layout (sidebar + terminal grid)
with periodic polling to keep state fresh.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.timer import Timer
from textual.widgets import Footer, Static

from lattice.ui.services import DashboardService, DashboardSnapshot
from lattice.ui.widgets.event_log import EventLog
from lattice.ui.widgets.soul_panel import SoulPanel
from lattice.ui.widgets.terminal_grid import TerminalGrid
from lattice.ui.widgets.voice_panel import MicState, VoicePanel

log = structlog.get_logger(__name__)

# Polling interval in seconds for background refresh.
_POLL_INTERVAL: float = 1.0


class LatticeDashboard(App):
    """Lattice orchestrator TUI dashboard.

    Displays a terminal grid of CC instances alongside a sidebar with
    voice controls, soul state, and event log.

    Args:
        soul_dir: Path to the soul ecosystem directory.
        db_path: Path to the DuckDB database file.
        columns: Number of terminal grid columns.
        interactive: Whether to allow sending input to panes.
    """

    TITLE = "Lattice Dashboard"
    SUB_TITLE = "orchestrator terminal UI"

    CSS_PATH = "dashboard.css"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("t", "focus_text_input", "Text Input", show=True),
        Binding("v", "toggle_mic", "Mic Toggle", show=True),
        Binding("1", "focus_pane('1')", "CC #1", show=False),
        Binding("2", "focus_pane('2')", "CC #2", show=False),
        Binding("3", "focus_pane('3')", "CC #3", show=False),
        Binding("4", "focus_pane('4')", "CC #4", show=False),
        Binding("5", "focus_pane('5')", "CC #5", show=False),
        Binding("6", "focus_pane('6')", "CC #6", show=False),
        Binding("7", "focus_pane('7')", "CC #7", show=False),
        Binding("8", "focus_pane('8')", "CC #8", show=False),
        Binding("9", "focus_pane('9')", "CC #9", show=False),
    ]

    def __init__(
        self,
        soul_dir: Path,
        db_path: str = ".lattice/orchestrator.duckdb",
        columns: int = 3,
        interactive: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._service = DashboardService(soul_dir=soul_dir, db_path=db_path)
        self._columns = columns
        self._interactive = interactive
        self._last_snapshot: DashboardSnapshot | None = None
        self._recording = False
        self._poll_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            " \u25c8 Lattice Dashboard",
            id="title-bar",
        )
        with Container(id="sidebar"):
            yield VoicePanel(id="voice-panel")
            yield SoulPanel(id="soul-panel")
            yield EventLog(id="event-log-panel")
        with Container(id="main-content"):
            yield TerminalGrid(columns=self._columns, id="terminal-grid")
        yield Static(
            " \u23f3 Starting...",
            id="status-bar",
        )
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize backend service and start polling."""
        await self._service.initialize()
        self._poll_timer = self.set_interval(_POLL_INTERVAL, self._poll_backend)
        # Run first poll immediately
        self.run_worker(self._poll_backend())

    async def on_unmount(self) -> None:
        """Stop polling timer and clean up backend resources."""
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        await self._service.close()

    async def _poll_backend(self) -> None:
        """Poll all backend sources and update widgets."""
        try:
            snapshot = await self._service.poll_full_snapshot()
            self._last_snapshot = snapshot
            self._apply_snapshot(snapshot)
        except Exception as exc:
            log.error("dashboard.poll_failed", error=str(exc))
            try:
                status_bar = self.query_one("#status-bar", Static)
                status_bar.update(
                    f" [bold red]Poll error:[/bold red] {exc}"
                )
            except Exception:
                pass

    def _apply_snapshot(self, snap: DashboardSnapshot) -> None:
        """Push snapshot data into all dashboard widgets."""
        # Terminal grid
        grid = self.query_one("#terminal-grid", TerminalGrid)
        grid.sync_instances(list(snap.instances), dict(snap.captured_output))

        # Soul panel
        soul = self.query_one("#soul-panel", SoulPanel)
        soul.update_state(snap.soul_state, list(snap.memory_entries))

        # Event log
        events = self.query_one("#event-log-panel", EventLog)
        events.update_events(list(snap.recent_events))

        # Status bar
        instance_count = len(snap.instances)
        event_count = len(snap.recent_events)
        blocker_count = len(snap.soul_state.blockers)
        plan_count = len(snap.soul_state.plan)

        status_parts = [
            f"\u25c8 {instance_count} instance{'s' if instance_count != 1 else ''}",
            f"\u26a1 {event_count} events",
            f"\U0001f4cb {plan_count} plan items",
        ]
        if blocker_count > 0:
            status_parts.append(f"\u26a0 {blocker_count} blockers")

        mode = "interactive" if self._interactive else "read-only"
        status_parts.append(f"[{mode}]")

        status_bar = self.query_one("#status-bar", Static)
        status_bar.update(" " + "  \u2502  ".join(status_parts))

    # ── Actions ──────────────────────────────────────────────

    async def action_refresh(self) -> None:
        """Force an immediate backend poll."""
        await self._poll_backend()

    def action_focus_text_input(self) -> None:
        """Focus the voice panel text input."""
        try:
            voice = self.query_one("#voice-panel", VoicePanel)
            text_input = voice.query_one("#voice-text-input")
            text_input.focus()
        except Exception:
            pass

    def action_toggle_mic(self) -> None:
        """Toggle mic recording state."""
        voice = self.query_one("#voice-panel", VoicePanel)
        if self._recording:
            self._recording = False
            voice.set_mic_state(MicState.IDLE)
            voice.append_transcript("[dim]Recording stopped[/dim]")
        else:
            self._recording = True
            voice.set_mic_state(MicState.RECORDING)
            voice.append_transcript(
                "[bold red]Recording... press v or click to stop[/bold red]"
            )

    def action_focus_pane(self, number: str) -> None:
        """Focus a specific CC terminal pane by user number."""
        try:
            grid = self.query_one("#terminal-grid", TerminalGrid)
            pane = grid.find_pane_by_number(int(number))
            if pane is not None:
                pane.focus()
        except Exception:
            pass

    # ── Message Handlers ─────────────────────────────────────

    async def on_voice_panel_text_submitted(
        self, message: VoicePanel.TextSubmitted
    ) -> None:
        """Handle text command from the voice panel input."""
        voice = self.query_one("#voice-panel", VoicePanel)
        voice.set_mic_state(MicState.PROCESSING)
        voice.append_transcript(
            f"[bold cyan]>[/bold cyan] [italic]{message.text}[/italic]"
        )

        try:
            result = await self._service.process_text_command(message.text)
            voice.append_result(
                transcript=message.text,
                action=result.get("action", "unknown"),
                detail=result.get("detail", ""),
            )
        except Exception as exc:
            voice.append_transcript(
                f"[bold red]Error:[/bold red] {exc}"
            )
        finally:
            voice.set_mic_state(MicState.IDLE)

    def on_voice_panel_mic_toggled(self, _message: VoicePanel.MicToggled) -> None:
        """Handle mic button click — toggle recording state."""
        self.action_toggle_mic()
