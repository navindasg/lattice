"""Lattice CLI — codebase intelligence mapper.

Entry point: `lattice` command group with subcommands.

Commands:
    map:init <target>    — Walk <target> directory, run analysis pipeline,
                           serialize to <target>/.agent-docs/_graph.json.
    map:status <target>  — Show pipeline progress, confidence distribution,
                           and token cost for TARGET.
    map:hint <target>    — Store a developer hint in .agent-docs/_hints.json.
    map:gaps <target>    — Compute test coverage gaps from an existing
                           _graph.json, write _test_coverage.json.
    map:doc <target>     — Dispatch fleet agents wave-by-wave to document all
                           directories, writing _dir.md files and test stubs.
    map:cross <target>   — Detect cross-cutting patterns (event flows, shared
                           state, API contracts, plugin points), write
                           _project.md, and augment _graph.json.

All commands accept --json to output a structured JSON envelope to stdout.

Usage::

    lattice map:init /path/to/project
    lattice map:init /path/to/project --json
    lattice map:status /path/to/project
    lattice map:status /path/to/project --json
    lattice map:hint /path/to/project src/auth "handles OAuth"
    lattice map:gaps /path/to/project --top 10
    lattice map:doc /path/to/project --tier silver
    lattice map:cross /path/to/project

The map:doc pipeline:
    1. Load _graph.json from target/.agent-docs/
    2. Build directory DAG and plan topological waves
    3. Print wave plan (before any LLM calls)
    4. For each wave: dispatch via FleetDispatcher, assemble docs, write stubs
    5. Checkpoint completed waves for resume support
    6. Print final run summary
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click
import duckdb
import networkx as nx

from lattice.adapters.python_adapter import PythonAdapter
from lattice.api.models import error_response, success_response
from lattice.cli.formatting import (
    print_correct_applied,
    print_cross_summary,
    print_doc_summary,
    print_doc_wave,
    print_gaps_summary,
    print_hint_stored,
    print_hook_installed,
    print_hook_uninstalled,
    print_init_summary,
    print_queue_result,
    print_skip_applied,
    print_status,
    print_test_status_result,
)
from lattice.cli.hints import _map_correct_impl, _map_hint_impl, _map_skip_impl
from lattice.cli.hooks import _hook_install_impl, _hook_uninstall_impl
from lattice.cli.queue import _map_queue_impl, _map_test_status_impl, _read_queue, _write_queue
from lattice.cli.status import _map_status_impl
from lattice.orchestrator.status import get_all_instance_status, get_instance_status
from lattice.cross_cutting import (
    CrossCuttingAnalyzer,
    build_cross_cutting_edges,
    enrich_dir_docs_if_present,
    write_project_doc,
)
from lattice.fleet.assembler import DocumentAssembler
from lattice.fleet.checkpoint import FleetCheckpoint
from lattice.fleet.dispatcher import FleetDispatcher
from lattice.fleet.planner import build_directory_dag, format_wave_plan, plan_waves
from lattice.fleet.models import WavePlan
from lattice.fleet.skeleton import SkeletonWriter
from lattice.graph.builder import DependencyGraphBuilder
from lattice.graph.config_wiring import ConfigWiringDetector
from lattice.graph.entry_points import EntryPointDetector
from lattice.graph.serializer import serialize_graph
from lattice.models.analysis import FileAnalysis
from lattice.testing import CoverageBuilder, TestClassifier, TestDiscovery


# Directories to skip during file traversal
_SKIP_DIRS = frozenset({
    "__pycache__",
    "node_modules",
    ".git",
    ".agent-docs",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
    "htmlcov",
    ".mypy_cache",
    ".pytest_cache",
})

# File extensions per adapter type
_PYTHON_EXTENSIONS = frozenset({".py"})
_TS_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx"})


@click.group()
def cli() -> None:
    """Lattice -- codebase intelligence mapper."""


def _walk_files(target: Path) -> list[Path]:
    """Recursively walk target, collecting source files while skipping common dirs."""
    collected: list[Path] = []
    for item in target.rglob("*"):
        # Skip files inside excluded directories
        if any(part in _SKIP_DIRS for part in item.parts):
            continue
        if item.is_file() and item.suffix in (_PYTHON_EXTENSIONS | _TS_EXTENSIONS):
            collected.append(item)
    return collected


def _try_import_typescript_adapter() -> type | None:
    """Attempt to import TypeScriptAdapter; return None if Node.js is unavailable."""
    try:
        from lattice.adapters.typescript_adapter import TypeScriptAdapter
        return TypeScriptAdapter
    except Exception:
        return None


def _map_init_impl(target: Path) -> dict:
    """Core pipeline implementation, separated for testability.

    Args:
        target: The directory to analyse.

    Returns:
        Serialized graph dict ready for JSON output.
    """
    project_root = target.resolve()

    # Collect source files
    source_files = _walk_files(project_root)

    # Prepare adapters
    python_adapter = PythonAdapter(project_root)

    TypeScriptAdapterClass = _try_import_typescript_adapter()
    ts_adapter = None
    ts_init_failed = False
    if TypeScriptAdapterClass is not None:
        try:
            ts_adapter = TypeScriptAdapterClass(project_root)
        except Exception:
            ts_init_failed = True

    # Analyze files
    analyses: list[FileAnalysis] = []
    for file_path in source_files:
        try:
            if file_path.suffix in _PYTHON_EXTENSIONS:
                analysis = python_adapter.analyze(file_path)
                analyses.append(analysis)
            elif file_path.suffix in _TS_EXTENSIONS:
                if ts_adapter is not None:
                    try:
                        analysis = ts_adapter.analyze(file_path)
                        analyses.append(analysis)
                    except Exception:
                        # Individual file failures are non-fatal
                        pass
                # If ts_adapter is None, skip TS files silently
        except Exception:
            # Non-fatal: skip files that fail to parse
            pass

    # Build graph
    builder = DependencyGraphBuilder()
    graph = builder.build(analyses, project_root)

    # Annotate entry points
    entry_detector = EntryPointDetector()
    entry_detector.detect(graph, analyses)

    # Add config wiring
    config_detector = ConfigWiringDetector()
    config_detector.detect(project_root, graph, analyses)

    # Serialize
    return serialize_graph(graph, analyses)


@cli.command("map:init")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_init(target: str, as_json: bool) -> None:
    """Walk TARGET directory and produce a dependency graph.

    Writes the graph to TARGET/.agent-docs/_graph.json and prints a summary.
    TARGET defaults to the current directory.
    """
    target_path = Path(target).resolve()

    graph_data = _map_init_impl(target_path)

    # Write output
    output_dir = target_path / ".agent-docs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "_graph.json"

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(graph_data, f, indent=2)

    if as_json:
        click.echo(json.dumps(success_response("map:init", graph_data)))
    else:
        print_init_summary(graph_data, output_path)


# ---------------------------------------------------------------------------
# map:status command
# ---------------------------------------------------------------------------


@cli.command("map:status")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_status(target: str, as_json: bool) -> None:
    """Show mapping pipeline status for TARGET.

    Prints pipeline progress (passes complete), confidence distribution,
    and token cost summary. Works even on cold start (no .agent-docs/).

    TARGET defaults to the current directory.
    """
    target_path = Path(target).resolve()
    status_data = _map_status_impl(target_path)

    if as_json:
        click.echo(json.dumps(success_response("map:status", status_data)))
    else:
        print_status(status_data)


# ---------------------------------------------------------------------------
# map:hint command
# ---------------------------------------------------------------------------


@cli.command("map:hint")
@click.argument("target", type=click.Path(exists=True, file_okay=False))
@click.argument("directory")
@click.argument("text", required=False, default=None)
@click.option("--idk", is_flag=True, help="Mark directory for IDK escalated investigation.")
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_hint(target: str, directory: str, text: str | None, idk: bool, as_json: bool) -> None:
    """Store a developer hint for DIRECTORY in TARGET project.

    Appends the hint to TARGET/.agent-docs/_hints.json.
    Provide TEXT for a free-form hint, or --idk to flag the directory
    as needing escalated investigation.
    """
    if not text and not idk:
        raise click.UsageError("Either TEXT or --idk must be provided.")

    target_path = Path(target).resolve()

    if idk:
        result = _map_hint_impl(target_path, directory, hint_text=None, hint_type="idk")
    else:
        result = _map_hint_impl(target_path, directory, hint_text=text, hint_type="hint")

    if as_json:
        click.echo(json.dumps(success_response("map:hint", result)))
    else:
        print_hint_stored(result["directory"], result["hint_count"])


# ---------------------------------------------------------------------------
# map:correct command
# ---------------------------------------------------------------------------


@cli.command("map:correct")
@click.argument("target", type=click.Path(exists=True, file_okay=False))
@click.argument("directory")
@click.option(
    "--field",
    required=True,
    type=click.Choice(["summary", "responsibilities"]),
    help="Field to correct.",
)
@click.option("--value", required=True, help="New value for the field.")
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_correct(target: str, directory: str, field: str, value: str, as_json: bool) -> None:
    """Apply a developer correction to DIRECTORY documentation in TARGET.

    Updates the named --field in TARGET/.agent-docs/DIRECTORY/_dir.md,
    setting confidence=1.0 and source=developer.
    """
    target_path = Path(target).resolve()

    try:
        result = _map_correct_impl(target_path, directory, field, value)
    except FileNotFoundError as exc:
        if as_json:
            click.echo(json.dumps(error_response("map:correct", "NO_DOCUMENTATION", str(exc))))
            return
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        if as_json:
            click.echo(json.dumps(error_response("map:correct", "INVALID_FIELD", str(exc))))
            return
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(success_response("map:correct", result)))
    else:
        print_correct_applied(result["directory"], result["field"])


# ---------------------------------------------------------------------------
# map:skip command
# ---------------------------------------------------------------------------


@cli.command("map:skip")
@click.argument("target", type=click.Path(exists=True, file_okay=False))
@click.argument("directory")
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_skip(target: str, directory: str, as_json: bool) -> None:
    """Mark DIRECTORY as low-priority (skip) in TARGET project.

    Stores a skip entry in TARGET/.agent-docs/_hints.json.
    """
    target_path = Path(target).resolve()
    result = _map_skip_impl(target_path, directory)

    if as_json:
        click.echo(json.dumps(success_response("map:skip", result)))
    else:
        print_skip_applied(result["directory"])


# ---------------------------------------------------------------------------
# map:gaps helpers
# ---------------------------------------------------------------------------


def load_graph_from_json(graph_path: Path) -> nx.DiGraph:
    """Load a _graph.json file into a NetworkX DiGraph.

    Args:
        graph_path: Path to the _graph.json file.

    Returns:
        NetworkX DiGraph with node and edge attributes populated.
    """
    with graph_path.open(encoding="utf-8") as f:
        data = json.load(f)

    graph = nx.DiGraph()

    for node in data.get("nodes", []):
        graph.add_node(
            node["id"],
            language=node.get("language", "unknown"),
            is_entry_point=node.get("is_entry_point", False),
            entry_point_type=node.get("entry_point_type"),
            entry_details=node.get("entry_details"),
        )

    for edge in data.get("edges", []):
        graph.add_edge(
            edge["source"],
            edge["target"],
            import_type=edge.get("import_type", "standard"),
        )

    return graph


# ---------------------------------------------------------------------------
# map:gaps command
# ---------------------------------------------------------------------------


@cli.command("map:gaps")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--top",
    default=10,
    show_default=True,
    help="Number of top gaps to display.",
)
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_gaps(target: str, top: int, as_json: bool) -> None:
    """Compute test coverage gaps for TARGET using an existing dependency graph.

    Reads TARGET/.agent-docs/_graph.json, discovers and classifies tests, computes
    transitive edge coverage, and writes _test_coverage.json with a gap report.

    TARGET defaults to the current directory.
    """
    target_path = Path(target).resolve()
    agent_docs = target_path / ".agent-docs"
    graph_path = agent_docs / "_graph.json"

    if not graph_path.exists():
        if as_json:
            click.echo(json.dumps(error_response(
                "map:gaps",
                "GRAPH_NOT_FOUND",
                f"No dependency graph found at {graph_path}. Run `lattice map:init` first.",
            )))
            return
        raise click.ClickException(
            f"No dependency graph found at {graph_path}. "
            "Run `lattice map:init` first."
        )

    # Load graph
    graph = load_graph_from_json(graph_path)
    graph_node_keys: set[str] = set(graph.nodes())

    # Discover and classify tests
    discovered_paths = TestDiscovery(target_path).discover()
    test_files = TestClassifier(target_path, graph_node_keys).classify_all(
        discovered_paths
    )

    # Compute coverage
    builder = CoverageBuilder(graph, target_path)
    coverage = builder.build(test_files, top_n=top)
    serialized = CoverageBuilder.serialize(coverage)

    # Write output
    output_path = agent_docs / "_test_coverage.json"
    agent_docs.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(serialized, f, indent=2)

    if as_json:
        click.echo(json.dumps(success_response("map:gaps", serialized)))
        return

    # Human-readable output
    graph_mtime = datetime.fromtimestamp(
        graph_path.stat().st_mtime, tz=timezone.utc
    ).isoformat()

    print_gaps_summary(serialized, coverage.gaps, graph_mtime, output_path, top)


# ---------------------------------------------------------------------------
# map:doc command
# ---------------------------------------------------------------------------


@cli.command("map:doc")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--tier",
    default="bronze",
    type=click.Choice(["silver", "bronze"]),
    show_default=True,
    help="Model tier for fleet agents.",
)
@click.option(
    "--concurrency",
    default=8,
    show_default=True,
    help="Max concurrent agent calls per wave.",
)
@click.option(
    "--resume",
    default=None,
    type=str,
    help="Resume a previous run by run_id.",
)
@click.option("--force", is_flag=True, help="Re-investigate developer-protected directories.")
@click.option("--incremental", is_flag=True, help="Re-document only pending queue entries.")
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_doc(target: str, tier: str, concurrency: int, resume: str | None, force: bool, incremental: bool, as_json: bool) -> None:
    """Dispatch fleet agents to document all directories in TARGET.

    Reads TARGET/.agent-docs/_graph.json, plans waves, and dispatches
    agents wave-by-wave. Writes _dir.md and test stubs to .agent-docs/.
    Use --resume <run_id> to restart an interrupted run.
    Use --incremental to re-document only pending entries from _queue.json.

    TARGET defaults to the current directory.
    """
    target_path = Path(target).resolve()
    agent_docs = target_path / ".agent-docs"
    graph_path = agent_docs / "_graph.json"

    # --incremental mode: override tier default to silver if not explicitly set
    if incremental:
        ctx = click.get_current_context()
        if ctx.get_parameter_source("tier") == click.core.ParameterSource.DEFAULT:
            tier = "silver"

        _run_incremental_doc(
            target_path=target_path,
            agent_docs=agent_docs,
            graph_path=graph_path,
            tier=tier,
            concurrency=concurrency,
            resume=resume,
            force=force,
            as_json=as_json,
        )
        return

    if not graph_path.exists():
        if as_json:
            click.echo(json.dumps(error_response(
                "map:doc",
                "GRAPH_NOT_FOUND",
                f"No dependency graph found at {graph_path}. Run `lattice map:init` first.",
            )))
            return
        raise click.ClickException(
            f"No dependency graph found at {graph_path}. "
            "Run `lattice map:init` first."
        )

    # Load file-level graph
    file_graph = load_graph_from_json(graph_path)

    # Load test coverage data if available (non-fatal if missing)
    coverage_data: dict = {}
    coverage_path = agent_docs / "_test_coverage.json"
    if coverage_path.exists():
        try:
            with coverage_path.open(encoding="utf-8") as f:
                coverage_data = json.load(f)
        except Exception:
            # Non-fatal: proceed without coverage gap data
            pass

    # Build directory DAG and plan waves
    try:
        dir_dag = build_directory_dag(file_graph)
        waves = plan_waves(dir_dag)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    # Determine run_id (new UUID for new run, or --resume value)
    run_id = resume if resume is not None else str(uuid.uuid4())

    # Build WavePlan
    total_tokens = sum(w.estimated_input_tokens for w in waves)
    wave_plan = WavePlan(waves=waves, total_estimated_tokens=total_tokens, run_id=run_id)

    # Display wave plan BEFORE any LLM calls (success criteria #1)
    if not as_json:
        click.echo(format_wave_plan(wave_plan, tier))
        click.echo("")

    # Set up DuckDB and FleetCheckpoint (shared connection)
    db_path = agent_docs / "fleet.duckdb"
    conn = duckdb.connect(str(db_path))
    fleet_checkpoint = FleetCheckpoint(conn)

    # Resolve completed waves for resume logic
    completed_wave_indices = set(fleet_checkpoint.get_completed_waves(run_id))
    if completed_wave_indices and not as_json:
        click.echo(
            f"Resuming run {run_id}: skipping {len(completed_wave_indices)} "
            f"already-completed wave(s)."
        )

    # Build dispatcher, assembler, skeleton writer
    dispatcher = FleetDispatcher(
        tier=tier,
        project_root=target_path,
        file_graph=file_graph,
        coverage_data=coverage_data,
        agent_docs_root=agent_docs,
        checkpoint=fleet_checkpoint,
        concurrency_cap=concurrency,
        force=force,
    )
    assembler = DocumentAssembler()
    skeleton_writer = SkeletonWriter()

    # Dispatch wave-by-wave
    for wave in wave_plan.waves:
        if wave.index in completed_wave_indices:
            if not as_json:
                click.echo(f"Wave {wave.index}: skipped (already complete)")
            continue

        # Run the async dispatch in the current event loop (or a new one)
        results = asyncio.run(dispatcher.dispatch_wave(wave, run_id=run_id))

        # Assemble docs
        written, failed = assembler.assemble_wave(results, agent_docs)

        # Write test stubs for successful results
        stubs_written = 0
        for result in results:
            if not result.failed and result.dir_doc is not None:
                stub_paths = skeleton_writer.write_stubs(result, agent_docs)
                stubs_written += len(stub_paths)

        # Record token usage per directory
        for result in results:
            if not result.failed:
                from lattice.fleet.planner import _TIER_COST_PER_MILLION
                cost_per_million = _TIER_COST_PER_MILLION.get(tier, 3.0)
                estimated_cost = (result.input_tokens / 1_000_000) * cost_per_million
                fleet_checkpoint.record_token_usage(
                    run_id=run_id,
                    wave_index=wave.index,
                    directory=result.directory,
                    tier=tier,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    estimated_cost_usd=estimated_cost,
                )

        if not as_json:
            print_doc_wave(wave.index, len(wave.directories), written, failed, stubs_written)

    # Print final run summary
    summary = fleet_checkpoint.get_run_summary(run_id)
    conn.close()

    if as_json:
        click.echo(json.dumps(success_response("map:doc", {"run_id": run_id, **summary})))
    else:
        click.echo("")
        print_doc_summary(run_id, summary)


# ---------------------------------------------------------------------------
# map:doc --incremental helper
# ---------------------------------------------------------------------------


def _run_incremental_doc(
    target_path: Path,
    agent_docs: Path,
    graph_path: Path,
    tier: str,
    concurrency: int,
    resume: str | None,
    force: bool,
    as_json: bool,
) -> None:
    """Execute map:doc --incremental: re-document only pending queue entries.

    Reads pending entries from _queue.json, dispatches only affected
    directories via subgraph wave planning, marks upstream 1-hop consumers
    stale, and removes processed entries from the queue.
    """
    from lattice.shadow.reader import parse_dir_doc
    from lattice.shadow.writer import write_dir_doc

    if not graph_path.exists():
        if as_json:
            click.echo(json.dumps(error_response(
                "map:doc",
                "GRAPH_NOT_FOUND",
                f"No dependency graph found at {graph_path}. Run `lattice map:init` first.",
            )))
            return
        raise click.ClickException(
            f"No dependency graph found at {graph_path}. "
            "Run `lattice map:init` first."
        )

    queue_path = agent_docs / "_queue.json"
    queue_data = _read_queue(queue_path)
    tmp_path = agent_docs / "_queue.json.tmp"

    pending_entries = [e for e in queue_data.get("entries", []) if e.get("status") == "pending"]

    if not pending_entries:
        if not as_json:
            click.echo("No pending entries in queue.")
        else:
            click.echo(json.dumps(success_response("map:doc", {"run_id": None, "incremental": True, "status": "no_pending"})))
        return

    # Flatten all affected and upstream directories from pending entries
    all_affected: set[str] = set()
    all_upstream: set[str] = set()
    for entry in pending_entries:
        all_affected.update(entry.get("affected_directories", []))
        all_upstream.update(entry.get("upstream_consumers", []))

    # Load file-level graph
    file_graph = load_graph_from_json(graph_path)

    # Load test coverage data if available (non-fatal if missing)
    coverage_data: dict = {}
    coverage_path = agent_docs / "_test_coverage.json"
    if coverage_path.exists():
        try:
            with coverage_path.open(encoding="utf-8") as f:
                coverage_data = json.load(f)
        except Exception:
            pass

    # Build full directory DAG then subgraph of only affected dirs
    try:
        dir_dag = build_directory_dag(file_graph)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    # Filter out developer-protected directories (unless --force)
    dispatched_dirs: set[str] = set()
    for d in all_affected:
        if not force:
            dir_md = agent_docs / d / "_dir.md"
            if dir_md.exists():
                try:
                    doc = parse_dir_doc(dir_md)
                    if doc.source == "developer":
                        continue
                except Exception:
                    pass
        dispatched_dirs.add(d)

    if not dispatched_dirs:
        if not as_json:
            click.echo("All affected directories are developer-protected. Use --force to override.")
        return

    # Build subgraph and plan waves
    sub_dag = dir_dag.subgraph(dispatched_dirs).copy()
    try:
        waves = plan_waves(sub_dag)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    run_id = resume if resume is not None else str(uuid.uuid4())

    total_tokens = sum(w.estimated_input_tokens for w in waves)
    wave_plan = WavePlan(waves=waves, total_estimated_tokens=total_tokens, run_id=run_id)

    if not as_json:
        click.echo(format_wave_plan(wave_plan, tier))
        click.echo("")

    # Set up DuckDB and checkpoint
    db_path = agent_docs / "fleet.duckdb"
    conn = duckdb.connect(str(db_path))
    fleet_checkpoint = FleetCheckpoint(conn)

    completed_wave_indices = set(fleet_checkpoint.get_completed_waves(run_id))

    dispatcher = FleetDispatcher(
        tier=tier,
        project_root=target_path,
        file_graph=file_graph,
        coverage_data=coverage_data,
        agent_docs_root=agent_docs,
        checkpoint=fleet_checkpoint,
        concurrency_cap=concurrency,
        force=force,
    )
    assembler = DocumentAssembler()
    skeleton_writer = SkeletonWriter()

    # Dispatch wave-by-wave
    for wave in wave_plan.waves:
        if wave.index in completed_wave_indices:
            if not as_json:
                click.echo(f"Wave {wave.index}: skipped (already complete)")
            continue

        results = asyncio.run(dispatcher.dispatch_wave(wave, run_id=run_id))

        written, failed = assembler.assemble_wave(results, agent_docs)

        stubs_written = 0
        for result in results:
            if not result.failed and result.dir_doc is not None:
                stub_paths = skeleton_writer.write_stubs(result, agent_docs)
                stubs_written += len(stub_paths)

        for result in results:
            if not result.failed:
                from lattice.fleet.planner import _TIER_COST_PER_MILLION
                cost_per_million = _TIER_COST_PER_MILLION.get(tier, 3.0)
                estimated_cost = (result.input_tokens / 1_000_000) * cost_per_million
                fleet_checkpoint.record_token_usage(
                    run_id=run_id,
                    wave_index=wave.index,
                    directory=result.directory,
                    tier=tier,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    estimated_cost_usd=estimated_cost,
                )

        if not as_json:
            print_doc_wave(wave.index, len(wave.directories), written, failed, stubs_written)

    # Mark 1-hop upstream consumers as stale
    for upstream_dir in all_upstream:
        dir_md = agent_docs / upstream_dir / "_dir.md"
        if not dir_md.exists():
            continue  # Pitfall 3: skip if no _dir.md
        try:
            doc = parse_dir_doc(dir_md)
            if not doc.stale:
                updated = doc.model_copy(update={"stale": True})
                write_dir_doc(updated, agent_docs)
        except Exception:
            pass

    # Remove processed pending entries from queue
    remaining_entries = [e for e in queue_data.get("entries", []) if e.get("status") != "pending"]
    _write_queue({"entries": remaining_entries}, queue_path, tmp_path)

    # Print final run summary
    summary = fleet_checkpoint.get_run_summary(run_id)
    conn.close()

    if as_json:
        click.echo(json.dumps(success_response("map:doc", {"run_id": run_id, "incremental": True, **summary})))
    else:
        click.echo("")
        print_doc_summary(run_id, summary)


# ---------------------------------------------------------------------------
# hook:install command
# ---------------------------------------------------------------------------


@cli.command("hook:install")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def hook_install(target: str, as_json: bool) -> None:
    """Install the Lattice post-commit hook in TARGET git repository.

    Creates or appends to .git/hooks/post-commit with a sentinel-delimited
    section that calls map:queue in the background after each commit.

    TARGET defaults to the current directory.
    """
    target_path = Path(target).resolve()
    result = _hook_install_impl(target_path)

    if as_json:
        click.echo(json.dumps(success_response("hook:install", result)))
    else:
        print_hook_installed(result)


# ---------------------------------------------------------------------------
# hook:uninstall command
# ---------------------------------------------------------------------------


@cli.command("hook:uninstall")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def hook_uninstall(target: str, as_json: bool) -> None:
    """Remove the Lattice post-commit hook section from TARGET.

    Removes the LATTICE-HOOK-BEGIN/END sentinel block from .git/hooks/post-commit.
    Deletes the hook file entirely if no other content remains.

    TARGET defaults to the current directory.
    """
    target_path = Path(target).resolve()
    result = _hook_uninstall_impl(target_path)

    if as_json:
        click.echo(json.dumps(success_response("hook:uninstall", result)))
    else:
        print_hook_uninstalled(result)


# ---------------------------------------------------------------------------
# map:queue command
# ---------------------------------------------------------------------------


@cli.command("map:queue")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--commit", required=True, help="Git commit hash to queue.")
@click.option(
    "--files",
    required=True,
    multiple=True,
    help="Changed file paths (can be specified multiple times).",
)
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_queue(target: str, commit: str, files: tuple[str, ...], as_json: bool) -> None:
    """Queue a commit for incremental re-documentation.

    Writes an entry to TARGET/.agent-docs/_queue.json with affected
    directories and 1-hop upstream consumers. Called automatically
    by the post-commit hook.

    TARGET defaults to the current directory.
    """
    target_path = Path(target).resolve()
    result = _map_queue_impl(target_path, commit, list(files))

    if as_json:
        click.echo(json.dumps(success_response("map:queue", result)))
    else:
        print_queue_result(result)


# ---------------------------------------------------------------------------
# map:test-status command
# ---------------------------------------------------------------------------


@cli.command("map:test-status")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_test_status(target: str, as_json: bool) -> None:
    """Recompute test coverage and update integration_points in _dir.md files.

    Reads TARGET/.agent-docs/_graph.json, discovers test files, recomputes
    coverage, and updates integration_points (TESTED/UNTESTED) in affected
    _dir.md files. No LLM calls are made.

    TARGET defaults to the current directory.
    """
    target_path = Path(target).resolve()
    result = _map_test_status_impl(target_path)

    if as_json:
        click.echo(json.dumps(success_response("map:test-status", result)))
    else:
        print_test_status_result(result)


# ---------------------------------------------------------------------------
# map:cross command
# ---------------------------------------------------------------------------


@cli.command("map:cross")
@click.argument("target", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def map_cross(target: str, as_json: bool) -> None:
    """Detect cross-cutting patterns in TARGET and write _project.md.

    Reads TARGET/.agent-docs/_graph.json (must exist — run `map:init` first),
    runs cross-cutting analysis (event flows, shared state, API contracts,
    plugin points), writes _project.md to .agent-docs/, augments _graph.json
    with cross_cutting_edges, and conditionally enriches _dir.md files.

    TARGET defaults to the current directory.
    """
    target_path = Path(target).resolve()
    agent_docs = target_path / ".agent-docs"
    graph_path = agent_docs / "_graph.json"

    if not graph_path.exists():
        if as_json:
            click.echo(json.dumps(error_response(
                "map:cross",
                "GRAPH_NOT_FOUND",
                f"No dependency graph found at {graph_path}. Run `lattice map:init` first.",
            )))
            return
        raise click.ClickException(
            f"No dependency graph found at {graph_path}. "
            "Run `lattice map:init` first."
        )

    # Load graph data
    with graph_path.open(encoding="utf-8") as f:
        graph_data = json.load(f)

    # Collect source files
    source_files = _walk_files(target_path)

    # Run cross-cutting analysis
    analyzer = CrossCuttingAnalyzer(target_path)
    project_doc = analyzer.analyze(graph_data, source_files)

    # Write _project.md
    agent_docs.mkdir(parents=True, exist_ok=True)
    project_md_path = write_project_doc(project_doc, agent_docs)

    # Augment _graph.json with cross_cutting_edges
    cross_cutting_edges = build_cross_cutting_edges(project_doc)
    graph_data["cross_cutting_edges"] = cross_cutting_edges
    with graph_path.open("w", encoding="utf-8") as f:
        json.dump(graph_data, f, indent=2)

    # Conditionally enrich _dir.md files (no-op if none exist)
    enrich_dir_docs_if_present(project_doc, agent_docs)

    if as_json:
        data = {
            "event_flows": len(project_doc.event_flows),
            "shared_state": len(project_doc.shared_state),
            "api_contracts": len(project_doc.api_contracts),
            "plugin_points": len(project_doc.plugin_points),
            "blind_spots": len(project_doc.blind_spots),
            "output": str(project_md_path),
        }
        click.echo(json.dumps(success_response("map:cross", data)))
    else:
        print_cross_summary(project_doc, project_md_path)


# ---------------------------------------------------------------------------
# serve command
# ---------------------------------------------------------------------------


@cli.command("serve")
@click.option(
    "--port",
    default=8765,
    show_default=True,
    help="Port to listen on.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind.",
)
@click.option(
    "--reload",
    is_flag=True,
    help="Enable hot reload for development.",
)
def serve(port: int, host: str, reload: bool) -> None:
    """Start the Lattice Mapper HTTP API server.

    Starts uvicorn on HOST:PORT serving the FastAPI /command endpoint.
    The OpenAPI spec is available at /docs and /openapi.json.

    Examples::

        lattice serve
        lattice serve --port 9000
        lattice serve --host 0.0.0.0 --port 8080 --reload
    """
    import uvicorn

    from lattice.api.app import app

    uvicorn.run(app, host=host, port=port, reload=reload)


# ---------------------------------------------------------------------------
# orchestrator:status command
# ---------------------------------------------------------------------------


@cli.command("orchestrator:status")
@click.option(
    "--instance",
    "instance_id",
    default=None,
    help="Show status for a specific instance ID.",
)
@click.option(
    "--db",
    "db_path",
    default=".lattice/orchestrator.duckdb",
    show_default=True,
    help="Path to orchestrator DuckDB.",
)
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def orchestrator_status(instance_id: str | None, db_path: str, as_json: bool) -> None:
    """Display context utilization status for orchestrator instances.

    Reads the context_utilization table from the orchestrator DuckDB at DB_PATH.
    Use --instance to filter to a single instance. Without --instance, all
    tracked instances are shown sorted by utilization descending.

    Returns zero-value status (no error) when the instance or database does
    not exist yet.

    Examples::

        lattice orchestrator:status
        lattice orchestrator:status --instance inst-abc123
        lattice orchestrator:status --db /path/to/orchestrator.duckdb --json
    """
    from lattice.cli.formatting import console
    from rich.table import Table
    from rich.text import Text

    db_file = Path(db_path)

    if not db_file.exists():
        # DuckDB file absent — emit empty status gracefully
        if as_json:
            data: dict = {"instances": [], "db_path": db_path, "db_exists": False}
            click.echo(json.dumps(success_response("orchestrator:status", data)))
        else:
            console.print(
                f"[dim]No orchestrator database at [cyan]{db_path}[/cyan]. "
                "Start the orchestrator to initialize.[/dim]"
            )
        return

    conn = duckdb.connect(str(db_file), read_only=True)

    try:
        if instance_id is not None:
            status = get_instance_status(conn, instance_id)
            rows = [status]
        else:
            rows = get_all_instance_status(conn)
    finally:
        conn.close()

    if as_json:
        data = {
            "instances": rows,
            "db_path": db_path,
            "db_exists": True,
        }
        click.echo(json.dumps(success_response("orchestrator:status", data)))
        return

    # Human-readable table output
    if not rows:
        console.print("[dim]No instance utilization data found.[/dim]")
        return

    table = Table(title="Orchestrator Instance Status", show_header=True, header_style="bold cyan")
    table.add_column("Instance ID", style="cyan")
    table.add_column("Utilization %", justify="right")
    table.add_column("Bytes Sent", justify="right")
    table.add_column("Bytes Received", justify="right")
    table.add_column("Compactions", justify="right")
    table.add_column("Last Updated", style="dim")

    for row in rows:
        pct = row["utilization_pct"]
        if pct >= 80.0:
            pct_style = "red"
        elif pct >= 50.0:
            pct_style = "yellow"
        else:
            pct_style = "green"

        table.add_row(
            row["instance_id"],
            Text(f"{pct:.1f}%", style=pct_style),
            f"{row['bytes_sent']:,}",
            f"{row['bytes_received']:,}",
            str(row["compaction_count"]),
            row["last_updated"] or "—",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# orchestrator:voice command
# ---------------------------------------------------------------------------


@cli.command("orchestrator:voice")
@click.option(
    "--text",
    "text_input",
    default=None,
    help="Process text input instead of audio (text fallback).",
)
@click.option(
    "--db",
    "db_path",
    default=".lattice/orchestrator.duckdb",
    show_default=True,
    help="Path to orchestrator DuckDB.",
)
@click.option(
    "--project",
    "project_root",
    default=None,
    help="Project root to wire live mapper subprocess for NDJSON dispatch.",
)
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def orchestrator_voice(
    text_input: str | None,
    db_path: str,
    project_root: str | None,
    as_json: bool,
) -> None:
    """Start voice listener or process text input through intent router.

    Without --text, starts push-to-talk voice listener (requires microphone and
    keyboard accessibility permissions).

    With --text, processes the given text through the same intent classification
    and routing pipeline — useful for testing, scripting, or when microphone is unavailable.

    With --project, spawns a live mapper subprocess via ProcessManager so voice
    commands route to actual NDJSON I/O instead of returning a CLI fallback.

    Examples::

        lattice orchestrator:voice
        lattice orchestrator:voice --text "map the auth directory"
        lattice orchestrator:voice --text "map status" --project /path/to/project --json
    """
    from lattice.orchestrator.voice.models import VoiceConfig
    from lattice.orchestrator.voice.pipeline import VoicePipeline, format_voice_display
    from lattice.orchestrator.voice.router import IntentRouter

    # Load config (gracefully falls back to defaults if settings unavailable)
    try:
        from lattice.llm.config import LatticeSettings
        settings = LatticeSettings()
        voice_config = settings.voice
    except Exception:
        voice_config = VoiceConfig()

    async def _run_voice() -> None:
        """Run the full voice pipeline in a single event loop."""
        from lattice.orchestrator.manager import ProcessManager
        from lattice.orchestrator.models import OrchestratorConfig

        db_conn: duckdb.DuckDBPyConnection | None = None
        db_file = Path(db_path)
        manager: ProcessManager | None = None
        mapper_procs: dict[str, asyncio.subprocess.Process] = {}

        try:
            if project_root is not None:
                # Single rw connection shared by ProcessManager and IntentRouter
                db_file.parent.mkdir(parents=True, exist_ok=True)
                db_conn = duckdb.connect(str(db_file))
                manager = ProcessManager(db_conn, OrchestratorConfig())
                await manager.spawn_mapper(str(Path(project_root).resolve()))
                mapper_procs = manager.mapper_processes
            elif db_file.exists():
                db_conn = duckdb.connect(str(db_file), read_only=True)

            router = IntentRouter(
                db_conn=db_conn,
                mapper_processes=mapper_procs or None,
            )
            pipeline = VoicePipeline(
                config=voice_config,
                router=router,
                mapper_processes=mapper_procs or None,
            )

            if text_input is not None:
                if mapper_procs:
                    result = await pipeline.process_text_async(text_input)
                else:
                    result = pipeline.process_text(text_input)
                if as_json:
                    click.echo(json.dumps(success_response("orchestrator:voice", {
                        "transcript": text_input,
                        "action": result.action,
                        "success": result.success,
                        "detail": result.detail,
                        "data": result.data,
                    })))
                else:
                    click.echo(format_voice_display(text_input, result))
                return

            # Voice listener mode: start push-to-talk loop
            await pipeline.run_listener()
        finally:
            if manager is not None:
                from lattice.orchestrator.manager import terminate_instance
                for proc in manager.mapper_processes.values():
                    await terminate_instance(proc, timeout=5.0)
            if db_conn is not None:
                db_conn.close()

    asyncio.run(_run_voice())


# ---------------------------------------------------------------------------
# orchestrator:start command
# ---------------------------------------------------------------------------


@cli.command("orchestrator:start")
@click.option("--project", required=True, help="Project name from .lattice/config.yaml")
@click.option(
    "--config",
    "config_path",
    default=".lattice/config.yaml",
    show_default=True,
    help="Path to project config YAML.",
)
@click.option(
    "--no-voice",
    "no_voice",
    is_flag=True,
    help="Disable voice listener (run only process manager and mapper).",
)
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON envelope.")
def orchestrator_start(project: str, config_path: str, no_voice: bool, as_json: bool) -> None:
    """Configure and start an orchestrator for a named project.

    Loads the project configuration from CONFIG_PATH, validates the ProjectConfig
    model, creates the required .lattice/ directory structure, then starts the
    async orchestrator event loop with ProcessManager, Mapper subprocess, and
    (optionally) VoicePipeline.

    The event loop keeps all subprocesses alive and handles graceful shutdown
    on SIGTERM/SIGINT.

    Examples::

        lattice orchestrator:start --project my-project
        lattice orchestrator:start --project my-project --no-voice
        lattice orchestrator:start --project my-project --json
    """
    import yaml
    from lattice.llm.config import ProjectConfig
    from lattice.orchestrator.runner import OrchestratorRunner

    config_file = Path(config_path)

    if not config_file.exists():
        if as_json:
            click.echo(json.dumps(error_response(
                "orchestrator:start",
                "CONFIG_NOT_FOUND",
                f"Project config not found at {config_path}.",
            )))
            return
        raise click.ClickException(f"Project config not found at {config_path}.")

    try:
        with config_file.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        if as_json:
            click.echo(json.dumps(error_response(
                "orchestrator:start",
                "CONFIG_PARSE_ERROR",
                f"Failed to parse {config_path}: {exc}",
            )))
            return
        raise click.ClickException(f"Failed to parse {config_path}: {exc}") from exc

    try:
        project_config = ProjectConfig(**raw)
    except Exception as exc:
        if as_json:
            click.echo(json.dumps(error_response(
                "orchestrator:start",
                "CONFIG_INVALID",
                f"Invalid project config: {exc}",
            )))
            return
        raise click.ClickException(f"Invalid project config: {exc}") from exc

    if project_config.name != project:
        if as_json:
            click.echo(json.dumps(error_response(
                "orchestrator:start",
                "PROJECT_MISMATCH",
                f"Config name '{project_config.name}' does not match --project '{project}'.",
            )))
            return
        raise click.ClickException(
            f"Config name '{project_config.name}' does not match --project '{project}'."
        )

    # Create .lattice/ directory structure
    root = Path(project_config.root)
    lattice_dir = root / ".lattice"
    souls_dir = lattice_dir / "souls"
    souls_dir.mkdir(parents=True, exist_ok=True)

    db_path = str(lattice_dir / "orchestrator.duckdb")

    # Load voice config
    try:
        from lattice.llm.config import LatticeSettings
        voice_config_obj = LatticeSettings().voice
    except Exception:
        from lattice.orchestrator.voice.models import VoiceConfig
        voice_config_obj = VoiceConfig()

    runner = OrchestratorRunner(
        project_root=str(root),
        db_path=db_path,
        voice_config=voice_config_obj,
        voice_enabled=not no_voice,
    )

    if as_json:
        click.echo(json.dumps(success_response("orchestrator:start", {
            "project": project,
            "root": str(root),
            "status": "starting",
            "lattice_dir": str(lattice_dir),
            "voice_enabled": not no_voice,
        })))
    else:
        click.echo(f"Orchestrator starting for project: {project} at {root}")

    asyncio.run(runner.run())
