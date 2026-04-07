"""SoulReader: reads soul ecosystem files and builds structured context.

Provides read access to all four soul files (SOUL.md, AGENTS.md, STATE.md,
MEMORY.md) and can assemble them into an LLM system prompt.
"""
from __future__ import annotations

import re
from pathlib import Path

import structlog

from lattice.orchestrator.soul_ecosystem.models import (
    OrchestratorState,
    SoulContext,
    SoulMemoryEntry,
)
from lattice.orchestrator.soul_ecosystem.templates import (
    AGENTS_TEMPLATE,
    MEMORY_TEMPLATE,
    SOUL_TEMPLATE,
    STATE_TEMPLATE,
)

logger = structlog.get_logger(__name__)

_MEMORY_ENTRY_PATTERN = re.compile(
    r"^- \[(.+?)\] \[(\w+)\] (.+)$", re.MULTILINE
)


class SoulReader:
    """Reads all soul ecosystem files and builds structured context."""

    def __init__(self, soul_dir: Path) -> None:
        self._soul_dir = soul_dir

    @property
    def soul_dir(self) -> Path:
        """Return the soul directory path."""
        return self._soul_dir

    def read_all(self) -> SoulContext:
        """Read all four soul files, returning defaults for missing files.

        Returns:
            SoulContext with content from each file, or default template content
            if the file does not exist.
        """
        return SoulContext(
            soul=self._read_file("SOUL.md", SOUL_TEMPLATE),
            agents=self._read_file("AGENTS.md", AGENTS_TEMPLATE),
            state=self._read_file("STATE.md", STATE_TEMPLATE),
            memory=self._read_file("MEMORY.md", MEMORY_TEMPLATE),
        )

    def build_system_prompt(self) -> str:
        """Assemble SOUL.md + AGENTS.md + STATE.md into LLM system prompt.

        MEMORY.md is excluded from the system prompt (too long for context).
        Use read_memory_entries() or query_memory() for selective retrieval.

        Returns:
            Formatted string with clear section headers.
        """
        ctx = self.read_all()
        return (
            f"=== IDENTITY ===\n{ctx.soul}\n\n"
            f"=== PROCEDURES ===\n{ctx.agents}\n\n"
            f"=== CURRENT STATE ===\n{ctx.state}"
        )

    def read_state(self) -> OrchestratorState:
        """Parse STATE.md into structured OrchestratorState.

        Returns:
            OrchestratorState parsed from STATE.md content.
            Returns empty state if the file doesn't exist.
        """
        content = self._read_file("STATE.md", STATE_TEMPLATE)
        return OrchestratorState.from_markdown(content)

    def read_memory_entries(self) -> list[SoulMemoryEntry]:
        """Parse MEMORY.md into list of SoulMemoryEntry.

        Expected format per line: - [ISO_TIMESTAMP] [CATEGORY] content

        Returns:
            List of parsed memory entries. Empty list if no entries found.
        """
        content = self._read_file("MEMORY.md", MEMORY_TEMPLATE)
        return _parse_memory_entries(content)

    def query_memory(
        self, category: str | None = None
    ) -> list[SoulMemoryEntry]:
        """Query memory entries, optionally filtered by category.

        Args:
            category: If provided, only return entries matching this category.
                      If None, return all entries.

        Returns:
            Filtered list of memory entries.
        """
        entries = self.read_memory_entries()
        if category is None:
            return entries
        return [e for e in entries if e.category == category]

    def _read_file(self, filename: str, default: str) -> str:
        """Read a file from the soul directory, returning default if missing.

        Args:
            filename: Name of the file to read.
            default: Default content to return if the file doesn't exist.

        Returns:
            File content as string, or default if file is missing.
        """
        path = self._soul_dir / filename
        if not path.exists():
            logger.debug(
                "soul_ecosystem.file_missing",
                file=filename,
                soul_dir=str(self._soul_dir),
            )
            return default
        return path.read_text(encoding="utf-8")


def _parse_memory_entries(content: str) -> list[SoulMemoryEntry]:
    """Parse memory entries from MEMORY.md content.

    Expected format: - [ISO_TIMESTAMP] [CATEGORY] content

    Args:
        content: Full content of MEMORY.md.

    Returns:
        List of parsed SoulMemoryEntry objects.
    """
    entries: list[SoulMemoryEntry] = []
    for match in _MEMORY_ENTRY_PATTERN.finditer(content):
        entries.append(
            SoulMemoryEntry(
                timestamp=match.group(1),
                category=match.group(2),
                content=match.group(3),
            )
        )
    return entries
