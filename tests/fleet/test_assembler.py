"""Tests for DocumentAssembler — validates and writes DirDoc from AgentResult.

TDD RED phase: tests written before implementation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lattice.fleet.models import AgentResult
from lattice.shadow.schema import DirDoc, GapSummary, StaticAnalysisLimits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dir_doc(directory: str = "src/auth") -> DirDoc:
    """Create a minimal valid DirDoc with source='agent'."""
    return DirDoc(
        directory=directory,
        confidence=0.85,
        source="agent",
        confidence_factors=["full contents reviewed"],
        last_analyzed=datetime.now(timezone.utc),
        summary="Auth module handles JWT validation.",
        responsibilities=["JWT validation", "token refresh"],
        developer_hints=[],
        child_refs=[],
        static_analysis_limits=StaticAnalysisLimits(),
        gap_summary=GapSummary(),
    )


def _make_agent_result(
    directory: str = "src/auth",
    *,
    failed: bool = False,
    error: str | None = None,
    dir_doc: DirDoc | None = None,
) -> AgentResult:
    """Create an AgentResult for testing."""
    return AgentResult(
        directory=directory,
        failed=failed,
        error=error,
        dir_doc=dir_doc or (_make_dir_doc(directory) if not failed else None),
        test_stubs=[],
        input_tokens=100,
        output_tokens=50,
    )


# ---------------------------------------------------------------------------
# DocumentAssembler tests
# ---------------------------------------------------------------------------


def test_assemble_successful_result_writes_dir_md(tmp_path: Path) -> None:
    """Successful AgentResult with valid DirDoc writes _dir.md and returns path."""
    from lattice.fleet.assembler import DocumentAssembler

    assembler = DocumentAssembler()
    result = _make_agent_result("src/auth")

    written_path = assembler.assemble(result, tmp_path)

    assert written_path is not None
    assert written_path.exists()
    assert written_path.name == "_dir.md"
    assert written_path.parent == tmp_path / "src" / "auth"


def test_assemble_failed_result_returns_none(tmp_path: Path) -> None:
    """Failed AgentResult returns None without writing any file."""
    from lattice.fleet.assembler import DocumentAssembler

    assembler = DocumentAssembler()
    result = _make_agent_result("src/auth", failed=True, error="LLM timeout")

    written_path = assembler.assemble(result, tmp_path)

    assert written_path is None
    # No _dir.md should be written
    assert not (tmp_path / "src" / "auth" / "_dir.md").exists()


def test_assemble_none_dir_doc_returns_none(tmp_path: Path) -> None:
    """AgentResult with None dir_doc returns None without writing."""
    from lattice.fleet.assembler import DocumentAssembler

    assembler = DocumentAssembler()
    result = AgentResult(
        directory="src/models",
        failed=False,
        error=None,
        dir_doc=None,  # No doc despite not failed
        test_stubs=[],
        input_tokens=80,
        output_tokens=30,
    )

    written_path = assembler.assemble(result, tmp_path)

    assert written_path is None


def test_assemble_wave_returns_correct_written_and_failed_counts(tmp_path: Path) -> None:
    """assemble_wave returns (written_count, failed_count) correctly."""
    from lattice.fleet.assembler import DocumentAssembler

    assembler = DocumentAssembler()

    results = [
        _make_agent_result("src/auth"),        # success
        _make_agent_result("src/utils"),       # success
        _make_agent_result("src/models", failed=True, error="timeout"),  # failed
        AgentResult(                           # no dir_doc
            directory="src/views",
            failed=False,
            error=None,
            dir_doc=None,
            test_stubs=[],
            input_tokens=50,
            output_tokens=20,
        ),
    ]

    written, failed = assembler.assemble_wave(results, tmp_path)

    assert written == 2  # src/auth and src/utils
    assert failed == 2   # src/models (failed=True) and src/views (None dir_doc)


def test_assemble_wave_all_success(tmp_path: Path) -> None:
    """assemble_wave with all successful results returns (n, 0)."""
    from lattice.fleet.assembler import DocumentAssembler

    assembler = DocumentAssembler()
    results = [
        _make_agent_result("src/auth"),
        _make_agent_result("src/utils"),
        _make_agent_result("src/models"),
    ]

    written, failed = assembler.assemble_wave(results, tmp_path)

    assert written == 3
    assert failed == 0


# ---------------------------------------------------------------------------
# Task 2 (08-02): Hint re-injection and confidence threshold tests
# ---------------------------------------------------------------------------


def test_hint_reinjection(tmp_path: Path) -> None:
    """After assembly, developer hints from _hints.json are re-injected into _dir.md."""
    import json
    from lattice.fleet.assembler import DocumentAssembler
    from lattice.shadow.reader import parse_dir_doc

    # Create _hints.json with a hint for src/auth
    hints = {
        "src/auth": [
            {"type": "hint", "text": "handles OAuth", "stored_at": "2024-01-01T00:00:00Z"}
        ]
    }
    (tmp_path / "_hints.json").write_text(json.dumps(hints))

    assembler = DocumentAssembler()
    result = _make_agent_result("src/auth")
    written_path = assembler.assemble(result, tmp_path)

    assert written_path is not None
    doc = parse_dir_doc(written_path)
    assert "handles OAuth" in doc.developer_hints


def test_hints_survive_rerun(tmp_path: Path) -> None:
    """Developer hints are re-injected after a second assemble (map:doc re-run)."""
    import json
    from lattice.fleet.assembler import DocumentAssembler
    from lattice.shadow.reader import parse_dir_doc

    hints = {
        "src/auth": [
            {"type": "hint", "text": "session manager", "stored_at": "2024-01-01T00:00:00Z"}
        ]
    }
    (tmp_path / "_hints.json").write_text(json.dumps(hints))

    assembler = DocumentAssembler()

    # First run
    result1 = _make_agent_result("src/auth")
    assembler.assemble(result1, tmp_path)

    # Second run (simulates map:doc re-run producing a new DirDoc with empty developer_hints)
    result2 = _make_agent_result("src/auth")
    written_path = assembler.assemble(result2, tmp_path)

    assert written_path is not None
    doc = parse_dir_doc(written_path)
    assert "session manager" in doc.developer_hints


def test_reinject_skips_idk_skip_entries(tmp_path: Path) -> None:
    """Re-injection only includes hint/expand entries; idk and skip types are excluded."""
    import json
    from lattice.fleet.assembler import DocumentAssembler
    from lattice.shadow.reader import parse_dir_doc

    hints = {
        "src/auth": [
            {"type": "idk", "stored_at": "2024-01-01T00:00:00Z"},
            {"type": "skip", "stored_at": "2024-01-01T00:00:00Z"},
            {"type": "correct", "stored_at": "2024-01-01T00:00:00Z"},
            {"type": "hint", "text": "valid hint", "stored_at": "2024-01-01T00:00:00Z"},
        ]
    }
    (tmp_path / "_hints.json").write_text(json.dumps(hints))

    assembler = DocumentAssembler()
    result = _make_agent_result("src/auth")
    written_path = assembler.assemble(result, tmp_path)

    assert written_path is not None
    doc = parse_dir_doc(written_path)
    assert "valid hint" in doc.developer_hints
    assert len(doc.developer_hints) == 1  # Only the hint type, not idk/skip/correct


def test_reinject_noop_no_hints_file(tmp_path: Path) -> None:
    """Re-injection is a no-op when _hints.json does not exist — no error raised."""
    from lattice.fleet.assembler import DocumentAssembler
    from lattice.shadow.reader import parse_dir_doc

    assembler = DocumentAssembler()
    result = _make_agent_result("src/auth")
    written_path = assembler.assemble(result, tmp_path)

    # No error, file written normally
    assert written_path is not None
    doc = parse_dir_doc(written_path)
    assert doc.developer_hints == []


def test_confidence_threshold_normal(tmp_path: Path) -> None:
    """AgentResult with confidence=0.4 is skipped with default threshold (0.5)."""
    from lattice.fleet.assembler import DocumentAssembler

    assembler = DocumentAssembler()
    # Create dir doc with low confidence
    low_conf_doc = _make_dir_doc("src/auth").model_copy(update={"confidence": 0.4})
    result = AgentResult(
        directory="src/auth",
        failed=False,
        error=None,
        dir_doc=low_conf_doc,
        test_stubs=[],
        input_tokens=100,
        output_tokens=50,
    )

    written_path = assembler.assemble(result, tmp_path)

    assert written_path is None
    assert not (tmp_path / "src" / "auth" / "_dir.md").exists()


def test_confidence_threshold_idk(tmp_path: Path) -> None:
    """AgentResult with confidence=0.4 is written when threshold=0.3 (IDK mode)."""
    from lattice.fleet.assembler import DocumentAssembler

    assembler = DocumentAssembler()
    low_conf_doc = _make_dir_doc("src/auth").model_copy(update={"confidence": 0.4})
    result = AgentResult(
        directory="src/auth",
        failed=False,
        error=None,
        dir_doc=low_conf_doc,
        test_stubs=[],
        input_tokens=100,
        output_tokens=50,
    )

    written_path = assembler.assemble(result, tmp_path, confidence_threshold=0.3)

    assert written_path is not None
    assert written_path.exists()


def test_assemble_wave_idk_directories(tmp_path: Path) -> None:
    """assemble_wave uses 0.3 threshold for directories in idk_directories set."""
    from lattice.fleet.assembler import DocumentAssembler

    assembler = DocumentAssembler()

    # src/auth is IDK (0.3 threshold) — confidence 0.4 should be written
    low_conf_doc = _make_dir_doc("src/auth").model_copy(update={"confidence": 0.4})
    idk_result = AgentResult(
        directory="src/auth",
        failed=False,
        error=None,
        dir_doc=low_conf_doc,
        test_stubs=[],
        input_tokens=100,
        output_tokens=50,
    )

    # src/utils is not IDK (0.5 threshold) — confidence 0.4 should be skipped
    non_idk_low_doc = _make_dir_doc("src/utils").model_copy(update={"confidence": 0.4})
    non_idk_result = AgentResult(
        directory="src/utils",
        failed=False,
        error=None,
        dir_doc=non_idk_low_doc,
        test_stubs=[],
        input_tokens=100,
        output_tokens=50,
    )

    results = [idk_result, non_idk_result]
    written, failed = assembler.assemble_wave(
        results, tmp_path, idk_directories={"src/auth"}
    )

    assert written == 1   # src/auth written (IDK threshold 0.3)
    assert failed == 1    # src/utils skipped (normal threshold 0.5)
