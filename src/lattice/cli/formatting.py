"""Rich-based CLI output helpers for lattice commands.

All public functions print to the module-level console instance using Rich
formatting. Color conventions:
    green  — success, completed items
    red    — errors, failures
    yellow — warnings, stale items
    cyan   — informational, counts, paths

Exports:
    print_status          — render map:status output (pipeline progress + confidence)
    print_init_summary    — render map:init output (file count, languages, etc.)
    print_gaps_summary    — render map:gaps output (coverage table)
    print_cross_summary   — render map:cross output
    print_hint_stored     — confirm hint storage
    print_doc_wave        — wave progress line for map:doc
    print_doc_summary     — run summary table for map:doc
"""
from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)


# ---------------------------------------------------------------------------
# map:status
# ---------------------------------------------------------------------------


def print_status(status_data: dict) -> None:
    """Render map:status output with Rich tables.

    Args:
        status_data: Dict returned by _map_status_impl.
    """
    passes = status_data.get("passes_complete", {})
    distribution = status_data.get("confidence_distribution", {})
    token_summary = status_data.get("token_summary", {})
    directories_documented = status_data.get("directories_documented", 0)
    active_run_id = status_data.get("active_run_id")

    # --- Pipeline passes table ---
    passes_table = Table(title="Pipeline Passes", show_header=True, header_style="bold cyan")
    passes_table.add_column("Pass", style="bold")
    passes_table.add_column("Status")

    pass_labels = [
        ("map:init", "init"),
        ("map:gaps", "gaps"),
        ("map:doc", "doc"),
        ("map:cross", "cross"),
    ]
    for label, key in pass_labels:
        done = passes.get(key, False)
        status_text = Text("[complete]", style="green") if done else Text("[pending]", style="yellow")
        passes_table.add_row(label, status_text)

    console.print(passes_table)

    # --- Directories documented ---
    console.print(
        f"Directories documented: [cyan]{directories_documented}[/cyan]"
    )

    # --- Confidence distribution table ---
    dist_table = Table(title="Confidence Distribution", show_header=True, header_style="bold cyan")
    dist_table.add_column("Band", style="bold")
    dist_table.add_column("Count", justify="right")
    dist_table.add_column("Threshold")

    dist_rows = [
        ("low", "red", "< 0.5"),
        ("medium", "yellow", "0.5 – 0.79"),
        ("high", "green", "0.8 – 0.99"),
        ("developer_verified", "bright_green", "1.0 or source=developer"),
    ]
    for key, color, threshold in dist_rows:
        count = distribution.get(key, 0)
        dist_table.add_row(
            Text(key, style=color),
            str(count),
            threshold,
        )

    console.print(dist_table)

    # --- Token summary ---
    if token_summary.get("total_input_tokens", 0) > 0:
        token_table = Table(title="Token Cost Summary", show_header=True, header_style="bold cyan")
        token_table.add_column("Metric", style="bold")
        token_table.add_column("Value", justify="right")

        token_table.add_row("Input tokens", f"{token_summary['total_input_tokens']:,}")
        token_table.add_row("Output tokens", f"{token_summary['total_output_tokens']:,}")
        token_table.add_row(
            "Estimated cost",
            f"${token_summary['total_estimated_cost']:.4f}",
        )
        console.print(token_table)
    else:
        console.print("[dim]No token usage recorded yet.[/dim]")

    # --- Active run ---
    if active_run_id:
        console.print(f"Active run: [cyan]{active_run_id}[/cyan]")
    else:
        console.print("[dim]No active run.[/dim]")

    # --- Queue status ---
    queue_status = status_data.get("queue_status", {})
    if queue_status:
        pending_count = queue_status.get("pending_count", 0)
        stale_count = queue_status.get("stale_count", 0)
        pending_entries = queue_status.get("pending_entries", [])
        stale_dirs = queue_status.get("stale_directories", [])

        queue_table = Table(title="Queue", show_header=False, expand=False)
        queue_table.add_column("Metric", style="bold")
        queue_table.add_column("Value")

        pending_style = "yellow" if pending_count > 0 else "dim"
        stale_style = "yellow" if stale_count > 0 else "dim"
        queue_table.add_row(
            "Pending",
            f"[{pending_style}]{pending_count} directories[/{pending_style}]",
        )
        queue_table.add_row(
            "Stale",
            f"[{stale_style}]{stale_count} directories[/{stale_style}]",
        )
        console.print(queue_table)

        if pending_count > 0 and pending_entries:
            pending_table = Table(
                title="Pending Queue Entries",
                show_header=True,
                header_style="bold yellow",
            )
            pending_table.add_column("Commit", style="cyan")
            pending_table.add_column("Directories")
            pending_table.add_column("Queued At", style="dim")

            for entry in pending_entries:
                dirs = ", ".join(entry.get("affected_directories", []))
                pending_table.add_row(
                    entry.get("commit_hash", "")[:8],
                    dirs,
                    entry.get("queued_at", "")[:19],
                )
            console.print(pending_table)

        if stale_count > 0 and stale_dirs:
            console.print("Stale directories:")
            for d in stale_dirs:
                console.print(f"  [yellow]{d}[/yellow]")


