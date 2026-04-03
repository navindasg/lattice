"""Coverage models — frozen Pydantic models for test coverage data (TC-01).

Provides TestFile, GapEntry, and TestCoverage models used by the test coverage
mapping subsystem (Phase 3).
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


class TestCoverage(BaseModel):
    """Complete test coverage report for a codebase.

    Attributes:
        test_files: All discovered and classified test files.
        covered_edges: Dependency graph edges exercised by integration/e2e tests.
            Each entry is a dict with keys: source, target, import_type.
        gaps: Untested dependency edges ranked by centrality (highest first).
    """

    test_files: list[TestFile] = Field(default_factory=list)
    covered_edges: list[dict] = Field(default_factory=list)
    gaps: list[GapEntry] = Field(default_factory=list)
    model_config = {"frozen": True}
