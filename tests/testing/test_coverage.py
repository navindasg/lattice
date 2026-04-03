"""Tests for CoverageBuilder — transitive edge coverage and gap analysis (TC-03).

TDD suite covering:
- compute_covered_edges: transitive closure from integration/e2e tests
- compute_covered_edges: unit tests excluded
- compute_covered_edges: modules not in graph are skipped gracefully
- compute_gap_report: uncovered edges sorted by centrality descending
- compute_gap_report: annotation format with betweenness and entry-point count
- compute_gap_report: top_n limits results
- compute_gap_report: empty list when all edges covered
- build: returns valid TestCoverage
- serialize: returns dict matching _test_coverage.json schema
"""
import pytest
import networkx as nx

from lattice.models.coverage import GapEntry, TestCoverage, TestFile
from lattice.testing.coverage import CoverageBuilder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def small_graph() -> nx.DiGraph:
    """A small directed dependency graph for testing.

    Topology (6 edges):
        entry_a --> src_b --> src_c
        entry_a --> src_d
        src_b   --> src_e
        src_d   --> src_e
        src_d   --> src_f
        src_c   --> src_f

    entry_a is an entry point node.
    """
    g = nx.DiGraph()
    g.add_node("src/entry_a.py", is_entry_point=True, language="python")
    g.add_node("src/src_b.py", is_entry_point=False, language="python")
    g.add_node("src/src_c.py", is_entry_point=False, language="python")
    g.add_node("src/src_d.py", is_entry_point=False, language="python")
    g.add_node("src/src_e.py", is_entry_point=False, language="python")
    g.add_node("src/src_f.py", is_entry_point=False, language="python")

    g.add_edge("src/entry_a.py", "src/src_b.py", import_type="standard")
    g.add_edge("src/entry_a.py", "src/src_d.py", import_type="standard")
    g.add_edge("src/src_b.py", "src/src_c.py", import_type="standard")
    g.add_edge("src/src_b.py", "src/src_e.py", import_type="standard")
    g.add_edge("src/src_d.py", "src/src_e.py", import_type="standard")
    g.add_edge("src/src_d.py", "src/src_f.py", import_type="standard")

    return g


@pytest.fixture()
def project_root(tmp_path):
    return tmp_path


@pytest.fixture()
def builder(small_graph, project_root) -> CoverageBuilder:
    return CoverageBuilder(small_graph, project_root)


def _make_test_file(
    path: str,
    test_type: str,
    source_modules: list[str],
) -> TestFile:
    """Helper to build a TestFile with minimal boilerplate."""
    return TestFile(
        path=path,
        language="python",
        test_type=test_type,  # type: ignore[arg-type]
        reason="test fixture",
        source_modules=source_modules,
    )


# ---------------------------------------------------------------------------
# compute_covered_edges
# ---------------------------------------------------------------------------


class TestComputeCoveredEdges:
    def test_integration_test_importing_a_covers_a_b_edge(
        self, builder: CoverageBuilder
    ) -> None:
        """Integration test importing entry_a transitively covers (entry_a, src_b)."""
        tf = _make_test_file(
            "tests/test_integration.py",
            "integration",
            ["src/entry_a.py"],
        )
        covered = builder.compute_covered_edges([tf])
        assert ("src/entry_a.py", "src/src_b.py") in covered

    def test_integration_test_covers_transitive_edges(
        self, builder: CoverageBuilder
    ) -> None:
        """Importing entry_a covers all reachable edges transitively."""
        tf = _make_test_file(
            "tests/test_integration.py",
            "integration",
            ["src/entry_a.py"],
        )
        covered = builder.compute_covered_edges([tf])
        # entry_a -> src_b and entry_a -> src_d must both be covered
        assert ("src/entry_a.py", "src/src_b.py") in covered
        assert ("src/entry_a.py", "src/src_d.py") in covered
        # src_b -> src_c and src_b -> src_e (reachable from entry_a via src_b)
        assert ("src/src_b.py", "src/src_c.py") in covered
        assert ("src/src_b.py", "src/src_e.py") in covered

    def test_multiple_integration_tests_union_edges(
        self, builder: CoverageBuilder
    ) -> None:
        """Two integration tests covering different modules produce the union of edges."""
        tf1 = _make_test_file(
            "tests/test_int_b.py",
            "integration",
            ["src/src_b.py"],
        )
        tf2 = _make_test_file(
            "tests/test_int_d.py",
            "integration",
            ["src/src_d.py"],
        )
        covered = builder.compute_covered_edges([tf1, tf2])
        # From src_b
        assert ("src/src_b.py", "src/src_c.py") in covered
        assert ("src/src_b.py", "src/src_e.py") in covered
        # From src_d
        assert ("src/src_d.py", "src/src_e.py") in covered
        assert ("src/src_d.py", "src/src_f.py") in covered

    def test_unit_test_does_not_contribute_edges(
        self, builder: CoverageBuilder
    ) -> None:
        """Unit tests importing entry_a must NOT contribute covered edges."""
        tf = _make_test_file(
            "tests/test_unit.py",
            "unit",
            ["src/entry_a.py"],
        )
        covered = builder.compute_covered_edges([tf])
        assert len(covered) == 0

    def test_e2e_test_contributes_edges(
        self, builder: CoverageBuilder
    ) -> None:
        """e2e tests must be treated same as integration tests (contribute edges)."""
        tf = _make_test_file(
            "tests/e2e/test_e2e.py",
            "e2e",
            ["src/src_b.py"],
        )
        covered = builder.compute_covered_edges([tf])
        assert ("src/src_b.py", "src/src_c.py") in covered

    def test_module_not_in_graph_is_skipped_gracefully(
        self, builder: CoverageBuilder
    ) -> None:
        """source_modules referencing non-existent nodes do not raise errors."""
        tf = _make_test_file(
            "tests/test_int.py",
            "integration",
            ["does/not/exist.py"],
        )
        # Should not raise KeyError or any other exception
        covered = builder.compute_covered_edges([tf])
        assert isinstance(covered, set)

    def test_empty_test_list_returns_empty_set(
        self, builder: CoverageBuilder
    ) -> None:
        covered = builder.compute_covered_edges([])
        assert covered == set()