# ---------------------------------------------------------------------------
# map:init
# ---------------------------------------------------------------------------


def print_init_summary(graph_data: dict, output_path: Path) -> None:
    """Render map:init output with Rich.

    Args:
        graph_data: Serialized graph dict from _map_init_impl.
        output_path: Path where _graph.json was written.
    """
    metadata = graph_data.get("metadata", {})
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    file_count = metadata.get("file_count", 0)
    languages = metadata.get("languages", {})
    blind_spots = metadata.get("blind_spots", [])
    entry_points = [n for n in nodes if n.get("is_entry_point")]

    lang_breakdown = ", ".join(
        f"{lang.capitalize()}: {count}"
        for lang, count in sorted(languages.items())
        if lang != "config"
    )

    summary_table = Table(title="map:init Summary", show_header=False, expand=False)
    summary_table.add_column("Metric", style="bold cyan", no_wrap=True)
    summary_table.add_column("Value", overflow="fold")

    summary_table.add_row("Files analyzed", str(file_count))
    if lang_breakdown:
        summary_table.add_row("Languages", lang_breakdown)
    summary_table.add_row("Entry points", str(len(entry_points)))
    summary_table.add_row("Edges", str(len(edges)))
    summary_table.add_row("Blind spots", str(len(blind_spots)))
    console.print(summary_table)
    click.echo(f"Output: {output_path}")


# ---------------------------------------------------------------------------
# map:gaps
# ---------------------------------------------------------------------------


def print_gaps_summary(
    serialized: dict,
    gaps: list,
    graph_mtime: str,
    output_path: Path,
    top: int,
) -> None:
    """Render map:gaps output with Rich tables.

    Args:
        serialized: Serialized coverage dict from CoverageBuilder.serialize().
        gaps: List of GapEntry objects.
        graph_mtime: ISO 8601 mtime of _graph.json.
        output_path: Path where _test_coverage.json was written.
        top: Maximum number of gaps to display.
    """
    metadata = serialized.get("metadata", {})
    test_files_count = len(serialized.get("test_files", []))

    # --- Summary header ---
    header_table = Table(title="Coverage Summary", show_header=False)
    header_table.add_column("Metric", style="bold cyan")
    header_table.add_column("Value")

    header_table.add_row("Tests discovered:", str(test_files_count))
    header_table.add_row("Total edges:", str(metadata.get("total_edges", 0)))
    header_table.add_row(
        "Covered edges:",
        f"{metadata.get('covered_edges', 0)} ({metadata.get('coverage_pct', 0.0):.1f}%)",
    )
    header_table.add_row("Uncovered edges:", str(metadata.get("uncovered_edges", 0)))
    console.print(header_table)

    # --- Gaps table ---
    if gaps:
        gaps_table = Table(title=f"Top {top} Coverage Gaps", show_header=True, header_style="bold red")
        gaps_table.add_column("Rank", justify="right", style="bold")
        gaps_table.add_column("Source")
        gaps_table.add_column("Target")
        gaps_table.add_column("Centrality", justify="right")
        gaps_table.add_column("Annotation")

        for rank, gap in enumerate(gaps, start=1):
            gaps_table.add_row(
                str(rank),
                gap.source,
                gap.target,
                f"{gap.centrality:.4f}",
                gap.annotation,
            )

        console.print(gaps_table)

    console.print(f"\n[dim]_graph.json last modified: {graph_mtime}[/dim]")
    click.echo(f"Output: {output_path}")


