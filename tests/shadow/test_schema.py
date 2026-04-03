"""Tests for DirDoc schema, GapSummary, StaticAnalysisLimits, ConfidenceSource."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from lattice.shadow.schema import (
    ConfidenceSource,
    DirDoc,
    GapSummary,
    StaticAnalysisLimits,
)

_NOW = datetime.now(timezone.utc)


def make_dir_doc(**kwargs) -> DirDoc:
    defaults = {
        "directory": "src/lattice",
        "confidence": 0.8,
        "source": "agent",
        "confidence_factors": ["imports resolved", "all functions documented"],
        "last_analyzed": _NOW,
    }
    defaults.update(kwargs)
    return DirDoc(**defaults)


class TestDirDocConstruction:
    def test_constructs_with_required_fields(self):
        doc = make_dir_doc()
        assert doc.directory == "src/lattice"
        assert doc.confidence == 0.8
        assert doc.source == "agent"
        assert doc.last_analyzed == _NOW

    def test_defaults_are_set(self):
        doc = make_dir_doc()
        assert doc.stale is False
        assert doc.summary == ""
        assert doc.responsibilities == []
        assert doc.developer_hints == []
        assert doc.child_refs == []

    def test_gap_summary_defaults(self):
        gs = GapSummary()
        assert gs.untested_edges == 0
        assert gs.top_gaps == []

    def test_static_analysis_limits_defaults(self):
        sal = StaticAnalysisLimits()
        assert sal.dynamic_imports == 0
        assert sal.unresolved_paths == 0


class TestDirDocValidation:
    def test_rejects_confidence_below_zero(self):
        with pytest.raises(ValidationError):
            make_dir_doc(confidence=-0.1)

    def test_rejects_confidence_above_one(self):
        with pytest.raises(ValidationError):
            make_dir_doc(confidence=1.5)

    def test_accepts_confidence_zero(self):
        doc = make_dir_doc(confidence=0.0, source="static")
        assert doc.confidence == 0.0

    def test_accepts_confidence_one(self):
        doc = make_dir_doc(confidence=1.0)
        assert doc.confidence == 1.0

    def test_rejects_empty_confidence_factors_for_agent(self):
        with pytest.raises(ValidationError, match="confidence_factors"):
            make_dir_doc(source="agent", confidence_factors=[])

    def test_allows_empty_confidence_factors_for_developer(self):
        doc = make_dir_doc(source="developer", confidence_factors=[])
        assert doc.confidence_factors == []

    def test_allows_empty_confidence_factors_for_static(self):
        doc = make_dir_doc(source="static", confidence_factors=[])
        assert doc.confidence_factors == []


class TestDirDocImmutability:
    def test_is_frozen(self):
        doc = make_dir_doc()
        with pytest.raises(Exception):
            doc.confidence = 0.5  # type: ignore[misc]

    def test_model_copy_produces_new_instance(self):
        doc = make_dir_doc()
        new_doc = doc.model_copy(update={"confidence": 0.5})
        assert new_doc.confidence == 0.5
        assert doc.confidence == 0.8
        assert new_doc is not doc
