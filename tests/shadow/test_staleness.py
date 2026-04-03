"""Tests for git-based staleness detection utilities."""
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lattice.shadow.schema import DirDoc
from lattice.shadow.staleness import is_stale, last_git_commit_time


def make_dir_doc(last_analyzed: datetime, source: str = "static") -> DirDoc:
    return DirDoc(
        directory="src/lattice",
        confidence=0.8,
        source=source,  # type: ignore[arg-type]
        confidence_factors=[],
        last_analyzed=last_analyzed,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subdir = tmp_path / "src"
    subdir.mkdir()
    file = subdir / "module.py"
    file.write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    return tmp_path


class TestLastGitCommitTime:
    def test_returns_none_for_untracked_directory(self, tmp_path: Path):
        """Directory with no git history returns None."""
        # tmp_path is not a git repo, so no history
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
        result = last_git_commit_time(tmp_path / "no_commits_here", tmp_path)
        assert result is None

    def test_returns_timezone_aware_utc_datetime(self, git_repo: Path):
        """Returns a timezone-aware UTC datetime for a committed directory."""
        result = last_git_commit_time(git_repo / "src", git_repo)
        assert result is not None
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc

    def test_returns_datetime_for_committed_directory(self, git_repo: Path):
        """Returns a datetime that corresponds to the commit time."""
        result = last_git_commit_time(git_repo / "src", git_repo)
        assert isinstance(result, datetime)
        # The commit was made recently, so it should be close to now
        now = datetime.now(timezone.utc)
        diff = abs((now - result).total_seconds())
        assert diff < 60  # Within 1 minute


class TestIsStale:
    def test_returns_true_when_commit_after_last_analyzed(self, git_repo: Path):
        """Stale when git commit is newer than last_analyzed."""
        commit_time = last_git_commit_time(git_repo / "src", git_repo)
        assert commit_time is not None
        # last_analyzed is before the commit
        old_time = commit_time - timedelta(seconds=10)
        doc = DirDoc(
            directory="src",
            confidence=0.8,
            source="static",
            confidence_factors=[],
            last_analyzed=old_time,
        )
        assert is_stale(doc, git_repo) is True

    def test_returns_false_when_commit_before_last_analyzed(self, git_repo: Path):
        """Not stale when last_analyzed is after the git commit."""
        commit_time = last_git_commit_time(git_repo / "src", git_repo)
        assert commit_time is not None
        # last_analyzed is after the commit
        future_time = commit_time + timedelta(seconds=10)
        doc = DirDoc(
            directory="src",
            confidence=0.8,
            source="static",
            confidence_factors=[],
            last_analyzed=future_time,
        )
        assert is_stale(doc, git_repo) is False

    def test_returns_false_when_no_git_history(self, tmp_path: Path):
        """Not stale (returns False) when directory has no git history."""
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
        doc = DirDoc(
            directory="no_commits_here",
            confidence=0.8,
            source="static",
            confidence_factors=[],
            last_analyzed=datetime.now(timezone.utc),
        )
        assert is_stale(doc, tmp_path) is False
