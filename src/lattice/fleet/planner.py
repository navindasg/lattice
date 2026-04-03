"""Wave planner for the agent fleet dispatcher.

Converts a file-level dependency graph into directory-level topological waves,
where each wave can be processed in parallel because dependencies (outbound
edges) are guaranteed to be documented in earlier waves.

Edge direction convention (from RESEARCH.md):
    Edges in _graph.json point from importer to imported (source depends on target).
    After directory aggregation: A -> B means directory A depends on directory B.
    topological_generations() yields B first (wave 0 = leaves = no outgoing deps),
    then A (wave N = root = most dependents). This is correct for bottom-up documentation.

Public API:
    build_directory_dag(file_graph)  — aggregate file edges to directory edges
    plan_waves(dir_graph)            — produce ordered Wave list
    format_wave_plan(wave_plan, tier) — human-readable display with cost estimate
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import networkx as nx
import structlog

from lattice.fleet.models import Wave, WavePlan

logger = structlog.get_logger()


def _node_to_dir(node: str) -> str:
    """Extract parent directory string from a file node path.

    Uses PurePosixPath for cross-platform handling of forward-slash paths
    as stored in _graph.json.

    Args:
        node: File path string as stored in the graph (e.g., "src/auth/session.py").

    Returns:
        Parent directory string (e.g., "src/auth"). For root-level files,
        returns "." (PurePosixPath(".").parent behavior).
    """
    return str(PurePosixPath(node).parent)


# Token cost per million input tokens (approximate, user-configurable in future)
_TIER_COST_PER_MILLION: dict[str, float] = {
    "silver": 3.0,   # ~$3/M input tokens (Claude Haiku class)
    "bronze": 0.15,  # ~$0.15/M input tokens (Ollama local)
    "gold": 15.0,    # ~$15/M input tokens (Claude Sonnet class)
}

# Default token estimate per directory when no file-size data is available
_DEFAULT_TOKENS_PER_DIR = 2000


def build_directory_dag(file_graph: nx.DiGraph) -> nx.DiGraph:
    """Aggregate file-level edges to directory-level edges.

    For each file edge (source, target), computes the parent directories and
    adds a directory-level edge if source_dir != target_dir. Also ensures every
    directory in the file graph appears as a node in the DAG (even if it has no
    cross-directory edges), placing isolated directories in wave 0.

    Self-loops (intra-directory edges) are not added.

    Args:
        file_graph: File-level NetworkX DiGraph from load_graph_from_json().
                    Nodes are file paths (strings). Edges represent imports.

    Returns:
        Directory-level NetworkX DiGraph. Nodes are directory path strings.
        Edge A -> B means directory A contains a file that imports a file in B.
    """
    dag: nx.DiGraph = nx.DiGraph()

    # Walk all nodes: ensure every directory is represented (handles isolated dirs)
    for node in file_graph.nodes():
        dir_path = _node_to_dir(node)
        if not dag.has_node(dir_path):
            dag.add_node(dir_path)

    # Walk all edges: aggregate file-level edges to directory-level edges
    for source, target in file_graph.edges():
        src_dir = _node_to_dir(source)
        tgt_dir = _node_to_dir(target)

        # Skip self-loops (same directory)
        if src_dir == tgt_dir:
            continue

        # Add directory edge (NetworkX handles deduplication via add_edge)
        if not dag.has_edge(src_dir, tgt_dir):
            dag.add_edge(src_dir, tgt_dir)

    return dag


def plan_waves(dir_graph: nx.DiGraph) -> list[Wave]:
    """Convert a directory DAG into ordered topological waves.

    Uses nx.topological_generations() which yields nodes in topological order.
    Directories with no outgoing edges (leaves) appear in wave 0.
    Directories whose dependencies are all in earlier waves appear in later waves.

    Args:
        dir_graph: Directory-level DiGraph from build_directory_dag().

    Returns:
        Ordered list of Wave objects from wave 0 (leaves) to last wave (root).
        Returns empty list for empty graphs.

    Raises:
        ValueError: If the directory graph contains a cycle (not expected in
                    normal usage, but possible with circular directory symlinks).
    """
    if len(dir_graph.nodes) == 0:
        return []

    # Reverse the graph so that leaf directories (depended upon, no outgoing deps)
    # appear in wave 0. topological_generations() yields nodes with no incoming
    # edges first; after reversal, those are the original graph's sinks (leaves).
    reversed_graph = dir_graph.reverse()

    try:
        generations = list(nx.topological_generations(reversed_graph))
    except nx.NetworkXUnfeasible as exc:
        raise ValueError(
            f"Cycle detected in directory dependency graph — cannot compute waves. "
            f"Check for circular directory references. Details: {exc}"
        ) from exc

    waves: list[Wave] = []
    for index, generation in enumerate(generations):
        wave = Wave(
            index=index,
            directories=frozenset(generation),
            estimated_input_tokens=len(generation) * _DEFAULT_TOKENS_PER_DIR,
        )
        waves.append(wave)

    return waves


def format_wave_plan(wave_plan: WavePlan, tier: str) -> str:
    """Format a wave plan for CLI display before any LLM calls are made.

    Shows wave order, directory count per wave, estimated token cost, and
    estimated dollar cost for the selected tier.

    Args:
        wave_plan: The computed WavePlan with all waves and token estimates.
        tier: Model tier string ("silver", "bronze", "gold").

    Returns:
        Multi-line human-readable string suitable for click.echo().
    """
    cost_per_million = _TIER_COST_PER_MILLION.get(tier.lower(), _TIER_COST_PER_MILLION["silver"])
    total_tokens = wave_plan.total_estimated_tokens
    total_cost = (total_tokens / 1_000_000) * cost_per_million

    lines: list[str] = [
        f"Wave Plan  (run_id: {wave_plan.run_id})",
        f"Tier: {tier}  |  Total estimated tokens: {total_tokens:,}  |  "
        f"Estimated cost: ${total_cost:.4f}",
        "",
        f"{'Wave':<8}{'Dirs':<8}{'Est. Tokens':<16}{'Est. Cost':<12}",
        "-" * 48,
    ]

    for wave in wave_plan.waves:
        wave_cost = (wave.estimated_input_tokens / 1_000_000) * cost_per_million
        lines.append(
            f"  {wave.index:<6}{len(wave.directories):<8}"
            f"{wave.estimated_input_tokens:<16,}${wave_cost:<11.4f}"
        )

    lines.append("-" * 48)
    lines.append(f"  Total   {sum(len(w.directories) for w in wave_plan.waves):<8}"
                 f"{total_tokens:<16,}${total_cost:<11.4f}")

    return "\n".join(lines)
