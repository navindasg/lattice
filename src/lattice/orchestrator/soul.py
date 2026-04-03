"""SoulFile: persistent identity and memory model for CC instances.

SoulFile serializes to a five-section Markdown format and round-trips via from_markdown().
All models are frozen Pydantic models (immutable after construction).

Sections:
  ## Identity             — free-text paragraph describing the instance's role
  ## Project Context      — pointer lines to .agent-docs/ files
  ## Current State        — structured lists of completed/in_progress/blocked_on
  ## Preferences          — free-text behavioral preferences
  ## Conversation Memory (Compacted) — timestamped memory entries from prior sessions

write_soul_atomically() writes content via temp file + Path.replace() for POSIX atomicity.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    """A single timestamped memory entry from a prior compacted session.

    Renders as: - [timestamp] content
    """

    timestamp: str
    content: str

    model_config = {"frozen": True}


class CurrentState(BaseModel):
    """Structured tracking of work status for an instance.

    Renders each list item with a named prefix:
      - Completed: {item}
      - In progress: {item}
      - Blocked on: {item}
    """

    completed: list[str] = Field(default_factory=list)
    in_progress: list[str] = Field(default_factory=list)
    blocked_on: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}

    def to_markdown(self) -> str:
        """Render state lists as prefixed bullet points."""
        lines: list[str] = []
        for item in self.completed:
            lines.append(f"- Completed: {item}")
        for item in self.in_progress:
            lines.append(f"- In progress: {item}")
        for item in self.blocked_on:
            lines.append(f"- Blocked on: {item}")
        return "\n".join(lines)

    @classmethod
    def from_markdown(cls, text: str) -> CurrentState:
        """Parse CurrentState from rendered markdown lines."""
        completed: list[str] = []
        in_progress: list[str] = []
        blocked_on: list[str] = []

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- Completed: "):
                completed.append(stripped[len("- Completed: "):])
            elif stripped.startswith("- In progress: "):
                in_progress.append(stripped[len("- In progress: "):])
            elif stripped.startswith("- Blocked on: "):
                blocked_on.append(stripped[len("- Blocked on: "):])

        return cls(completed=completed, in_progress=in_progress, blocked_on=blocked_on)


_REQUIRED_SECTIONS = frozenset({
    "Identity",
    "Project Context",
    "Current State",
    "Preferences",
    "Conversation Memory (Compacted)",
})

_MEMORY_PATTERN = re.compile(r"^- \[(.+?)\] (.+)$")


class SoulFile(BaseModel):
    """Persistent identity and memory model for a CC instance.

    Serializes to five-section Markdown via to_markdown() and restores via from_markdown().
    All fields are immutable after construction (frozen model).

    Fields:
        instance_id: Unique instance identifier.
        identity: Free-text paragraph describing the instance's role and purpose.
        project_context: Pointer lines to .agent-docs/ or other reference files.
        current_state: Structured work status (completed/in_progress/blocked_on).
        preferences: Free-text behavioral preferences for this instance.
        memory: List of timestamped memory entries from compacted prior sessions.
        compaction_count: Number of times this soul has been compacted.
    """

    _SECTION_PATTERN: ClassVar[re.Pattern] = re.compile(r"^## (.+)$", re.MULTILINE)

    instance_id: str
    identity: str
    project_context: str
    current_state: CurrentState = Field(default_factory=CurrentState)
    preferences: str
    memory: list[MemoryEntry] = Field(default_factory=list)
    compaction_count: int = 0
    project_id: str | None = None

    model_config = {"frozen": True}

    def to_markdown(self) -> str:
        """Serialize soul to five-section Markdown string.

        If project_id is set, prepends an HTML comment before the Identity section
        so that from_markdown() can recover it while remaining backward compatible
        with older soul files that have no such comment.

        Returns:
            Markdown string with five ## sections separated by double newlines.
            If project_id is set, the string begins with a project_id comment.
        """
        memory_lines = "\n".join(
            f"- [{entry.timestamp}] {entry.content}" for entry in self.memory
        )

        sections = [
            f"## Identity\n{self.identity}",
            f"## Project Context\n{self.project_context}",
            f"## Current State\n{self.current_state.to_markdown()}",
            f"## Preferences\n{self.preferences}",
            f"## Conversation Memory (Compacted)\n{memory_lines}",
        ]
        body = "\n\n".join(sections)
        if self.project_id is not None:
            return f"<!-- project_id: {self.project_id} -->\n\n{body}"
        return body

    @classmethod
    def from_markdown(cls, instance_id: str, text: str) -> SoulFile:
        """Restore a SoulFile from its Markdown representation.

        Supports backward compatibility: if the text begins with an HTML comment
        ``<!-- project_id: ... -->``, the project_id is extracted. Older soul files
        without this comment restore with project_id=None.

        Args:
            instance_id: The instance ID to assign to the restored soul.
            text: Markdown string produced by to_markdown().

        Returns:
            Restored SoulFile instance.

        Raises:
            ValueError: If any of the five required sections is missing.
        """
        # Extract optional project_id from HTML comment before first ## header
        project_id: str | None = None
        _PROJECT_ID_PATTERN = re.compile(r"<!--\s*project_id:\s*(.+?)\s*-->")
        pid_match = _PROJECT_ID_PATTERN.search(text)
        if pid_match:
            project_id = pid_match.group(1).strip()

        # Split on ## headers; result: [pre, header1, body1, header2, body2, ...]
        parts = cls._SECTION_PATTERN.split(text)
        # parts[0] is any text before first header (usually empty or project_id comment)
        # parts[1::2] are header names, parts[2::2] are bodies
        section_map: dict[str, str] = {}
        headers = parts[1::2]
        bodies = parts[2::2]
        for header, body in zip(headers, bodies):
            section_map[header.strip()] = body.strip()

        missing = _REQUIRED_SECTIONS - set(section_map.keys())
        if missing:
            raise ValueError(f"Soul file missing sections: {missing}")

        # Parse current state
        current_state = CurrentState.from_markdown(section_map["Current State"])

        # Parse memory entries
        memory: list[MemoryEntry] = []
        for line in section_map["Conversation Memory (Compacted)"].splitlines():
            m = _MEMORY_PATTERN.match(line.strip())
            if m:
                memory.append(MemoryEntry(timestamp=m.group(1), content=m.group(2)))

        return cls(
            instance_id=instance_id,
            identity=section_map["Identity"],
            project_context=section_map["Project Context"],
            current_state=current_state,
            preferences=section_map["Preferences"],
            memory=memory,
            project_id=project_id,
        )


def _parse_memory_bullets(text: str) -> list[MemoryEntry]:
    """Parse memory bullet lines in "[HH:MM] content" format into MemoryEntry objects.

    Each valid line must start with "- [" or "[" followed by a timestamp in brackets.
    Lines that don't match the pattern are silently skipped.

    Args:
        text: Multi-line string of memory bullet points.

    Returns:
        List of MemoryEntry objects. Empty list for empty or fully malformed input.
    """
    entries: list[MemoryEntry] = []
    for line in text.splitlines():
        stripped = line.strip()
        # Strip leading "- " if present
        if stripped.startswith("- "):
            stripped = stripped[2:]
        # Must start with "[" to have a timestamp bracket
        if not stripped.startswith("["):
            continue
        close = stripped.find("]")
        if close == -1:
            continue
        timestamp = stripped[1:close]
        content = stripped[close + 1:].strip()
        if timestamp and content:
            entries.append(MemoryEntry(timestamp=timestamp, content=content))
    return entries


def _progressively_compress(
    existing: list[MemoryEntry],
    new_entries: list[MemoryEntry],
    keep_recent: int = 10,
) -> list[MemoryEntry]:
    """Compress older memory entries while keeping recent entries detailed.

    If len(existing) <= keep_recent, all existing entries are preserved as-is.
    Otherwise, entries beyond the most recent keep_recent are merged into a
    single compressed summary entry.

    Args:
        existing: Current list of memory entries in the soul file.
        new_entries: New entries from the latest summarization.
        keep_recent: Number of most-recent existing entries to keep verbatim.

    Returns:
        New list: [compressed_entry (if any)] + recent + new_entries.
    """
    if len(existing) <= keep_recent:
        return list(existing) + list(new_entries)

    old = existing[:-keep_recent]
    recent = existing[-keep_recent:]

    compressed = MemoryEntry(
        timestamp=old[0].timestamp,
        content=(
            f"Compacted: {len(old)} earlier entries covering "
            f"{old[0].content}...{old[-1].content}"
        ),
    )
    return [compressed] + list(recent) + list(new_entries)


def _extract_key_terms(soul: "SoulFile") -> list[str]:
    """Extract key verification terms from a SoulFile for echo-back probing.

    Collects:
    - Words from identity that are capitalized or contain dots/slashes (module names, paths)
    - All items in current_state.in_progress and current_state.completed
    - Content from the last 3 memory entries

    Args:
        soul: SoulFile instance to extract terms from.

    Returns:
        Deduplicated list of lowercased terms. At least 1 term is always returned.
    """
    terms: list[str] = []

    # Words from identity that look like module names or paths
    for word in soul.identity.split():
        cleaned = word.strip(".,;:!?()[]\"'")
        if cleaned and (cleaned[0].isupper() or "." in cleaned or "/" in cleaned):
            terms.append(cleaned.lower())

    # All in_progress and completed items
    for item in soul.current_state.in_progress:
        terms.append(item.lower().strip())
    for item in soul.current_state.completed:
        terms.append(item.lower().strip())

    # Content from last 3 memory entries
    for entry in soul.memory[-3:]:
        terms.append(entry.content.lower().strip())

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            deduped.append(term)

    # Ensure minimum 1 term
    if not deduped:
        deduped.append(soul.instance_id.lower())

    return deduped


def write_soul_atomically(path: Path, content: str) -> None:
    """Write content to path using temp file + rename for POSIX atomicity.

    Writes to path.with_suffix(".tmp") then calls tmp.replace(path).
    On POSIX systems, os.replace() (used by Path.replace()) is atomic.
    No partial file is left at path if the process crashes mid-write.

    Args:
        path: Target path for the soul file (e.g. .lattice/souls/instance-{id}.md).
        content: String content to write (UTF-8 encoded).
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
