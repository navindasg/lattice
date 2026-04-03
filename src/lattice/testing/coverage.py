"""CoverageBuilder — computes test coverage of dependency graph edges (TC-03).

Determines which dependency graph edges are transitively exercised by
integration and e2e tests, ranks uncovered edges by edge betweenness
centrality, and serializes results to the _test_coverage.json schema.

Design decisions:
- Unit tests do NOT contribute to edge coverage (only integration and e2e)
- Transitive closure: importing module A covers ALL edges reachable from A
- Edge betweenness centrality (normalized) used to rank coverage gaps
- Entry-point count per gap edge: how many entry points can reach the edge's source
- Serialized output includes metadata with coverage percentage
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import networkx as nx

from lattice.models.coverage import GapEntry, TestCoverage, TestFile

# Test types that contribute to edge coverage
_COVERAGE_TEST_TYPES: frozenset[str] = frozenset({"integration", "e2e"})


class CoverageBuilder:
    """Builds test coverage reports from dependency graphs and classified test files.

    Args:
        dep_graph: Fully annotated dependency graph (NetworkX DiGraph).
            Nodes must have ``is_entry_point`` boolean attribute.
        project_root: Root directory of the project (used for context).
    """

    def __init__(self, dep_graph: nx.DiGraph, project_root: Path) -> None:
        self._graph = dep_graph
        self._project_root = project_root

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_covered_edges(
        self, test_files: list[TestFile]
    ) -> set[tuple[str, str]]:
        """Compute the set of dependency edges transitively covered by tests.

        Only integration and e2e tests contribute to edge coverage. Unit tests
        are explicitly excluded.

        For each qualifying test file, each source_module is treated as a
        starting node. All edges reachable from that node (including itself)
        via transitive closure are added to the covered set.

        Args:
            test_files: Classified test files (from TestClassifier).

        Returns:
            Set of (source, target) edge tuples that are covered.
        """
        covered: set[tuple[str, str]] = set()

        qualifying = [
            tf for tf in test_files if tf.test_type in _COVERAGE_TEST_TYPES
        ]

        for test_file in qualifying:
            for module in test_file.source_modules:
                if module not in self._graph:
                    # Module not in graph — skip gracefully
                    continue

                # Compute transitive closure: all nodes reachable from module
                reachable = nx.descendants(self._graph, module) | {module}

                # Collect all edges in the induced subgraph
                for source, target in self._graph.edges():
                    if source in reachable:
                        covered.add((source, target))

        return covered

    def compute_gap_report(
        self,
        covered_edges: set[tuple[str, str]],
        top_n: int = 10,
    ) -> list[GapEntry]:
        """Compute uncovered edges ranked by betweenness centrality.

        Edges not in ``covered_edges`` are gap candidates. They are ranked by
        edge betweenness centrality (descending), with ties broken alphabetically
        by (source, target) for stability.

        Each gap entry includes an annotation with:
        - Betweenness score (4 decimal places)
        - Number of entry point nodes that can reach the edge's source

        Args:
            covered_edges: Set of (source, target) tuples that are covered.
            top_n: Maximum number of gaps to return.

        Returns:
            List of GapEntry objects sorted by centrality descending, limited to top_n.
        """
        all_edges = set(self._graph.edges())
        uncovered = all_edges - covered_edges

        if not uncovered:
            return []

        # Compute edge betweenness centrality for the full graph
        centrality_map: dict[tuple[str, str], float] = (
            nx.edge_betweenness_centrality(self._graph, normalized=True)
        )

        # Find all entry point nodes
        entry_points = [
            node
            for node, data in self._graph.nodes(data=True)
            if data.get("is_entry_point", False)
        ]

        gaps: list[GapEntry] = []
        for source, target in uncovered:
            centrality = centrality_map.get((source, target), 0.0)

            # Count how many entry points can reach the edge's source
            entry_point_count = sum(
                1
                for ep in entry_points
                if ep == source or nx.has_path(self._graph, ep, source)
            )

            annotation = (
                f"betweenness {centrality:.4f}, "
                f"on path from {entry_point_count} entry point(s)"
            )

            gaps.append(
                GapEntry(
                    source=source,
                    target=target,
                    centrality=centrality,
                    annotation=annotation,
                )
            )

        # Sort by centrality descending, then source/target alphabetically for stability
        gaps.sort(key=lambda g: (-g.centrality, g.source, g.target))

        return gaps[:top_n]

    def build(
        self,
        test_files: list[TestFile],
        top_n: int = 10,
    ) -> TestCoverage:
        """Build a complete TestCoverage report.

        Orchestrates compute_covered_edges → compute_gap_report → TestCoverage.

        Args:
            test_files: Classified test files.
            top_n: Maximum number of gaps to include in the report.

        Returns:
            TestCoverage with test_files, covered_edges, and gaps populated.
        """
        covered_edge_tuples = self.compute_covered_edges(test_files)

        covered_edges_dicts = [
            {"source": src, "target": tgt}
            for src, tgt in sorted(covered_edge_tuples)
        ]

        gaps = self.compute_gap_report(covered_edge_tuples, top_n=top_n)

        return TestCoverage(
            test_files=test_files,
            covered_edges=covered_edges_dicts,
            gaps=gaps,
        )

    @staticmethod
    def serialize(coverage: TestCoverage) -> dict:
        """Serialize a TestCoverage to the _test_coverage.json schema.

        Output schema::

            {
                "metadata": {
                    "analyzed_at": "<ISO 8601 UTC>",
                    "total_edges": <int>,
                    "covered_edges": <int>,
                    "uncovered_edges": <int>,
                    "coverage_pct": <float>
                },
                "test_files": [<TestFile.model_dump()>, ...],
                "covered_edges": [{"source": ..., "target": ...}, ...],
                "gaps": [<GapEntry.model_dump()>, ...]
            }

        Args:
            coverage: TestCoverage instance to serialize.

        Returns:
            Dict ready for json.dump().
        """
        analyzed_at = datetime.now(timezone.utc).isoformat()

        total = len(coverage.covered_edges) + len(coverage.gaps)
        covered_count = len(coverage.covered_edges)
        uncovered_count = len(coverage.gaps)
        coverage_pct = (covered_count / total * 100.0) if total > 0 else 0.0

        return {
            "metadata": {
                "analyzed_at": analyzed_at,
                "total_edges": total,
                "covered_edges": covered_count,
                "uncovered_edges": uncovered_count,
                "coverage_pct": coverage_pct,
            },
            "test_files": [tf.model_dump() for tf in coverage.test_files],
            "covered_edges": list(coverage.covered_edges),
            "gaps": [g.model_dump() for g in coverage.gaps],
        }
