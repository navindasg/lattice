"""Soul ecosystem state panel for the dashboard sidebar.

Displays structured OrchestratorState: instance assignments, plan items,
recent decisions, blockers, and memory entries.  Updated by the app's
polling loop.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import RichLog, Static

from lattice.orchestrator.soul_ecosystem.models import (
    OrchestratorState,
    SoulMemoryEntry,
)


class SoulPanel(Widget):
    """Sidebar panel displaying soul ecosystem state.

    Shows instance assignments, plan items, decisions, blockers,
    and recent memory entries from the soul ecosystem files.
    """

    DEFAULT_CSS = """
    SoulPanel {
        height: 1fr;
        width: 100%;
        padding: 0;
    }

    SoulPanel .soul-header {
        dock: top;
        height: 1;
        background: $secondary;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    SoulPanel .soul-content {
        height: 1fr;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(" \U0001f9e0 Soul State", classes="soul-header")
        yield VerticalScroll(
            RichLog(
                id="soul-log",
                highlight=True,
                markup=True,
                wrap=True,
            ),
            classes="soul-content",
        )

    @property
    def soul_log(self) -> RichLog:
        """Access the soul state log widget."""
        return self.query_one("#soul-log", RichLog)

    def update_state(
        self,
        state: OrchestratorState,
        memory: list[SoulMemoryEntry],
    ) -> None:
        """Replace the soul panel content with fresh state.

        Args:
            state: Parsed OrchestratorState from STATE.md.
            memory: List of recent memory entries from MEMORY.md.
        """
        log = self.soul_log
        log.clear()

        # Instances
        log.write("[bold underline]Instances[/bold underline]")
        if state.instances:
            for inst in state.instances:
                status_color = _status_color(inst.status)
                log.write(
                    f"  [{status_color}]\u25cf[/{status_color}] "
                    f"[bold]#{inst.instance_id}[/bold] {inst.task_description}"
                )
        else:
            log.write("  [dim]No active instances[/dim]")

        log.write("")

        # Plan
        log.write("[bold underline]Plan[/bold underline]")
        if state.plan:
            for i, item in enumerate(state.plan, 1):
                log.write(f"  {i}. {item}")
        else:
            log.write("  [dim]No current plan[/dim]")

        log.write("")

        # Blockers
        log.write("[bold underline]Blockers[/bold underline]")
        if state.blockers:
            for blocker in state.blockers:
                log.write(f"  [bold red]\u26a0[/bold red] {blocker}")
        else:
            log.write("  [dim green]\u2713 No blockers[/dim green]")

        log.write("")

        # Decisions (last 5)
        log.write("[bold underline]Recent Decisions[/bold underline]")
        if state.decisions:
            for dec in state.decisions[-5:]:
                icon = "\u2705" if dec.event_type == "approve" else "\u274c"
                reason = f" \u2014 {dec.reason}" if dec.reason else ""
                log.write(f"  {icon} {dec.target}{reason}")
        else:
            log.write("  [dim]No decisions recorded[/dim]")

        log.write("")

        # Memory (last 8)
        log.write("[bold underline]Memory[/bold underline]")
        if memory:
            for entry in memory[-8:]:
                log.write(
                    f"  [dim]{entry.timestamp[:16]}[/dim] "
                    f"[bold cyan][{entry.category}][/bold cyan] "
                    f"{entry.content}"
                )
        else:
            log.write("  [dim]No memory entries[/dim]")


def _status_color(status: str) -> str:
    """Map instance status to Rich color name."""
    colors = {
        "active": "green",
        "idle": "yellow",
        "blocked": "red",
    }
    return colors.get(status, "white")
