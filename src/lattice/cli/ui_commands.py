"""CLI commands for the Lattice TUI dashboard.

Commands:
    ui:dashboard — Launch the Textual-based terminal dashboard
"""
from __future__ import annotations

from pathlib import Path

import click


@click.command("ui:dashboard")
@click.option(
    "--cols",
    default=3,
    type=int,
    help="Number of terminal grid columns (default: 3).",
    show_default=True,
)
@click.option(
    "--interactive",
    is_flag=True,
    default=False,
    help="Enable sending input to terminal panes (default: read-only).",
)
@click.option(
    "--soul-dir",
    default=None,
    help="Override soul directory path (default: .lattice/soul/).",
)
@click.option(
    "--db-path",
    default=".lattice/orchestrator.duckdb",
    help="DuckDB database path.",
    show_default=True,
)
@click.pass_context
def ui_dashboard(
    ctx: click.Context,
    cols: int,
    interactive: bool,
    soul_dir: str | None,
    db_path: str,
) -> None:
    """Launch the Lattice TUI dashboard.

    Displays a live terminal grid of Claude Code instances alongside
    a sidebar with voice controls, soul ecosystem state, and event log.

    Requires a running tmux server with Claude Code instances.
    The orchestrator does NOT need to be running — the dashboard reads
    state directly from tmux and soul files.
    """
    project_root = Path.cwd()
    soul_path = Path(soul_dir) if soul_dir else project_root / ".lattice" / "soul"

    if cols < 1 or cols > 9:
        click.echo("Error: --cols must be between 1 and 9", err=True)
        ctx.exit(1)
        return

    from lattice.ui.app import LatticeDashboard

    app = LatticeDashboard(
        soul_dir=soul_path,
        db_path=db_path,
        columns=cols,
        interactive=interactive,
    )
    app.run()
