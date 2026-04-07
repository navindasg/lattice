"""SoulWriter: atomic writes to STATE.md and MEMORY.md.

Uses tmp-file-and-replace pattern (via write_soul_atomically from soul.py)
for crash-safe writes. Never modifies SOUL.md or AGENTS.md (human-owned).

A threading lock serialises concurrent writes to prevent races where
one thread's atomic rename removes the file another thread is reading.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import structlog

from lattice.orchestrator.soul import write_soul_atomically
from lattice.orchestrator.soul_ecosystem.models import (
    OrchestratorState,
    SoulMemoryEntry,
)
from lattice.orchestrator.soul_ecosystem.templates import (
    AGENTS_TEMPLATE,
    MEMORY_TEMPLATE,
    SOUL_TEMPLATE,
    STATE_TEMPLATE,
)

logger = structlog.get_logger(__name__)


class SoulWriter:
    """Atomic writes to STATE.md and MEMORY.md.

    Uses tmp-file-and-replace pattern for crash-safe writes.
    Never modifies SOUL.md or AGENTS.md (human-owned).
    """

    def __init__(self, soul_dir: Path) -> None:
        self._soul_dir = soul_dir
        self._lock = threading.Lock()

    @property
    def soul_dir(self) -> Path:
        """Return the soul directory path."""
        return self._soul_dir

    def update_state(self, section: str, content: str) -> None:
        """Atomically update a named section in STATE.md.

        Replaces content between ## {section} and the next ## header (or EOF).
        Other sections are preserved unchanged.

        Args:
            section: Section name (e.g., "Instances", "Plan", "Decisions", "Blockers").
            content: New content for the section (without the ## header).
        """
        with self._lock:
            state_path = self._soul_dir / "STATE.md"
            if state_path.exists():
                current = state_path.read_text(encoding="utf-8")
            else:
                current = STATE_TEMPLATE

            pattern = re.compile(
                r"(## " + re.escape(section) + r"\n)(.*?)(?=\n## |\Z)",
                re.DOTALL,
            )

            def _replacer(match: re.Match) -> str:
                return match.group(1) + content

            updated = pattern.sub(_replacer, current)

            logger.debug(
                "soul_ecosystem.update_state",
                section=section,
                soul_dir=str(self._soul_dir),
            )
            self._soul_dir.mkdir(parents=True, exist_ok=True)
            write_soul_atomically(state_path, updated)

    def update_full_state(self, state: OrchestratorState) -> None:
        """Atomically replace entire STATE.md with rendered state.

        Args:
            state: OrchestratorState to render and write.
        """
        with self._lock:
            state_path = self._soul_dir / "STATE.md"
            content = state.to_markdown()

            logger.debug(
                "soul_ecosystem.update_full_state",
                soul_dir=str(self._soul_dir),
            )
            self._soul_dir.mkdir(parents=True, exist_ok=True)
            write_soul_atomically(state_path, content)

    def append_memory(self, entry: SoulMemoryEntry) -> None:
        """Append a timestamped, categorized entry to MEMORY.md.

        Format: - [ISO_TIMESTAMP] [CATEGORY] content
        Never overwrites existing entries.

        Args:
            entry: Memory entry to append.
        """
        with self._lock:
            memory_path = self._soul_dir / "MEMORY.md"
            if memory_path.exists():
                current = memory_path.read_text(encoding="utf-8")
            else:
                current = MEMORY_TEMPLATE

            line = f"- [{entry.timestamp}] [{entry.category}] {entry.content}"
            # Ensure trailing newline before appending
            if current and not current.endswith("\n"):
                current += "\n"
            updated = current + line + "\n"

            logger.debug(
                "soul_ecosystem.append_memory",
                category=entry.category,
                soul_dir=str(self._soul_dir),
            )
            self._soul_dir.mkdir(parents=True, exist_ok=True)
            write_soul_atomically(memory_path, updated)

    def init_soul_dir(self) -> None:
        """Create soul directory and populate with default templates.

        Only creates files that don't already exist (preserves human edits).
        This is called by ``lattice orchestrator:init``.
        """
        self._soul_dir.mkdir(parents=True, exist_ok=True)

        templates = {
            "SOUL.md": SOUL_TEMPLATE,
            "AGENTS.md": AGENTS_TEMPLATE,
            "STATE.md": STATE_TEMPLATE,
            "MEMORY.md": MEMORY_TEMPLATE,
        }

        for filename, template in templates.items():
            path = self._soul_dir / filename
            if not path.exists():
                logger.info(
                    "soul_ecosystem.init_file",
                    file=filename,
                    soul_dir=str(self._soul_dir),
                )
                write_soul_atomically(path, template)
            else:
                logger.debug(
                    "soul_ecosystem.init_file_exists",
                    file=filename,
                    soul_dir=str(self._soul_dir),
                )
