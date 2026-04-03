"""Git hook integration for automatic incremental documentation.

Manages the post-commit hook that triggers map:queue in the background
after each commit. Uses sentinel-delimited sections so Lattice can
coexist with other hook scripts.

Architecture: "queue-only, never dispatch from hook" — the hook writes
to _queue.json and map:doc --incremental consumes it at a safe time.

Exports:
    _hook_install_impl   — install the Lattice post-commit hook section
    _hook_uninstall_impl — remove the Lattice post-commit hook section
"""
from __future__ import annotations

import stat
from pathlib import Path

_SENTINEL_BEGIN = "# LATTICE-HOOK-BEGIN"
_SENTINEL_END = "# LATTICE-HOOK-END"

_HOOK_SECTION = """\
# LATTICE-HOOK-BEGIN
LATTICE_COMMIT=$(git rev-parse HEAD)
LATTICE_FILES=$(git diff-tree --no-commit-id --name-only -r HEAD | tr '\\n' ' ')
lattice map:queue . --commit "$LATTICE_COMMIT" --files $LATTICE_FILES &>/dev/null &
# LATTICE-HOOK-END
"""


def _hook_install_impl(target: Path) -> dict:
    """Install the Lattice post-commit hook section.

    Creates or appends to .git/hooks/post-commit with sentinel-delimited
    Lattice section. Idempotent — calling twice does not duplicate section.

    Args:
        target: Path to the project root (must contain .git/).

    Returns:
        {"installed": True, "hook_path": str} on success,
        {"installed": True, "already_present": True} if already installed,
        {"installed": False, "reason": "no_git_directory"} if .git/ missing.
    """
    git_dir = target / ".git"
    if not git_dir.exists():
        return {"installed": False, "reason": "no_git_directory"}

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    # Read existing content if present
    existing_content: str = ""
    if hook_path.exists():
        existing_content = hook_path.read_text(encoding="utf-8")

    # Idempotency check — already installed
    if _SENTINEL_BEGIN in existing_content:
        return {"installed": True, "already_present": True}

    # Build new content
    if existing_content:
        # Append Lattice section after existing content
        new_content = existing_content.rstrip("\n") + "\n\n" + _HOOK_SECTION
    else:
        # New file — add shebang then Lattice section
        new_content = "#!/bin/sh\n" + _HOOK_SECTION

    hook_path.write_text(new_content, encoding="utf-8")

    # Set executable bits
    current_mode = hook_path.stat().st_mode
    hook_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return {"installed": True, "hook_path": str(hook_path)}


def _hook_uninstall_impl(target: Path) -> dict:
    """Remove the Lattice post-commit hook section.

    Removes lines from LATTICE-HOOK-BEGIN through LATTICE-HOOK-END
    (inclusive). Deletes the file entirely if remaining content is
    only a shebang line or empty.

    Args:
        target: Path to the project root.

    Returns:
        {"removed": True, "hook_path": str} on success,
        {"removed": False, "reason": "no_lattice_section"} if not installed.
    """
    hook_path = target / ".git" / "hooks" / "post-commit"

    if not hook_path.exists():
        return {"removed": False, "reason": "no_lattice_section"}

    content = hook_path.read_text(encoding="utf-8")

    if _SENTINEL_BEGIN not in content:
        return {"removed": False, "reason": "no_lattice_section"}

    # Remove lines between sentinels (inclusive)
    lines = content.splitlines()
    filtered: list[str] = []
    inside_section = False
    for line in lines:
        if line.strip() == _SENTINEL_BEGIN:
            inside_section = True
            continue
        if line.strip() == _SENTINEL_END:
            inside_section = False
            continue
        if not inside_section:
            filtered.append(line)

    # Strip trailing blank lines from remaining content
    remaining = "\n".join(filtered).rstrip()

    # Delete file if remaining content is only shebang or empty
    if not remaining or remaining.strip() in ("#!/bin/sh", "#!/bin/bash", ""):
        hook_path.unlink()
        return {"removed": True, "hook_path": str(hook_path)}

    hook_path.write_text(remaining + "\n", encoding="utf-8")
    return {"removed": True, "hook_path": str(hook_path)}