# ---------------------------------------------------------------------------
# map:cross
# ---------------------------------------------------------------------------


def print_cross_summary(project_doc: object, output_path: Path) -> None:
    """Render map:cross output with Rich.

    Args:
        project_doc: ProjectDoc instance from CrossCuttingAnalyzer.
        output_path: Path where _project.md was written.
    """
    summary_table = Table(title="map:cross Summary", show_header=False)
    summary_table.add_column("Category", style="bold cyan")
    summary_table.add_column("Count", justify="right")

    summary_table.add_row("Event flows:", str(len(project_doc.event_flows)))
    summary_table.add_row("Shared state:", str(len(project_doc.shared_state)))
    summary_table.add_row("API contracts:", str(len(project_doc.api_contracts)))
    summary_table.add_row("Plugin points:", str(len(project_doc.plugin_points)))
    summary_table.add_row("Blind spots:", str(len(project_doc.blind_spots)))

    console.print(summary_table)
    click.echo(f"Output: {output_path}")


# ---------------------------------------------------------------------------
# map:hint
# ---------------------------------------------------------------------------


def print_hint_stored(directory: str, hint_count: int) -> None:
    """Confirm hint storage with Rich styled text.

    Args:
        directory: The directory key the hint was stored under.
        hint_count: Total number of hints now stored for that directory.
    """
    console.print(
        f"[green]Hint stored[/green] for [cyan]{directory}[/cyan] "
        f"([dim]{hint_count} hint(s) total[/dim])"
    )


def print_correct_applied(directory: str, field: str) -> None:
    """Confirm developer correction with Rich styled text.

    Args:
        directory: The directory key the correction was applied to.
        field: The field that was corrected (summary or responsibilities).
    """
    console.print(
        f"[green]Corrected[/green] [bold]{field}[/bold] for [cyan]{directory}[/cyan] "
        f"([dim]confidence: 1.0, source: developer[/dim])"
    )


def print_skip_applied(directory: str) -> None:
    """Confirm directory skip marking with Rich styled text.

    Args:
        directory: The directory key that was marked as skip.
    """
    console.print(
        f"Marked [cyan]{directory}[/cyan] as [dim]low-priority (skip)[/dim]"
    )


# ---------------------------------------------------------------------------
# map:doc
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# hook:install / hook:uninstall
# ---------------------------------------------------------------------------


def print_hook_installed(result: dict) -> None:
    """Confirm post-commit hook installation with Rich styled text.

    Args:
        result: Dict returned by _hook_install_impl.
    """
    if result.get("already_present"):
        console.print("[yellow]Lattice hook already installed[/yellow] — no changes made.")
    elif result.get("installed"):
        hook_path = result.get("hook_path", "unknown")
        console.print(
            f"[green]Lattice hook installed[/green] at [cyan]{hook_path}[/cyan]"
        )
    else:
        reason = result.get("reason", "unknown")
        console.print(f"[red]Hook install failed[/red]: {reason}")


