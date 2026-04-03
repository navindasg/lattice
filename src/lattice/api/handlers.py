"""Per-command dispatch handlers for the Lattice Mapper HTTP API.

Each handler receives the raw payload dict from the CommandRequest and a
FastAPI BackgroundTasks instance.  Handlers call into the same _impl()
functions used by the CLI, ensuring CLI and HTTP behaviour is identical.

Exports:
    handle_map_init   — run analysis pipeline and return graph envelope
    handle_map_status — return pipeline status envelope
    handle_map_hint   — store developer hint and return envelope
    handle_map_doc    — fire-and-forget doc dispatch, returns run_id immediately
    handle_map_gaps   — compute test coverage gaps and return envelope
    handle_map_cross  — detect cross-cutting patterns and return envelope
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import structlog
from fastapi import BackgroundTasks

from lattice.api.models import error_response, success_response
from lattice.cli.commands import _map_init_impl, _walk_files, load_graph_from_json
from lattice.cli.hints import _map_correct_impl, _map_hint_impl, _map_skip_impl
from lattice.cli.queue import _map_queue_impl, _map_test_status_impl
from lattice.cli.status import _map_status_impl

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# map:init
# ---------------------------------------------------------------------------


async def handle_map_init(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Run analysis pipeline and persist _graph.json.

    Args:
        payload: Must contain "target" key (path string).
        background_tasks: FastAPI background task registry (unused here).

    Returns:
        success_response envelope with graph data.
    """
    target = Path(payload.get("target", ".")).resolve()
    graph_data = _map_init_impl(target)

    # Write _graph.json (same location as CLI)
    output_dir = target / ".agent-docs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "_graph.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(graph_data, f, indent=2)

    return success_response("map:init", graph_data)


# ---------------------------------------------------------------------------
# map:status
# ---------------------------------------------------------------------------


