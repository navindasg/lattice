"""ContextManager: per-instance context utilization tracking with DuckDB persistence.

Tracks bytes sent and received per CC instance, computes utilization percentage
using a 4-chars/token heuristic against a configurable token window, and persists
snapshots to a DuckDB context_utilization table.

Also provides the compaction pipeline (compact()) which orchestrates:
  summarize -> update soul file -> /clear -> reinject -> verify via echo-back probe.

Usage:
    cm = ContextManager(conn, config, souls_dir)
    pct = cm.track_bytes_sent("inst-abc", len(message))
    if cm.needs_compaction("inst-abc"):
        result = await cm.compact("inst-abc", proc_stdin, proc_stdout)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import duckdb
import structlog
from pydantic import BaseModel

from lattice.orchestrator.models import ContextManagerConfig
from lattice.orchestrator.protocol import (
    create_request_envelope,
    read_message,
    write_message,
)
from lattice.orchestrator.soul import (
    SoulFile,
    _extract_key_terms,
    _parse_memory_bullets,
    _progressively_compress,
    write_soul_atomically,
)

_CHARS_PER_TOKEN = 4


class CompactionResult(BaseModel):
    """Result of a compaction pipeline run for a CC instance.

    Fields:
        instance_id: The CC instance that was compacted.
        status: PASS — verified; FAIL — verification failed; SKIPPED — timed out before
                compaction; ERROR — unexpected parse failure.
        compaction_count: The new compaction_count after this run (-1 if SKIPPED).
        detail: Human-readable detail for FAIL/SKIPPED/ERROR statuses.
    """

    instance_id: str
    status: Literal["PASS", "FAIL", "SKIPPED", "ERROR"]
    compaction_count: int
    detail: str = ""

    model_config = {"frozen": True}


_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS context_utilization (
        instance_id TEXT PRIMARY KEY,
        bytes_sent INTEGER DEFAULT 0,
        bytes_received INTEGER DEFAULT 0,
        utilization_pct REAL DEFAULT 0.0,
        compaction_count INTEGER DEFAULT 0,
        last_updated TEXT NOT NULL
    )
"""


