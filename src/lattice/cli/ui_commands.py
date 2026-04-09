"""CLI commands for the Lattice dashboard.

Commands:
    ui:dashboard — Launch the native desktop dashboard (pywebview).
                   Falls back to the Textual TUI with --tui.
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
    "--sock-path",
    default=None,
    help="Override orchestrator UDS socket path (default: ~/.lattice/orchestrator.sock).",
)
@click.option(
    "--tui",
    is_flag=True,
    default=False,
    help="Use the legacy Textual TUI instead of the native desktop window.",
)
@click.pass_context
def ui_dashboard(
    ctx: click.Context,
    cols: int,
    interactive: bool,
    soul_dir: str | None,
    sock_path: str | None,
    tui: bool,
) -> None:
    """Launch the Lattice dashboard.

    Opens a native desktop window with a live terminal grid of Claude Code
    instances alongside a sidebar with voice controls, soul ecosystem state,
    and event log.

    Use --tui to fall back to the terminal-based Textual dashboard.

    Requires a running tmux server with Claude Code instances.
    Events are streamed from the orchestrator's UDS socket when available.
    """
    project_root = Path.cwd()
    soul_path = Path(soul_dir) if soul_dir else project_root / ".lattice" / "soul"

    if cols < 1 or cols > 9:
        click.echo("Error: --cols must be between 1 and 9", err=True)
        ctx.exit(1)
        return

    resolved_sock = Path(sock_path) if sock_path else None

    if tui:
        from lattice.ui.app import LatticeDashboard

        app = LatticeDashboard(
            soul_dir=soul_path,
            sock_path=resolved_sock,
            columns=cols,
            interactive=interactive,
        )
        app.run()
    else:
        from lattice.ui.webview_app import launch_dashboard

        launch_dashboard(
            soul_dir=soul_path,
            sock_path=resolved_sock,
            columns=cols,
            interactive=interactive,
        )
