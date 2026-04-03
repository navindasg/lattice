"""EntryPointDetector — annotates graph nodes with entry point metadata.

Reads signals from graph nodes (set by DependencyGraphBuilder) and from
FileAnalysis.imports (import_type="decorator") to classify entry points.

Entry point types detected:
    "main"           — node has has_main_guard=True (from FileAnalysis.has_main_guard)
    "route"          — @app.route, @router.get, @router.post, etc.
    "cli"            — @click.command, @click.group
    "event_listener" — @celery.task and similar async task decorators

Detection strategy:
    1. Check has_main_guard attribute on graph node (already carried by builder)
       → sets entry_point_type="main"; this takes precedence over decorators
    2. Scan FileAnalysis.imports for import_type="decorator"
       → classify by module/attr name patterns

NOTE: Graph node mutation is a documented exception to immutability rules.
NetworkX stores node attributes in a mutable dict; EntryPointDetector mutates
them in-place rather than rebuilding the graph, which is the standard NetworkX
pattern for annotation passes.
"""
from __future__ import annotations

import networkx as nx

from lattice.models.analysis import FileAnalysis, ImportInfo


# Decorator attribute names that map to route entry points
_ROUTE_DECORATORS = frozenset({
    "route",
    "get", "post", "put", "delete", "patch",
    "head", "options",
})

# Decorator attribute names that map to CLI entry points
_CLI_DECORATORS = frozenset({
    "command", "group",
})

# Decorator attribute names that map to event listener entry points
_EVENT_DECORATORS = frozenset({
    "task", "on", "listener", "periodic_task",
})


def _classify_decorator(imp: ImportInfo) -> str | None:
    """Classify a decorator import into an entry point type.

    Parses the module field (e.g., "app.route", "click.command", "celery.task")
    to determine the entry point category.

    Args:
        imp: ImportInfo with import_type="decorator"

    Returns:
        Entry point type string or None if not a recognised entry pattern.
    """
    # module field is set to "object.method" or just "method" by PythonAdapter
    parts = imp.module.split(".")
    attr = parts[-1].lower() if parts else ""

    if attr in _ROUTE_DECORATORS:
        return "route"
    if attr in _CLI_DECORATORS:
        return "cli"
    if attr in _EVENT_DECORATORS:
        return "event_listener"
    return None


class EntryPointDetector:
    """Annotates graph nodes with entry point metadata.

    Usage::

        detector = EntryPointDetector()
        detector.detect(graph, analyses)
        # graph nodes now have is_entry_point, entry_point_type, entry_details
    """

    def detect(self, graph: nx.DiGraph, analyses: list[FileAnalysis]) -> None:
        """Annotate graph nodes in-place with entry point metadata.

        Priority: has_main_guard=True on graph node takes precedence over
        decorator-based detection for the same node.

        Args:
            graph: DiGraph built by DependencyGraphBuilder; mutated in-place.
            analyses: List of FileAnalysis objects corresponding to graph nodes.
        """
        # Build a lookup from node key to FileAnalysis for decorator scanning
        analysis_by_path: dict[str, FileAnalysis] = {a.path: a for a in analyses}

        for node_key, node_data in graph.nodes(data=True):
            # Priority 1: main guard signal (already on the node from builder)
            if node_data.get("has_main_guard"):
                graph.nodes[node_key]["is_entry_point"] = True
                graph.nodes[node_key]["entry_point_type"] = "main"
                continue

            # Priority 2: decorator-based detection
            # The analysis path may match node_key directly or via the raw path
            analysis = analysis_by_path.get(node_key)
            if analysis is None:
                # Try matching by node_key as a suffix of the analysis path
                for path, a in analysis_by_path.items():
                    if path.endswith(node_key) or node_key.endswith(path):
                        analysis = a
                        break

            if analysis is None:
                continue

            # Collect decorator imports sorted by line number (lowest first)
            decorators = sorted(
                (imp for imp in analysis.imports if imp.import_type == "decorator"),
                key=lambda i: i.line_number,
            )

            for dec in decorators:
                ep_type = _classify_decorator(dec)
                if ep_type is not None:
                    graph.nodes[node_key]["is_entry_point"] = True
                    graph.nodes[node_key]["entry_point_type"] = ep_type
                    break  # first matching decorator wins