class ContextManager:
    """Tracks per-instance context utilization and persists to DuckDB.

    Uses a 4-chars/token heuristic to estimate token count from byte counts.
    Compaction is triggered when utilization_pct >= config.compaction_threshold.

    Args:
        conn: An open duckdb.DuckDBPyConnection instance.
        config: ContextManagerConfig with threshold and window settings.
        souls_dir: Path to the souls directory (for future soul file I/O).
    """

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        config: ContextManagerConfig,
        souls_dir: Path,
    ) -> None:
        self._conn = conn
        self._config = config
        self._souls_dir = souls_dir
        self._bytes_sent: dict[str, int] = {}
        self._bytes_received: dict[str, int] = {}
        self._create_tables()

    def _create_tables(self) -> None:
        """Create context_utilization table idempotently."""
        self._conn.execute(_CREATE_TABLE_SQL)

    def track_bytes_sent(self, instance_id: str, n_bytes: int) -> float:
        """Increment bytes sent for an instance and return updated utilization_pct.

        Args:
            instance_id: The CC instance identifier.
            n_bytes: Number of bytes sent in this message.

        Returns:
            Updated utilization percentage (0.0 to 100.0+).
        """
        current = self._bytes_sent.get(instance_id, 0)
        self._bytes_sent[instance_id] = current + n_bytes
        return self._recalculate(instance_id)

    def track_bytes_received(self, instance_id: str, n_bytes: int) -> float:
        """Increment bytes received for an instance and return updated utilization_pct.

        Args:
            instance_id: The CC instance identifier.
            n_bytes: Number of bytes received in this message.

        Returns:
            Updated utilization percentage (0.0 to 100.0+).
        """
        current = self._bytes_received.get(instance_id, 0)
        self._bytes_received[instance_id] = current + n_bytes
        return self._recalculate(instance_id)

    def _recalculate(self, instance_id: str) -> float:
        """Recalculate utilization for an instance and persist the snapshot.

        Args:
            instance_id: The CC instance identifier.

        Returns:
            Utilization percentage based on total bytes / chars_per_token / window_tokens.
        """
        sent = self._bytes_sent.get(instance_id, 0)
        received = self._bytes_received.get(instance_id, 0)
        total_bytes = sent + received
        estimated_tokens = total_bytes / _CHARS_PER_TOKEN
        pct = (estimated_tokens / self._config.window_tokens) * 100.0
        self._persist_snapshot(instance_id, sent, received, pct)
        return pct

    def _persist_snapshot(
        self,
        instance_id: str,
        bytes_sent: int,
        bytes_received: int,
        pct: float,
    ) -> None:
        """Insert or replace utilization snapshot in DuckDB, preserving compaction_count.

        Uses a subquery to preserve the existing compaction_count on update.

        Args:
            instance_id: The CC instance identifier.
            bytes_sent: Current cumulative bytes sent.
            bytes_received: Current cumulative bytes received.
            pct: Computed utilization percentage.
        """
        now = datetime.now(timezone.utc).isoformat()
        # Preserve existing compaction_count via COALESCE subquery
        existing_count = self._conn.execute(
            "SELECT compaction_count FROM context_utilization WHERE instance_id = ?",
            [instance_id],
        ).fetchone()
        compaction_count = existing_count[0] if existing_count else 0

        self._conn.execute(
            "INSERT OR REPLACE INTO context_utilization "
            "(instance_id, bytes_sent, bytes_received, utilization_pct, "
            "compaction_count, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [instance_id, bytes_sent, bytes_received, pct, compaction_count, now],
        )

    def get_utilization(self, instance_id: str) -> float:
        """Return the persisted utilization_pct for an instance.

        Args:
            instance_id: The CC instance identifier.

        Returns:
            Utilization percentage if tracked, 0.0 if unknown.
        """
        row = self._conn.execute(
            "SELECT utilization_pct FROM context_utilization WHERE instance_id = ?",
            [instance_id],
        ).fetchone()
        return float(row[0]) if row else 0.0

    def needs_compaction(self, instance_id: str) -> bool:
        """Return True when utilization is at or above the compaction threshold.

        Args:
            instance_id: The CC instance identifier.

        Returns:
            True if get_utilization(instance_id) >= config.compaction_threshold.
        """
        return self.get_utilization(instance_id) >= self._config.compaction_threshold

    def reset_counters(self, instance_id: str, seed_bytes: int = 0) -> None:
        """Reset byte counters for an instance, optionally re-seeding with seed_bytes.

        Called after compaction to reset the context window tracking.
        seed_bytes represents the size of the compacted soul summary that was
        injected at the start of the new context window.

        Args:
            instance_id: The CC instance identifier.
            seed_bytes: Bytes to pre-seed bytes_sent after reset (default 0).
        """
        self._bytes_sent[instance_id] = seed_bytes
        self._bytes_received[instance_id] = 0
        self._recalculate(instance_id)

    async def compact(
        self,
        instance_id: str,
        proc_stdin: asyncio.StreamWriter,
        proc_stdout: asyncio.StreamReader,
    ) -> CompactionResult:
        """Orchestrate the full compaction pipeline for a CC instance.

        Steps:
          1. Send summarization prompt to CC via write_message.
          2. Await summary response (30s timeout — returns SKIPPED on timeout).
          3. Parse summary bullets into MemoryEntry objects.
          4. Load soul file from disk (returns ERROR on parse failure).
          5. Progressively compress memory entries.
          6. Write updated soul file atomically.
          7. Send /clear message.
          8. Reinject soul markdown.
          9. Reset byte counters re-seeded with soul markdown size.
          10. Update compaction_count in DuckDB.
          11. If verification_enabled, call _verify_reinject and return its result.

        Args:
            instance_id: The CC instance identifier.
            proc_stdin: asyncio.StreamWriter for the CC process stdin.
            proc_stdout: asyncio.StreamReader for the CC process stdout.

        Returns:
            CompactionResult with status PASS/FAIL/SKIPPED/ERROR and updated compaction_count.
        """
        log = structlog.get_logger(__name__).bind(instance_id=instance_id)

        # Step 1: Send summarization request
        summarize_request = create_request_envelope({
            "type": "prompt",
            "content": (
                "Summarize your current conversation state as a concise bullet list. "
                "Format each bullet as: [HH:MM] <one-line summary>. "
                "Include: tasks completed, task in progress, any blockers."
            ),
        })
        await write_message(proc_stdin, summarize_request)
        log.info("compaction_summarize_sent")

        # Step 2: Await summary response with timeout
        try:
            response = await asyncio.wait_for(read_message(proc_stdout), timeout=30.0)
        except asyncio.TimeoutError:
            log.warning("compaction_summarize_timeout", instance_id=instance_id)
            return CompactionResult(
                instance_id=instance_id,
                status="SKIPPED",
                compaction_count=-1,
                detail="Summarization timeout",
            )

        # Step 3: Parse summary bullets
        response_text = ""
        if response:
            response_text = response.get("result", "") or response.get("data", {}).get("content", "")
        new_entries = _parse_memory_bullets(response_text)
        log.debug("compaction_bullets_parsed", count=len(new_entries))

        # Step 4: Load soul file
        soul_path = self._souls_dir / f"instance-{instance_id}.md"
        try:
            soul_text = soul_path.read_text(encoding="utf-8")
            current_soul = SoulFile.from_markdown(instance_id, soul_text)
        except (ValueError, FileNotFoundError) as exc:
            log.error("compaction_soul_parse_error", error=str(exc))
            return CompactionResult(
                instance_id=instance_id,
                status="ERROR",
                compaction_count=-1,
                detail=f"Soul file parse error: {exc}",
            )

        # Step 5: Progressively compress memory
        compressed_memory = _progressively_compress(current_soul.memory, new_entries)

        # Step 6: Build updated soul (immutable model_copy)
        updated_soul = current_soul.model_copy(
            update={
                "memory": compressed_memory,
                "compaction_count": current_soul.compaction_count + 1,
            }
        )

        # Step 7: Write updated soul file atomically
        write_soul_atomically(soul_path, updated_soul.to_markdown())
        log.info("compaction_soul_written", compaction_count=updated_soul.compaction_count)

        # Step 8: Send /clear
        clear_request = create_request_envelope({"type": "clear"})
        await write_message(proc_stdin, clear_request)
        log.debug("compaction_clear_sent")

        # Step 9: Reinject soul markdown
        soul_md = updated_soul.to_markdown()
        reinject_request = create_request_envelope({"type": "prompt", "content": soul_md})
        await write_message(proc_stdin, reinject_request)
        log.debug("compaction_reinject_sent")

        # Step 10: Reset byte counters re-seeded with soul markdown size
        self.reset_counters(instance_id, seed_bytes=len(soul_md.encode()))
        log.debug("compaction_counters_reset", seed_bytes=len(soul_md.encode()))

        # Step 11: Update compaction_count in DuckDB
        self._conn.execute(
            "UPDATE context_utilization SET compaction_count = ? WHERE instance_id = ?",
            [updated_soul.compaction_count, instance_id],
        )
        log.info(
            "compaction_duckdb_updated",
            compaction_count=updated_soul.compaction_count,
        )

        # Step 12: Verify reinject if enabled
        if self._config.verification_enabled:
            return await self._verify_reinject(
                instance_id, updated_soul, proc_stdin, proc_stdout
            )

        return CompactionResult(
            instance_id=instance_id,
            status="PASS",
            compaction_count=updated_soul.compaction_count,
        )

    async def _verify_reinject(
        self,
        instance_id: str,
        soul: SoulFile,
        stdin: asyncio.StreamWriter,
        stdout: asyncio.StreamReader,
    ) -> CompactionResult:
        """Verify the CC instance retained task awareness after context reset.

        Sends an echo-back probe and checks whether the response contains
        key terms extracted from the soul file. Retries once on failure by
        re-injecting the soul markdown before re-probing.

        Pass threshold: >= 50% of key terms (min 1) present in response.

        Args:
            instance_id: The CC instance identifier.
            soul: The updated SoulFile to extract key terms from.
            stdin: asyncio.StreamWriter for the CC process stdin.
            stdout: asyncio.StreamReader for the CC process stdout.

        Returns:
            CompactionResult with status PASS or FAIL and correlation fields.
        """
        log = structlog.get_logger(__name__).bind(
            instance_id=instance_id,
            compaction_count=soul.compaction_count,
        )
        terms = _extract_key_terms(soul)
        pass_threshold = max(1, len(terms) // 2)

        for attempt in range(2):
            # Send probe
            probe = create_request_envelope({
                "type": "prompt",
                "content": "What is your current task and what have you completed?",
            })
            await write_message(stdin, probe)

            # Await probe response with timeout
            try:
                response = await asyncio.wait_for(read_message(stdout), timeout=15.0)
            except asyncio.TimeoutError:
                log.warning("reinject_verification_timeout", attempt=attempt)
                return CompactionResult(
                    instance_id=instance_id,
                    status="FAIL",
                    compaction_count=soul.compaction_count,
                    detail="Verification probe timeout",
                )

            response_text = ""
            if response:
                response_text = (
                    response.get("result", "")
                    or response.get("data", {}).get("content", "")
                ).lower()

            matched = sum(1 for term in terms if term.lower() in response_text)
            passed = matched >= pass_threshold

            log.info(
                "reinject_verification",
                status="PASS" if passed else "FAIL",
                matched_terms=matched,
                total_terms=len(terms),
                attempt=attempt,
            )

            if passed:
                return CompactionResult(
                    instance_id=instance_id,
                    status="PASS",
                    compaction_count=soul.compaction_count,
                )

            # First failure: retry by re-injecting soul markdown
            if attempt == 0:
                log.warning("reinject_verification_retry", instance_id=instance_id)
                retry_inject = create_request_envelope({
                    "type": "prompt",
                    "content": soul.to_markdown(),
                })
                await write_message(stdin, retry_inject)

        # Both attempts failed
        return CompactionResult(
            instance_id=instance_id,
            status="FAIL",
            compaction_count=soul.compaction_count,
            detail="Reinject verification failed after retry",
        )