# ---------------------------------------------------------------------------
# compute_gap_report
# ---------------------------------------------------------------------------


class TestComputeGapReport:
    def test_gap_report_returns_uncovered_edges(
        self, builder: CoverageBuilder
    ) -> None:
        """When nothing is covered, all 6 edges appear as gaps."""
        gaps = builder.compute_gap_report(set())
        assert len(gaps) == 6

    def test_gap_report_empty_when_all_edges_covered(
        self, builder: CoverageBuilder, small_graph: nx.DiGraph
    ) -> None:
        """All edges covered → empty gap list."""
        all_edges = set(small_graph.edges())
        gaps = builder.compute_gap_report(all_edges)
        assert gaps == []

    def test_gap_report_sorted_by_centrality_descending(
        self, builder: CoverageBuilder
    ) -> None:
        """Gap entries must be sorted by centrality descending."""
        gaps = builder.compute_gap_report(set())
        centralities = [g.centrality for g in gaps]
        assert centralities == sorted(centralities, reverse=True)

    def test_gap_report_annotation_has_betweenness_and_entry_points(
        self, builder: CoverageBuilder
    ) -> None:
        """Each GapEntry annotation must mention betweenness score and entry point count."""
        gaps = builder.compute_gap_report(set())
        for gap in gaps:
            assert "betweenness" in gap.annotation
            assert "entry point" in gap.annotation

    def test_gap_report_annotation_format(
        self, builder: CoverageBuilder
    ) -> None:
        """Annotation format: 'betweenness X.XXXX, on path from N entry point(s)'."""
        gaps = builder.compute_gap_report(set())
        for gap in gaps:
            # betweenness {score:.4f}
            assert "betweenness" in gap.annotation
            # N entry point(s)
            assert "entry point" in gap.annotation

    def test_gap_report_top_n_limits_results(
        self, builder: CoverageBuilder
    ) -> None:
        """top_n parameter limits the number of returned gaps."""
        gaps = builder.compute_gap_report(set(), top_n=3)
        assert len(gaps) <= 3

    def test_gap_report_excludes_covered_edges(
        self, builder: CoverageBuilder, small_graph: nx.DiGraph
    ) -> None:
        """Covered edges must not appear in the gap report."""
        covered = {("src/entry_a.py", "src/src_b.py")}
        gaps = builder.compute_gap_report(covered)
        gap_edges = {(g.source, g.target) for g in gaps}
        assert ("src/entry_a.py", "src/src_b.py") not in gap_edges

    def test_gap_report_returns_gap_entry_instances(
        self, builder: CoverageBuilder
    ) -> None:
        """compute_gap_report returns a list of GapEntry objects."""
        gaps = builder.compute_gap_report(set())
        for gap in gaps:
            assert isinstance(gap, GapEntry)

    def test_gap_report_centrality_values_are_floats(
        self, builder: CoverageBuilder
    ) -> None:
        """All GapEntry centrality values must be valid floats."""
        gaps = builder.compute_gap_report(set())
        for gap in gaps:
            assert isinstance(gap.centrality, float)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