async def handle_map_status(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Return pipeline status envelope.

    Args:
        payload: Must contain "target" key (path string).
        background_tasks: FastAPI background task registry (unused here).

    Returns:
        success_response envelope with status data.
    """
    target = Path(payload.get("target", ".")).resolve()
    return success_response("map:status", _map_status_impl(target))


# ---------------------------------------------------------------------------
# map:hint
# ---------------------------------------------------------------------------


async def handle_map_hint(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Store a developer hint for a directory.

    Args:
        payload: Must contain "target", "directory", and "text" keys.
        background_tasks: FastAPI background task registry (unused here).

    Returns:
        success_response envelope with directory and hint_count, or error
        envelope with code INVALID_PAYLOAD if directory/text are missing.
    """
    target = Path(payload.get("target", ".")).resolve()
    directory: str | None = payload.get("directory")
    text: str | None = payload.get("text")

    if not directory or not text:
        return error_response(
            "map:hint",
            "INVALID_PAYLOAD",
            "directory and text are required",
        )

    return success_response("map:hint", _map_hint_impl(target, directory, text))


# ---------------------------------------------------------------------------
# map:doc
# ---------------------------------------------------------------------------


async def handle_map_doc(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Fire-and-forget fleet dispatch.  Returns run_id immediately.

    Checks for _graph.json existence before accepting the request.
    The actual fleet dispatch runs in the background via BackgroundTasks.

    Args:
        payload: Must contain "target".  Optionally "tier", "run_id",
                 "concurrency".
        background_tasks: FastAPI background task registry.

    Returns:
        success_response with {"run_id": ..., "status": "started"}, or
        error_response with code GRAPH_NOT_FOUND.
    """
    target = Path(payload.get("target", ".")).resolve()
    graph_path = target / ".agent-docs" / "_graph.json"

    if not graph_path.exists():
        return error_response(
            "map:doc",
            "GRAPH_NOT_FOUND",
            f"No _graph.json at {target}. Run map:init first.",
        )

    run_id: str = payload.get("run_id") or str(uuid.uuid4())
    tier: str = payload.get("tier", "bronze")
    concurrency: int = int(payload.get("concurrency", 8))

    if background_tasks is not None:
        background_tasks.add_task(
            _run_map_doc_background,
            target,
            tier,
            run_id,
            concurrency,
        )
    else:
        asyncio.create_task(
            _run_map_doc_background(target, tier, run_id, concurrency)
        )

    return success_response("map:doc", {"run_id": run_id, "status": "started"})


async def _run_map_doc_background(
    target: Path,
    tier: str,
    run_id: str,
    concurrency: int,
) -> None:
    """Run the map:doc fleet dispatch in the background.

    Imports heavy dependencies inside the function to avoid circular imports
    at module-load time.  All errors are caught and logged — the server must
    not crash when a background task fails.

    Args:
        target: Project root directory.
        tier: Model tier ("bronze" or "silver").
        run_id: UUID for this dispatch run.
        concurrency: Maximum concurrent agent calls per wave.
    """
    try:
        import duckdb

        from lattice.fleet.assembler import DocumentAssembler
        from lattice.fleet.checkpoint import FleetCheckpoint
        from lattice.fleet.dispatcher import FleetDispatcher
        from lattice.fleet.models import WavePlan
        from lattice.fleet.planner import build_directory_dag, plan_waves
        from lattice.fleet.skeleton import SkeletonWriter

        agent_docs = target / ".agent-docs"
        graph_path = agent_docs / "_graph.json"

        file_graph = load_graph_from_json(graph_path)

        # Build directory DAG and waves
        dir_dag = build_directory_dag(file_graph)
        waves = plan_waves(dir_dag)
        total_tokens = sum(w.estimated_input_tokens for w in waves)
        wave_plan = WavePlan(waves=waves, total_estimated_tokens=total_tokens, run_id=run_id)

        # Set up checkpoint
        db_path = agent_docs / "fleet.duckdb"
        conn = duckdb.connect(str(db_path))
        fleet_checkpoint = FleetCheckpoint(conn)

        completed_wave_indices = set(fleet_checkpoint.get_completed_waves(run_id))

        dispatcher = FleetDispatcher(
            tier=tier,
            project_root=target,
            file_graph=file_graph,
            coverage_data={},
            agent_docs_root=agent_docs,
            checkpoint=fleet_checkpoint,
            concurrency_cap=concurrency,
        )
        assembler = DocumentAssembler()
        skeleton_writer = SkeletonWriter()

        for wave in wave_plan.waves:
            if wave.index in completed_wave_indices:
                continue

            results = await dispatcher.dispatch_wave(wave, run_id=run_id)
            assembler.assemble_wave(results, agent_docs)

            for result in results:
                if not result.failed and result.dir_doc is not None:
                    skeleton_writer.write_stubs(result, agent_docs)

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

        conn.close()

    except Exception as exc:
        log.error("map:doc background task failed", run_id=run_id, error=str(exc))


# ---------------------------------------------------------------------------
# map:gaps
# ---------------------------------------------------------------------------


async def handle_map_gaps(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Compute test coverage gaps.

    Args:
        payload: Must contain "target" key.
        background_tasks: FastAPI background task registry (unused here).

    Returns:
        success_response with serialized coverage data, or error_response
        with code GRAPH_NOT_FOUND.
    """
    target = Path(payload.get("target", ".")).resolve()
    agent_docs = target / ".agent-docs"
    graph_path = agent_docs / "_graph.json"

    if not graph_path.exists():
        return error_response(
            "map:gaps",
            "GRAPH_NOT_FOUND",
            f"No _graph.json at {target}. Run map:init first.",
        )

    from lattice.testing import CoverageBuilder, TestClassifier, TestDiscovery

    graph = load_graph_from_json(graph_path)
    graph_node_keys: set[str] = set(graph.nodes())

    discovered_paths = TestDiscovery(target).discover()
    test_files = TestClassifier(target, graph_node_keys).classify_all(discovered_paths)

    builder = CoverageBuilder(graph, target)
    coverage = builder.build(test_files)
    serialized = CoverageBuilder.serialize(coverage)

    return success_response("map:gaps", serialized)


# ---------------------------------------------------------------------------
# map:cross
# ---------------------------------------------------------------------------


async def handle_map_cross(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Detect cross-cutting patterns and write _project.md.

    Args:
        payload: Must contain "target" key.
        background_tasks: FastAPI background task registry (unused here).

    Returns:
        success_response with summary counts, or error_response with
        code GRAPH_NOT_FOUND.
    """
    target = Path(payload.get("target", ".")).resolve()
    agent_docs = target / ".agent-docs"
    graph_path = agent_docs / "_graph.json"

    if not graph_path.exists():
        return error_response(
            "map:cross",
            "GRAPH_NOT_FOUND",
            f"No _graph.json at {target}. Run map:init first.",
        )

    from lattice.cross_cutting import (
        CrossCuttingAnalyzer,
        build_cross_cutting_edges,
        enrich_dir_docs_if_present,
        write_project_doc,
    )

    with graph_path.open(encoding="utf-8") as f:
        graph_data = json.load(f)

    source_files = _walk_files(target)

    analyzer = CrossCuttingAnalyzer(target)
    project_doc = analyzer.analyze(graph_data, source_files)

    agent_docs.mkdir(parents=True, exist_ok=True)
    project_md_path = write_project_doc(project_doc, agent_docs)

    cross_cutting_edges = build_cross_cutting_edges(project_doc)
    updated_graph_data = {**graph_data, "cross_cutting_edges": cross_cutting_edges}
    with graph_path.open("w", encoding="utf-8") as f:
        json.dump(updated_graph_data, f, indent=2)

    enrich_dir_docs_if_present(project_doc, agent_docs)

    data = {
        "event_flows": len(project_doc.event_flows),
        "shared_state": len(project_doc.shared_state),
        "api_contracts": len(project_doc.api_contracts),
        "plugin_points": len(project_doc.plugin_points),
        "blind_spots": len(project_doc.blind_spots),
        "output": str(project_md_path),
    }

    return success_response("map:cross", data)


# ---------------------------------------------------------------------------
# map:correct
# ---------------------------------------------------------------------------


async def handle_map_correct(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Update a _dir.md field with a developer correction.

    Args:
        payload: Must contain "target", "directory", "field", and "value" keys.
        background_tasks: FastAPI background task registry (unused here).

    Returns:
        success_response envelope with directory/field/confidence/source, or
        error_response with code INVALID_PAYLOAD, NO_DOCUMENTATION, or
        INVALID_FIELD.
    """
    target = Path(payload.get("target", ".")).resolve()
    directory: str | None = payload.get("directory")
    field: str | None = payload.get("field")
    value: str | None = payload.get("value")

    if not directory or not field or not value:
        return error_response(
            "map:correct",
            "INVALID_PAYLOAD",
            "directory, field, and value are required",
        )

    try:
        return success_response("map:correct", _map_correct_impl(target, directory, field, value))
    except FileNotFoundError as exc:
        return error_response("map:correct", "NO_DOCUMENTATION", str(exc))
    except ValueError as exc:
        return error_response("map:correct", "INVALID_FIELD", str(exc))


# ---------------------------------------------------------------------------
# map:skip
# ---------------------------------------------------------------------------


async def handle_map_queue(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Queue a commit for incremental re-documentation.

    Args:
        payload: Must contain "target", "commit_hash", and "changed_files" keys.
        background_tasks: FastAPI background task registry (unused here).

    Returns:
        success_response envelope with queued_directories and upstream_stale, or
        error_response with code INVALID_PAYLOAD if required fields are missing.
    """
    target = Path(payload.get("target", ".")).resolve()
    commit_hash: str | None = payload.get("commit_hash")
    changed_files = payload.get("changed_files", [])

    if not commit_hash:
        return error_response(
            "map:queue",
            "INVALID_PAYLOAD",
            "commit_hash is required",
        )

    if not isinstance(changed_files, list):
        return error_response(
            "map:queue",
            "INVALID_PAYLOAD",
            "changed_files must be a list",
        )

    result = _map_queue_impl(target, commit_hash, changed_files)
    return success_response("map:queue", result)


async def handle_map_test_status(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Recompute test coverage and update integration_points in _dir.md files.

    Args:
        payload: Must contain "target" key (path string).
        background_tasks: FastAPI background task registry (unused here).

    Returns:
        success_response envelope with updated_directories and total_covered_edges,
        or error_response with code GRAPH_NOT_FOUND if _graph.json is missing.
    """
    target = Path(payload.get("target", ".")).resolve()
    result = _map_test_status_impl(target)
    if result.get("error") == "GRAPH_NOT_FOUND":
        return error_response(
            "map:test-status",
            "GRAPH_NOT_FOUND",
            f"No _graph.json at {target}. Run map:init first.",
        )
    return success_response("map:test-status", result)


async def handle_map_skip(payload: dict, background_tasks: BackgroundTasks) -> dict:
    """Mark a directory as low-priority (skip) in _hints.json.

    Args:
        payload: Must contain "target" and "directory" keys.
        background_tasks: FastAPI background task registry (unused here).

    Returns:
        success_response envelope with directory and skipped=True, or
        error_response with code INVALID_PAYLOAD if directory is missing.
    """
    target = Path(payload.get("target", ".")).resolve()
    directory: str | None = payload.get("directory")

    if not directory:
        return error_response(
            "map:skip",
            "INVALID_PAYLOAD",
            "directory is required",
        )

    return success_response("map:skip", _map_skip_impl(target, directory))
