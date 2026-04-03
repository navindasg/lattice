"""Tests for SkeletonWriter — writes test stubs to _test_stubs/ shadow path.

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

_VALID_PYTHON_STUB = """\
import pytest


def test_auth_models_integration():
    \"\"\"Auth -> models integration seam.\"\"\"
    raise NotImplementedError("stub — implement me")
"""

_INVALID_PYTHON_STUB = """\
def broken_syntax(
    # missing closing paren and colon
"""

_VALID_TYPESCRIPT_STUB = """\
describe('auth -> models integration', () => {
  it('should connect auth to models', async () => {
    throw new Error('stub — implement me');
  });
});
"""


def _make_agent_result_with_stubs(
    directory: str,
    stubs: list[dict],
) -> AgentResult:
    """Create an AgentResult with test_stubs."""
    dir_doc = DirDoc(
        directory=directory,
        confidence=0.8,
        source="agent",
        confidence_factors=["reviewed"],
        last_analyzed=datetime.now(timezone.utc),
    )
    return AgentResult(
        directory=directory,
        failed=False,
        error=None,
        dir_doc=dir_doc,
        test_stubs=stubs,
        input_tokens=100,
        output_tokens=50,
    )


# ---------------------------------------------------------------------------
# SkeletonWriter tests
# ---------------------------------------------------------------------------


def test_python_stub_with_valid_syntax_written_to_test_stubs_path(
    tmp_path: Path,
) -> None:
    """Python stub with valid syntax is written to correct _test_stubs/ path."""
    from lattice.fleet.skeleton import SkeletonWriter

    writer = SkeletonWriter()
    stub = {
        "path": "test_auth_models_integration.py",
        "content": _VALID_PYTHON_STUB,
        "language": "python",
    }
    result = _make_agent_result_with_stubs("src/auth", [stub])

    written_paths = writer.write_stubs(result, tmp_path)

    assert len(written_paths) == 1
    stub_path = written_paths[0]
    assert stub_path.exists()
    assert stub_path.name == "test_auth_models_integration.py"
    # Must be inside _test_stubs/
    assert stub_path.parent.name == "_test_stubs"
    assert stub_path.parent.parent == tmp_path / "src" / "auth"
    assert stub_path.read_text(encoding="utf-8") == _VALID_PYTHON_STUB


def test_typescript_stub_written_to_correct_path(tmp_path: Path) -> None:
    """TypeScript stub is written to correct _test_stubs/ path."""
    from lattice.fleet.skeleton import SkeletonWriter

    writer = SkeletonWriter()
    stub = {
        "path": "auth.models.integration.test.ts",
        "content": _VALID_TYPESCRIPT_STUB,
        "language": "typescript",
    }
    result = _make_agent_result_with_stubs("src/auth", [stub])

    written_paths = writer.write_stubs(result, tmp_path)

    assert len(written_paths) == 1
    stub_path = written_paths[0]
    assert stub_path.exists()
    assert stub_path.name == "auth.models.integration.test.ts"
    assert stub_path.parent.name == "_test_stubs"


def test_invalid_python_syntax_stub_is_skipped(tmp_path: Path) -> None:
    """Invalid Python syntax stub is not written — skipped with log."""
    from lattice.fleet.skeleton import SkeletonWriter

    writer = SkeletonWriter()
    stub = {
        "path": "test_broken.py",
        "content": _INVALID_PYTHON_STUB,
        "language": "python",
    }
    result = _make_agent_result_with_stubs("src/utils", [stub])

    written_paths = writer.write_stubs(result, tmp_path)

    assert len(written_paths) == 0
    assert not (tmp_path / "src" / "utils" / "_test_stubs" / "test_broken.py").exists()


def test_test_stubs_directory_created_if_not_exists(tmp_path: Path) -> None:
    """_test_stubs/ directory is created even if it doesn't already exist."""
    from lattice.fleet.skeleton import SkeletonWriter

    writer = SkeletonWriter()
    stub = {
        "path": "test_new_dir_integration.py",
        "content": _VALID_PYTHON_STUB,
        "language": "python",
    }
    result = _make_agent_result_with_stubs("src/brand/new/dir", [stub])

    # Verify the _test_stubs dir doesn't exist yet
    stubs_dir = tmp_path / "src" / "brand" / "new" / "dir" / "_test_stubs"
    assert not stubs_dir.exists()

    written_paths = writer.write_stubs(result, tmp_path)

    assert stubs_dir.exists()
    assert len(written_paths) == 1


def test_empty_test_stubs_returns_empty_list(tmp_path: Path) -> None:
    """AgentResult with empty test_stubs list returns empty path list."""
    from lattice.fleet.skeleton import SkeletonWriter

    writer = SkeletonWriter()
    result = _make_agent_result_with_stubs("src/auth", [])

    written_paths = writer.write_stubs(result, tmp_path)

    assert written_paths == []


def test_multiple_stubs_all_written(tmp_path: Path) -> None:
    """Multiple stubs in one AgentResult all written correctly."""
    from lattice.fleet.skeleton import SkeletonWriter

    writer = SkeletonWriter()
    stubs = [
        {
            "path": "test_auth_models_integration.py",
            "content": _VALID_PYTHON_STUB,
            "language": "python",
        },
        {
            "path": "test_auth_db_integration.py",
            "content": _VALID_PYTHON_STUB,
            "language": "python",
        },
    ]
    result = _make_agent_result_with_stubs("src/auth", stubs)

    written_paths = writer.write_stubs(result, tmp_path)

    assert len(written_paths) == 2
    stubs_dir = tmp_path / "src" / "auth" / "_test_stubs"
    assert (stubs_dir / "test_auth_models_integration.py").exists()
    assert (stubs_dir / "test_auth_db_integration.py").exists()
