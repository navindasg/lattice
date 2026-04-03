"""Graph serializer — converts a NetworkX DiGraph to a _graph.json-compatible dict.

The output format is designed for consumption by downstream AI agents and tools.
It avoids NetworkX's built-in node_link_data format (research anti-pattern) in
favour of a custom schema with explicit metadata, nodes, and edges sections.

Output schema::

    {
        "metadata": {
            "analyzed_at": "<ISO 8601 UTC timestamp>",
            "file_count": <int>,
            "languages": {"python": <int>, "typescript": <int>, ...},
            "blind_spots": [
                {"file": "<path>", "line": <int>, "expression": "<raw>"},
                ...
            ]
        },
        "nodes": [
            {
                "id": "<relative path>",
                "language": "<python|typescript|javascript|config>",
                "is_entry_point": <bool>,
                "entry_point_type": "<main|route|cli|event_listener|null>",
                "entry_details": <dict|null>,
                "exports": ["<name>", ...]
            },
            ...
        ],
        "edges": [
            {
                "source": "<node id>",
                "target": "<node id>",
                "import_type": "<standard|relative|reexport|decorator|config_ref>"
            },
            ...
        ]
    }
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import networkx as nx

from lattice.models.analysis import FileAnalysis


def serialize_graph(graph: nx.DiGraph, analyses: list[FileAnalysis]) -> dict:
    """Serialize a NetworkX DiGraph to a _graph.json-compatible dict.

    Args:
        graph: The fully annotated dependency graph (after DependencyGraphBuilder,
               EntryPointDetector, and ConfigWiringDetector have all run).
        analyses: The list of FileAnalysis objects that produced the graph.
                  Used to collect blind_spots (dynamic imports) and language counts.

    Returns:
        A dict with metadata, nodes, and edges sections ready for JSON serialization.
    """
    metadata = _build_metadata(graph, analyses)
    nodes = _build_nodes(graph)
    edges = _build_edges(graph)

    return {
        "metadata": metadata,
        "nodes": nodes,
        "edges": edges,
    }


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _build_metadata(graph: nx.DiGraph, analyses: list[FileAnalysis]) -> dict:
    """Build the metadata section."""
    analyzed_at = datetime.now(timezone.utc).isoformat()
    file_count = graph.number_of_nodes()

    # Language counts from graph node attributes
    language_counts: dict[str, int] = defaultdict(int)
    for _, data in graph.nodes(data=True):
        lang = data.get("language", "unknown")
        language_counts[lang] += 1

    # Blind spots: all dynamic imports across all analyses
    blind_spots: list[dict] = []
    for analysis in analyses:
        for imp in analysis.imports:
            if imp.import_type == "dynamic":
                blind_spots.append({
                    "file": analysis.path,
                    "line": imp.line_number,
                    "expression": imp.raw_expression or imp.module,
                })

    return {
        "analyzed_at": analyzed_at,
        "file_count": file_count,
        "languages": dict(language_counts),
        "blind_spots": blind_spots,
    }


def _build_nodes(graph: nx.DiGraph) -> list[dict]:
    """Build the nodes section from graph node data."""
    nodes: list[dict] = []
    for node_id, data in graph.nodes(data=True):
        nodes.append({
            "id": node_id,
            "language": data.get("language", "unknown"),
            "is_entry_point": data.get("is_entry_point", False),
            "entry_point_type": data.get("entry_point_type"),
            "entry_details": data.get("entry_details"),
            "exports": list(data.get("exports", [])),
        })
    return nodes


def _build_edges(graph: nx.DiGraph) -> list[dict]:
    """Build the edges section from graph edge data."""
    edges: list[dict] = []
    for source, target, data in graph.edges(data=True):
        edges.append({
            "source": source,
            "target": target,
            "import_type": data.get("import_type", "standard"),
        })
    return edges