def print_hook_uninstalled(result: dict) -> None:
    """Confirm post-commit hook removal with Rich styled text.

    Args:
        result: Dict returned by _hook_uninstall_impl.
    """
    if result.get("removed"):
        hook_path = result.get("hook_path", "unknown")
        console.print(
            f"[green]Lattice hook removed[/green] from [cyan]{hook_path}[/cyan]"
        )
    else:
        reason = result.get("reason", "unknown")
        console.print(f"[dim]Nothing to remove[/dim]: {reason}")


# ---------------------------------------------------------------------------
# map:queue
# ---------------------------------------------------------------------------


def print_queue_result(result: dict) -> None:
    """Display map:queue result with Rich styled text.

    Args:
        result: Dict returned by _map_queue_impl.
    """
    commit_hash = result.get("commit_hash", "unknown")
    queued_dirs = result.get("queued_directories", [])
    upstream_stale = result.get("upstream_stale", [])

    console.print(f"[green]Queued[/green] commit [cyan]{commit_hash[:8]}[/cyan]")

    if queued_dirs:
        console.print(f"  Affected directories ([dim]{len(queued_dirs)}[/dim]):")
        for d in queued_dirs:
            console.print(f"    [cyan]{d}[/cyan]")
    else:
        console.print("  [dim]No affected directories[/dim]")

    if upstream_stale:
        console.print(f"  Upstream consumers to mark stale ([dim]{len(upstream_stale)}[/dim]):")
        for d in upstream_stale:
            console.print(f"    [yellow]{d}[/yellow]")


# ---------------------------------------------------------------------------
# map:doc
# ---------------------------------------------------------------------------


def print_test_status_result(result: dict) -> None:
    """Display map:test-status result with Rich styled text.

    Args:
        result: Dict returned by _map_test_status_impl.
    """
    if result.get("error"):
        console.print(f"[red]Error[/red]: {result['error']}")
        return

    updated_dirs = result.get("updated_directories", [])
    console.print(f"Updated [cyan]{len(updated_dirs)}[/cyan] directories:")
    for d in updated_dirs:
        directory = d.get("directory", "unknown")
        tested = d.get("tested", 0)
        untested = d.get("untested", 0)
        console.print(
            f"  [cyan]{directory}[/cyan] "
            f"([green]{tested} edges TESTED[/green], "
            f"[yellow]{untested} edges UNTESTED[/yellow])"
        )


def print_doc_wave(wave_index: int, dir_count: int, written: int, failed: int, stubs: int) -> None:
    """Print a wave progress line for map:doc.

    Args:
        wave_index: Zero-based wave index.
        dir_count: Total directories in this wave.
        written: Number of docs successfully written.
        failed: Number of docs that failed.
        stubs: Number of test stubs written.
    """
    status_color = "green" if failed == 0 else "yellow"
    console.print(
        f"[{status_color}]Wave {wave_index}[/{status_color}]: "
        f"[cyan]{dir_count}[/cyan] dirs — "
        f"[green]{written}[/green] written, "
        f"[red]{failed}[/red] failed, "
        f"[dim]{stubs} stubs[/dim]"
    )


def print_doc_summary(run_id: str, summary: dict) -> None:
    """Render the map:doc run summary table.

    Args:
        run_id: The run identifier.
        summary: Dict from FleetCheckpoint.get_run_summary().
    """
    run_table = Table(title="Run Summary", show_header=False)
    run_table.add_column("Metric", style="bold cyan")
    run_table.add_column("Value")

    run_table.add_row("Run ID", run_id)
    run_table.add_row("Input tokens", f"{summary.get('total_input_tokens', 0):,}")
    run_table.add_row("Output tokens", f"{summary.get('total_output_tokens', 0):,}")
    run_table.add_row("Estimated cost", f"${summary.get('total_estimated_cost', 0.0):.4f}")
    run_table.add_row(
        "Waves",
        (
            f"{summary.get('waves_complete', 0)} complete, "
            f"{summary.get('waves_partial', 0)} partial, "
            f"{summary.get('waves_pending', 0)} pending"
        ),
    )

    console.print(run_table)
