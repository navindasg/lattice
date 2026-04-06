"""Coverage models — frozen Pydantic models for test coverage data (TC-01).

Provides TestFile, GapEntry, TestEdgeMapping, and TestCoverage models used by
the test coverage mapping subsystem (Phase 3).
"""
from typing import Literal

from pydantic import BaseModel, Field

TestType = Literal["unit", "integration", "e2e"]


class TestFile(BaseModel):
    """A discovered and classified test file.

    Attributes:
        path: Project-relative path to the test file.
        language: Source language of the test file.
        test_type: Classification of the test (unit, integration, e2e).
        reason: Human-readable explanation for the classification.
        source_modules: Project-relative paths of internal source modules
            imported by this test file.
    """

    path: str
    language: Literal["python", "javascript", "typescript"]
    test_type: TestType
    reason: str
    source_modules: list[str] = Field(default_factory=list)
    model_config = {"frozen": True}


class GapEntry(BaseModel):
    """An untested integration seam ranked by centrality.

    Attributes:
        source: Source node key (project-relative path).
        target: Target node key (project-relative path).
        centrality: Edge betweenness centrality score (0.0–1.0).
        annotation: Human-readable explanation of why this gap matters.
    """

    source: str
    target: str
    centrality: float
    annotation: str
    model_config = {"frozen": True}


class TestEdgeMapping(BaseModel):
    """Maps a single integration/e2e test to the dependency edges it exercises.

    Attributes:
        test_path: Project-relative path to the test file.
        covered_edges: Dependency edges transitively exercised by this test.
            Each entry is a dict with keys ``source`` and ``target``.
        covered_node_count: Number of unique source nodes reachable from
            this test's imports.
    """

    test_path: str
    covered_edges: list[dict[str, str]] = Field(default_factory=list)
    covered_node_count: int = 0
    model_config = {"frozen": True}


class TestCoverage(BaseModel):
    """Complete test coverage report for a codebase.

    Attributes:
        test_files: All discovered and classified test files.
        total_edge_count: Total number of edges in the dependency graph.
            Used for accurate coverage percentage calculation even when
            ``gaps`` is truncated by ``top_n``.
        covered_edges: Dependency graph edges exercised by integration/e2e tests.
            Each entry is a dict with keys ``source`` and ``target``.
        integration_graph: Per-test mapping showing which dependency edges
            each integration/e2e test exercises. Enables answering
            "which test covers edge X?" queries.
        gaps: Untested dependency edges ranked by centrality (highest first).
            May be truncated to ``top_n`` entries.
    """

    test_files: list[TestFile] = Field(default_factory=list)
    total_edge_count: int = 0
    covered_edges: list[dict[str, str]] = Field(default_factory=list)
    integration_graph: list[TestEdgeMapping] = Field(default_factory=list)
    gaps: list[GapEntry] = Field(default_factory=list)
    model_config = {"frozen": True}
