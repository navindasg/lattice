"""DirDoc Pydantic schema for _dir.md shadow tree files.

DirDoc captures the structured metadata written to each _dir.md file in the
shadow tree (.agent-docs/ directory). The model is frozen and uses
model_copy(update={...}) for safe updates.

Key design decisions:
- ConfidenceSource Literal constrains the source field to three known values
- confidence_factors must be non-empty when source='agent' (model_validator)
- last_analyzed uses timezone-aware UTC (datetime.now(timezone.utc) pattern)
- All nested models (GapSummary, StaticAnalysisLimits) are also frozen
"""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ConfidenceSource = Literal["agent", "developer", "static"]


class GapSummary(BaseModel):
    """Summary of coverage gaps detected in a directory.

    untested_edges: number of edges not covered by integration/e2e tests
    top_gaps: list of gap entry dicts (serialized GapEntry instances)
    """

    untested_edges: int = 0
    top_gaps: list[dict] = Field(default_factory=list)

    model_config = {"frozen": True}


class StaticAnalysisLimits(BaseModel):
    """Flags for static analysis limitations affecting confidence scoring.

    dynamic_imports: count of dynamic import patterns that couldn't be resolved
    unresolved_paths: count of imports that could not be path-resolved
    """

    dynamic_imports: int = 0
    unresolved_paths: int = 0

    model_config = {"frozen": True}


class DirDoc(BaseModel):
    """Structured metadata for a _dir.md shadow tree file.

    Required fields: directory, confidence, source, last_analyzed.
    Optional fields all have safe defaults.

    Validation rules:
    - confidence must be in [0.0, 1.0]
    - confidence_factors must be non-empty when source='agent'

    Use model_copy(update={...}) to derive modified instances from frozen model.
    """

    directory: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: ConfidenceSource
    confidence_factors: list[str] = Field(default_factory=list)
    stale: bool = False
    last_analyzed: datetime
    static_analysis_limits: StaticAnalysisLimits = Field(
        default_factory=StaticAnalysisLimits
    )
    gap_summary: GapSummary = Field(default_factory=GapSummary)
    summary: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    developer_hints: list[str] = Field(default_factory=list)
    child_refs: list[str] = Field(default_factory=list)
    cross_cutting_refs: list[str] = Field(default_factory=list)
    integration_points: list[dict] = Field(default_factory=list)

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def require_confidence_factors_for_agent(self) -> "DirDoc":
        """Enforce that agent-sourced docs explain their confidence score."""
        if self.source == "agent" and not self.confidence_factors:
            raise ValueError(
                "confidence_factors must be non-empty when source='agent'. "
                "Provide at least one factor explaining the confidence score."
            )
        return self
