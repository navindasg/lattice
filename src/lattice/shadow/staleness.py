"""Git-based staleness detection for shadow tree _dir.md files.

Compares the last git commit timestamp touching a directory against the
last_analyzed field in DirDoc to determine if a directory has been modified
since the shadow doc was written.
"""
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lattice.shadow.schema import DirDoc


def last_git_commit_time(directory: Path, project_root: Path) -> datetime | None:
    """Return the UTC datetime of the most recent git commit touching directory.

    Runs git log to find the last commit that modified any file under directory.
    Returns None if the directory has no git history (never committed).

    Args:
        directory: Path to the directory to check (absolute or relative).
        project_root: Root of the git repository (used as cwd for git commands).

    Returns:
        Timezone-aware UTC datetime of the last commit, or None if no history.
    """
    result = subprocess.run(
        ["git", "log", "-1", "--format=%at", "--", f"{directory}/*"],
        capture_output=True,
        text=True,
        cwd=str(project_root),
    )
    stdout = result.stdout.strip()
    if not stdout:
        return None
    return datetime.fromtimestamp(int(stdout), tz=timezone.utc)


def is_stale(doc: DirDoc, project_root: Path) -> bool:
    """Return True if the directory has git commits newer than doc.last_analyzed.

    Uses last_git_commit_time to get the most recent commit for doc.directory.
    Returns False when the directory has no git history (can't determine staleness).

    Args:
        doc: DirDoc whose directory and last_analyzed are used for comparison.
        project_root: Root of the git repository.

    Returns:
        True if a more recent commit exists than last_analyzed, False otherwise.
    """
    last_commit = last_git_commit_time(Path(doc.directory), project_root)
    if last_commit is None:
        return False
    return last_commit > doc.last_analyzed