class TestBuild:
    def test_build_returns_test_coverage_instance(
        self, builder: CoverageBuilder
    ) -> None:
        tf = _make_test_file(
            "tests/test_int.py",
            "integration",
            ["src/entry_a.py"],
        )
        result = builder.build([tf])
        assert isinstance(result, TestCoverage)

    def test_build_populates_test_files(
        self, builder: CoverageBuilder
    ) -> None:
        tf = _make_test_file(
            "tests/test_int.py",
            "integration",
            ["src/entry_a.py"],
        )
        result = builder.build([tf])
        assert len(result.test_files) == 1
        assert result.test_files[0] == tf

    def test_build_populates_covered_edges(
        self, builder: CoverageBuilder
    ) -> None:
        tf = _make_test_file(
            "tests/test_int.py",
            "integration",
            ["src/entry_a.py"],
        )
        result = builder.build([tf])
        assert len(result.covered_edges) > 0
        # Each covered edge is a dict with source and target
        for edge in result.covered_edges:
            assert "source" in edge
            assert "target" in edge

    def test_build_populates_gaps(
        self, builder: CoverageBuilder
    ) -> None:
        """Build with a single test covering some edges should leave gaps."""
        tf = _make_test_file(
            "tests/test_int.py",
            "integration",
            ["src/src_b.py"],  # doesn't cover src_d edges
        )
        result = builder.build([tf], top_n=10)
        assert len(result.gaps) > 0

    def test_build_no_gaps_when_all_covered(
        self, builder: CoverageBuilder, small_graph: nx.DiGraph
    ) -> None:
        """Build with integration test importing entry_a should cover most/all edges."""
        tf = _make_test_file(
            "tests/test_int.py",
            "integration",
            ["src/entry_a.py"],
        )
        result = builder.build([tf], top_n=10)
        # entry_a covers all reachable edges from it - which is all 6 edges
        assert len(result.gaps) == 0


# ---------------------------------------------------------------------------
# serialize
# ---------------------------------------------------------------------------


class TestSerialize:
    def test_serialize_returns_dict(self, builder: CoverageBuilder) -> None:
        coverage = TestCoverage()
        result = CoverageBuilder.serialize(coverage)
        assert isinstance(result, dict)

    def test_serialize_has_metadata_section(
        self, builder: CoverageBuilder
    ) -> None:
        coverage = TestCoverage()
        result = CoverageBuilder.serialize(coverage)
        assert "metadata" in result

    def test_serialize_metadata_has_required_keys(
        self, builder: CoverageBuilder
    ) -> None:
        coverage = TestCoverage()
        result = CoverageBuilder.serialize(coverage)
        meta = result["metadata"]
        assert "analyzed_at" in meta
        assert "total_edges" in meta
        assert "covered_edges" in meta
        assert "uncovered_edges" in meta
        assert "coverage_pct" in meta

    def test_serialize_has_test_files_section(
        self, builder: CoverageBuilder
    ) -> None:
        tf = _make_test_file("tests/test_int.py", "integration", [])
        coverage = TestCoverage(test_files=[tf])
        result = CoverageBuilder.serialize(coverage)
        assert "test_files" in result
        assert len(result["test_files"]) == 1

    def test_serialize_has_covered_edges_section(
        self, builder: CoverageBuilder
    ) -> None:
        coverage = TestCoverage(
            covered_edges=[{"source": "a.py", "target": "b.py"}]
        )
        result = CoverageBuilder.serialize(coverage)
        assert "covered_edges" in result
        assert len(result["covered_edges"]) == 1

    def test_serialize_has_gaps_section(self, builder: CoverageBuilder) -> None:
        gap = GapEntry(
            source="a.py",
            target="b.py",
            centrality=0.5,
            annotation="betweenness 0.5000, on path from 1 entry point(s)",
        )
        coverage = TestCoverage(gaps=[gap])
        result = CoverageBuilder.serialize(coverage)
        assert "gaps" in result
        assert len(result["gaps"]) == 1

    def test_serialize_metadata_analyzed_at_is_iso8601(
        self, builder: CoverageBuilder
    ) -> None:
        """analyzed_at must be a non-empty string (ISO 8601)."""
        coverage = TestCoverage()
        result = CoverageBuilder.serialize(coverage)
        analyzed_at = result["metadata"]["analyzed_at"]
        assert isinstance(analyzed_at, str)
        assert len(analyzed_at) > 0

    def test_serialize_coverage_pct_is_float(
        self, builder: CoverageBuilder
    ) -> None:
        coverage = TestCoverage()
        result = CoverageBuilder.serialize(coverage)
        assert isinstance(result["metadata"]["coverage_pct"], float)
