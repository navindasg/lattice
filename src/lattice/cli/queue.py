"""Core logic for the map:queue and map:test-status commands.

Writes a queue entry to .agent-docs/_queue.json after each commit,
capturing which directories were affected and their 1-hop upstream
consumers. The queue is consumed by map:doc --incremental.

Architecture: "queue-only, never dispatch from hook" — the post-commit
hook calls map:queue; actual re-documentation is a separate manual step.

Also provides _map_test_status_impl which deterministically recomputes
test coverage from the file graph and updates integration_points in
affected _dir.md files. No LLM calls are made.

Exports:
    _read_queue          — load _queue.json from disk
    _write_queue         — atomically write _queue.json via os.replace
    _map_queue_impl       — core queue-write logic
    _map_test_status_impl — recompute coverage and update _dir.md integration_points
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import structlog

from lattice.testing import CoverageBuilder, TestClassifier, TestDiscovery

log = structlog.get_logger(__name__)


def _read_queue(queue_path: Path) -> dict:
    """Read _queue.json from disk, returning empty entries dict on missing/corrupt file.

    Args:
        queue_path: Path to the _queue.json file.

    Returns:
        Dict with "entries" list, or {"entries": []} on missing/corrupt file.
    """
    if queue_path.exists():
        try:
            return json.loads(queue_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"entries": []}
    return {"entries": []}


def _write_queue(queue: dict, queue_path: Path, tmp_path: Path) -> None:
    """Atomically write queue dict to disk via tmp file and os.replace().

    Args:
        queue: Queue dict to serialize.
        queue_path: Target path for _queue.json.
        tmp_path: Temporary file path used for atomic write.
    """
    tmp_path.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp_path), str(queue_path))


def _map_queue_impl(target: Path, commit_hash: str, changed_files: list[str]) -> dict:
    """Write a queue entry for the given commit and changed files.

    Computes affected directories from file paths, traces 1-hop upstream
    consumers via the directory DAG, and coalesces entries that share
    overlapping affected_directories.

    Args:
        target: Path to the project root directory.
        commit_hash: Git commit hash for this change.
        changed_files: List of changed file paths (relative to project root).

    Returns:
        Dict with queued_directories, upstream_stale, commit_hash,
        and test_status_triggered.
    """
    agent_docs = target / ".agent-docs"
    agent_docs.mkdir(parents=True, exist_ok=True)

    queue_path = agent_docs / "_queue.json"
    tmp_path = agent_docs / "_queue.json.tmp"

    # Compute affected directories from changed file paths
    affected_directories: set[str] = {
        str(PurePosixPath(f).parent) for f in changed_files
    }

    # Compute upstream consumers via directory DAG (1-hop predecessors)
    upstream_consumers: set[str] = set()
    graph_path = agent_docs / "_graph.json"

    if graph_path.exists():
        try:
            from lattice.cli.commands import load_graph_from_json
            from lattice.fleet.planner import build_directory_dag

            file_graph = load_graph_from_json(graph_path)
            dir_dag = build_directory_dag(file_graph)

            for affected_dir in affected_directories:
                if dir_dag.has_node(affected_dir):
                    for predecessor in dir_dag.predecessors(affected_dir):
                        upstream_consumers.add(predecessor)

            # Upstream consumers exclude the affected directories themselves
            upstream_consumers -= affected_directories

        except Exception as exc:
            log.warning(
                "failed to compute upstream consumers from graph",
                error=str(exc),
                commit_hash=commit_hash,
            )
    else:
        log.warning(
            "no _graph.json found — upstream consumers not computed",
            target=str(target),
        )

    # Detect test file changes
    has_test_files = any(_is_test_file(f) for f in changed_files)
    test_status_triggered = has_test_files

    # Auto-trigger map:test-status when test files change and graph exists
    test_status_result: dict | None = None
    if has_test_files and graph_path.exists():
        try:
            test_status_result = _map_test_status_impl(target, changed_files=changed_files)
        except Exception as exc:
            log.warning(
                "map:test-status auto-trigger failed",
                error=str(exc),
                commit_hash=commit_hash,
            )

    # Read existing queue
    queue_data = _read_queue(queue_path)

    # Build new entry
    new_entry: dict[str, Any] = {
        "commit_hash": commit_hash,
        "changed_files": sorted(changed_files),
        "affected_directories": sorted(affected_directories),
        "upstream_consumers": sorted(upstream_consumers),
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }

    # Coalescing: merge entries that share overlapping affected_directories
    entries: list[dict] = queue_data.get("entries", [])
    merged = False

    for i, existing_entry in enumerate(entries):
        if existing_entry.get("status") != "pending":
            continue

        existing_dirs = set(existing_entry.get("affected_directories", []))
        if existing_dirs & affected_directories:
            # Overlapping — merge into existing entry
            merged_files = sorted(
                set(existing_entry.get("changed_files", [])) | set(changed_files)
            )
            merged_dirs = sorted(
                existing_dirs | affected_directories
            )
            merged_upstream = sorted(
                set(existing_entry.get("upstream_consumers", [])) | upstream_consumers
            )
            # Keep earliest queued_at
            existing_queued_at = existing_entry.get("queued_at", new_entry["queued_at"])
            if new_entry["queued_at"] < existing_queued_at:
                keep_queued_at = new_entry["queued_at"]
            else:
                keep_queued_at = existing_queued_at

            merged_entry: dict[str, Any] = {
                "commit_hash": commit_hash,
                "changed_files": merged_files,
                "affected_directories": merged_dirs,
                "upstream_consumers": merged_upstream,
                "queued_at": keep_queued_at,
                "status": "pending",
            }
            entries[i] = merged_entry
            affected_directories = set(merged_dirs)
            upstream_consumers = set(merged_upstream)
            merged = True
            break

    if not merged:
        entries = [*entries, new_entry]

    _write_queue({"entries": entries}, queue_path, tmp_path)

    result: dict[str, Any] = {
        "queued_directories": sorted(affected_directories),
        "upstream_stale": sorted(upstream_consumers),
        "commit_hash": commit_hash,
        "test_status_triggered": test_status_triggered,
    }
    if test_status_result is not None:
        result["test_status_result"] = test_status_result
    return result


def _is_test_file(path: str) -> bool:
    """Return True if the file path matches test file naming conventions.

    Args:
        path: File path string.

    Returns:
        True if filename starts with 'test_' or ends with '_test.py'.
    """
    filename = PurePosixPath(path).name
    return filename.startswith("test_") or filename.endswith("_test.py")


def _map_test_status_impl(
    target: Path,
    changed_files: list[str] | None = None,
) -> dict:
    """Recompute test coverage and update integration_points in _dir.md files.

    Deterministic — no LLM calls. Uses CoverageBuilder to compute which
    dependency graph edges are covered by integration/e2e tests, then
    writes the result to _test_coverage.json and updates integration_points
    in affected _dir.md files.

    Args:
        target: Project root directory.
        changed_files: Optional list of changed file paths (relative to target).
            If provided, used to filter which _dir.md files are updated
            (optimization). All test files are still used for full coverage.

    Returns:
        Dict with updated_directories list and total_covered_edges count,
        or error dict with code GRAPH_NOT_FOUND if _graph.json is missing.
    """
    agent_docs = target / ".agent-docs"
    graph_path = agent_docs / "_graph.json"

    if not graph_path.exists():
        return {"error": "GRAPH_NOT_FOUND", "updated_directories": []}

    # Lazy imports to avoid circular dependency
    from lattice.cli.commands import load_graph_from_json
    from lattice.shadow.reader import parse_dir_doc
    from lattice.shadow.writer import write_dir_doc

    # Load graph
    file_graph = load_graph_from_json(graph_path)
    graph_node_keys: set[str] = set(file_graph.nodes())

    # Discover and classify all test files for accurate coverage
    all_discovered = TestDiscovery(target).discover()
    test_files = TestClassifier(target, graph_node_keys).classify_all(all_discovered)

    # Compute coverage
    builder = CoverageBuilder(file_graph, target)
    coverage = builder.build(test_files)

    # Serialize and write _test_coverage.json
    serialized = CoverageBuilder.serialize(coverage)
    coverage_path = agent_docs / "_test_coverage.json"
    with coverage_path.open("w", encoding="utf-8") as f:
        json.dump(serialized, f, indent=2)

    # Build set of covered (src, tgt) tuples for fast lookup
    covered_edges: set[tuple[str, str]] = {
        (e["source"], e["target"]) for e in coverage.covered_edges
    }

    # Build a helper to find a covering test file for an edge
    def _find_covering_test(src: str, tgt: str) -> str | None:
        """Return relative path of first integration/e2e test that covers this edge."""
        from lattice.models.coverage import TestFile
        import networkx as nx

        for tf in test_files:
            if tf.test_type not in ("integration", "e2e"):
                continue
            for module in tf.source_modules:
                if module not in file_graph:
                    continue
                reachable = nx.descendants(file_graph, module) | {module}
                if src in reachable:
                    return tf.path
        return None

    # Group cross-directory edges by source directory
    dir_integration_points: dict[str, list[dict]] = {}
    for src, tgt in file_graph.edges():
        src_dir = str(PurePosixPath(src).parent)
        tgt_dir = str(PurePosixPath(tgt).parent)
        if src_dir == tgt_dir:
            continue  # Only include cross-directory edges

        status = "TESTED" if (src, tgt) in covered_edges else "UNTESTED"
        test_file = _find_covering_test(src, tgt) if status == "TESTED" else None

        edge_str = f"{src} -> {tgt}"
        point = {"edge": edge_str, "status": status, "test_file": test_file}
        dir_integration_points.setdefault(src_dir, []).append(point)

    # Update affected _dir.md files
    updated_dirs = []
    for directory, points in dir_integration_points.items():
        dir_md = agent_docs / directory / "_dir.md"
        if not dir_md.exists():
            continue
        try:
            doc = parse_dir_doc(dir_md)
            # model_copy preserves last_analyzed (not updated)
            updated = doc.model_copy(update={"integration_points": points})
            write_dir_doc(updated, agent_docs)
            tested_count = sum(1 for p in points if p["status"] == "TESTED")
            untested_count = sum(1 for p in points if p["status"] == "UNTESTED")
            updated_dirs.append({
                "directory": directory,
                "tested": tested_count,
                "untested": untested_count,
            })
        except Exception as exc:
            log.warning(
                "failed to update integration_points in _dir.md",
                directory=directory,
                error=str(exc),
            )

    return {
        "updated_directories": updated_dirs,
        "total_covered_edges": len(covered_edges),
    }
