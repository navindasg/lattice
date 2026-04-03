"""Document assembler — validates AgentResult and writes DirDoc to shadow tree.

DocumentAssembler is stateless. Each call to assemble() is independent.

The assembler enforces:
- Failed AgentResults are skipped (not written silently)
- AgentResults with no dir_doc are skipped
- ValidationError from Pydantic rejects malformed docs (not written silently)
- DirDocs below the confidence_threshold are skipped
- After each write, developer hints from _hints.json are re-injected
"""
from __future__ import annotations

import json
from pathlib import Path

import structlog
from pydantic import ValidationError

from lattice.fleet.models import AgentResult
from lattice.shadow.reader import parse_dir_doc
from lattice.shadow.writer import write_dir_doc

log = structlog.get_logger(__name__)

# Default confidence threshold — DirDocs below this are not written
_DEFAULT_CONFIDENCE_THRESHOLD = 0.5

# IDK confidence threshold — lower bar for hard-to-document directories
_IDK_CONFIDENCE_THRESHOLD = 0.3


class DocumentAssembler:
    """Stateless assembler that validates and writes DirDoc from AgentResult.

    Usage::

        assembler = DocumentAssembler()
        path = assembler.assemble(agent_result, agent_docs_root)
        written, failed = assembler.assemble_wave(results, agent_docs_root)
    """

    def _reinject_hints(
        self,
        directory: str,
        agent_docs_root: Path,
        written_path: Path,
    ) -> None:
        """Re-inject developer hints from _hints.json into a freshly written _dir.md.

        Only includes entries with type='hint' or type='expand' that have a 'text' field.
        Entries of type='idk', 'skip', or 'correct' are excluded.

        This ensures developer hints survive map:doc re-runs (the agent LLM won't produce
        developer_hints, so they must be injected from _hints.json after each write).

        Args:
            directory: Relative directory string key in _hints.json.
            agent_docs_root: Root of the .agent-docs shadow tree.
            written_path: Path to the freshly written _dir.md file.
        """
        hints_path = agent_docs_root / "_hints.json"
        if not hints_path.exists():
            return

        try:
            hints_data = json.loads(hints_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("hints_reinject_read_failed", directory=directory, error=str(exc))
            return

        dir_hints = hints_data.get(directory, [])

        hint_texts = [
            e["text"]
            for e in dir_hints
            if e.get("type", "hint") in ("hint", "expand") and e.get("text")
        ]

        if not hint_texts:
            return

        try:
            doc = parse_dir_doc(written_path)
            updated = doc.model_copy(update={"developer_hints": hint_texts})
            write_dir_doc(updated, agent_docs_root)
            log.info(
                "hints_reinjected",
                directory=directory,
                hint_count=len(hint_texts),
            )
        except Exception as exc:
            log.warning(
                "hints_reinject_failed",
                directory=directory,
                error=str(exc),
            )

    def assemble(
        self,
        agent_result: AgentResult,
        agent_docs_root: Path,
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> Path | None:
        """Validate and write a single DirDoc from an AgentResult.

        Returns the path to the written _dir.md, or None if the result was
        skipped (failed, missing doc, below confidence threshold, or invalid).

        After a successful write, re-injects developer hints from _hints.json.

        Args:
            agent_result: The outcome from a FleetDispatcher wave.
            agent_docs_root: Root of the .agent-docs shadow tree.
            confidence_threshold: Minimum confidence to write. DirDocs below this
                                  value are silently skipped.

        Returns:
            Path to written _dir.md, or None if skipped/rejected.
        """
        directory = agent_result.directory

        if agent_result.failed:
            log.warning(
                "assembler_skip_failed",
                directory=directory,
                error=agent_result.error,
            )
            return None

        if agent_result.dir_doc is None:
            log.warning(
                "assembler_skip_no_doc",
                directory=directory,
            )
            return None

        if agent_result.dir_doc.confidence < confidence_threshold:
            log.info(
                "assembler_skip_low_confidence",
                directory=directory,
                confidence=agent_result.dir_doc.confidence,
                threshold=confidence_threshold,
            )
            return None

        try:
            written_path = write_dir_doc(agent_result.dir_doc, agent_docs_root)
            log.info(
                "assembler_wrote_dir_doc",
                directory=directory,
                path=str(written_path),
            )
            # Re-inject hints after successful write
            self._reinject_hints(directory, agent_docs_root, written_path)
            return written_path
        except ValidationError as exc:
            log.error(
                "assembler_validation_error",
                directory=directory,
                error=str(exc),
            )
            return None

    def assemble_wave(
        self,
        results: list[AgentResult],
        agent_docs_root: Path,
        idk_directories: set[str] | None = None,
    ) -> tuple[int, int]:
        """Process all results from a wave, writing valid DirDocs.

        Args:
            results: List of AgentResult from dispatch_wave().
            agent_docs_root: Root of the .agent-docs shadow tree.
            idk_directories: Optional set of directory strings that should use
                             the IDK confidence threshold (0.3) instead of the
                             default (0.5).

        Returns:
            Tuple of (written_count, failed_count) where failed_count includes
            results where assemble() returned None.
        """
        written_count = 0
        failed_count = 0

        for result in results:
            threshold = (
                _IDK_CONFIDENCE_THRESHOLD
                if (idk_directories and result.directory in idk_directories)
                else _DEFAULT_CONFIDENCE_THRESHOLD
            )
            path = self.assemble(result, agent_docs_root, confidence_threshold=threshold)
            if path is not None:
                written_count += 1
            else:
                failed_count += 1

        log.info(
            "assembler_wave_complete",
            written=written_count,
            failed=failed_count,
        )
        return written_count, failed_count
