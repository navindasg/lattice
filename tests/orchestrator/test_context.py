"""Unit tests for ContextManager utilization tracking, DuckDB persistence,
compaction pipeline, and post-reinject verification.

Tests cover:
- context_utilization table is created on construction
- track_bytes_sent increments and returns utilization_pct
- track_bytes_received increments received total
- get_utilization returns persisted value; 0.0 for unknown instance
- utilization calculation: 256000 bytes / 4 chars/token / 128000 tokens = 50%
- needs_compaction returns False at 54%, True at 55%
- Utilization data survives ContextManager reconstruction with same connection
- reset_counters re-seeds bytes_sent, clears received, persists reset
- compact() orchestrates the full summarize-update-clear-reinject-verify flow
- Memory helpers: _parse_memory_bullets, _progressively_compress
- _verify_reinject echo-back probe with retry logic
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import duckdb
import pytest

from lattice.orchestrator.context import CompactionResult, ContextManager
from lattice.orchestrator.models import ContextManagerConfig
from lattice.orchestrator.soul import (
    CurrentState,
    MemoryEntry,
    SoulFile,
    _extract_key_terms,
    _parse_memory_bullets,
    _progressively_compress,
    write_soul_atomically,
)


class TestContextManagerTableCreation:
    """ContextManager creates context_utilization table on construction."""

    def test_creates_context_utilization_table(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        souls_dir = tmp_path / "souls"
        souls_dir.mkdir()

        ContextManager(conn, config, souls_dir)

        # Verify table exists by querying it
        result = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'context_utilization'"
        ).fetchall()
        column_names = {row[0] for row in result}
        assert "instance_id" in column_names
        assert "bytes_sent" in column_names
        assert "bytes_received" in column_names
        assert "utilization_pct" in column_names
        assert "compaction_count" in column_names
        assert "last_updated" in column_names


class TestTrackBytesSent:
    """track_bytes_sent increments and returns utilization_pct."""

    def test_returns_positive_pct_after_first_call(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        pct = cm.track_bytes_sent("inst-1", 1000)
        assert pct > 0.0

    def test_pct_increases_on_second_call(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        pct1 = cm.track_bytes_sent("inst-1", 1000)
        pct2 = cm.track_bytes_sent("inst-1", 1000)
        assert pct2 > pct1

    def test_pct_is_cumulative_not_per_call(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        # 128000 bytes * 2 = 256000 bytes = 64000 tokens = 50% of 128000 window
        cm.track_bytes_sent("inst-1", 128_000)
        pct = cm.track_bytes_sent("inst-1", 128_000)
        # Only sent bytes, no received: 256000 / 4 / 128000 = 50%
        assert abs(pct - 50.0) < 0.1


class TestTrackBytesReceived:
    """track_bytes_received increments received total and returns utilization_pct."""

    def test_returns_positive_pct_after_first_call(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        pct = cm.track_bytes_received("inst-1", 2000)
        assert pct > 0.0

    def test_received_adds_to_total(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        pct_sent = cm.track_bytes_sent("inst-1", 10_000)
        pct_both = cm.track_bytes_received("inst-1", 10_000)
        assert pct_both > pct_sent


class TestGetUtilization:
    """get_utilization returns persisted value; 0.0 for unknown instance."""

    def test_returns_0_for_unknown_instance(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        assert cm.get_utilization("unknown-inst") == 0.0

    def test_returns_same_value_as_last_track_call(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        pct = cm.track_bytes_sent("inst-1", 5_000)
        assert cm.get_utilization("inst-1") == pytest.approx(pct)


class TestUtilizationCalculation:
    """Utilization calculation: 4 chars/token heuristic against window_tokens."""

    def test_256000_bytes_yields_50_percent_of_128k_window(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        # 256000 bytes sent, 0 received
        # estimated_tokens = 256000 / 4 = 64000
        # pct = 64000 / 128000 * 100 = 50.0%
        pct = cm.track_bytes_sent("inst-1", 256_000)
        assert abs(pct - 50.0) < 0.01

    def test_512000_bytes_yields_100_percent(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        # 512000 bytes = 128000 tokens = 100%
        pct = cm.track_bytes_sent("inst-1", 512_000)
        assert abs(pct - 100.0) < 0.01

    def test_custom_window_tokens(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=200_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        # 200000 bytes / 4 = 50000 tokens / 200000 = 25%
        pct = cm.track_bytes_sent("inst-1", 200_000)
        assert abs(pct - 25.0) < 0.01


class TestNeedsCompaction:
    """needs_compaction returns True at/above threshold, False below."""

    def test_returns_false_at_54_percent(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        # 54% = 54 * 128000 * 4 / 100 = 276480 bytes
        cm.track_bytes_sent("inst-1", int(0.54 * 128_000 * 4))
        assert cm.needs_compaction("inst-1") is False

    def test_returns_true_at_55_percent(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        # 55% = 55 * 128000 * 4 / 100 = 281600 bytes
        cm.track_bytes_sent("inst-1", int(0.55 * 128_000 * 4))
        assert cm.needs_compaction("inst-1") is True

    def test_returns_true_at_60_percent(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        # 60% of 128000 tokens * 4 chars = 307200 bytes
        cm.track_bytes_sent("inst-1", int(0.60 * 128_000 * 4))
        assert cm.needs_compaction("inst-1") is True

    def test_returns_false_for_unknown_instance(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        # Unknown instance has 0.0 utilization, below any threshold
        assert cm.needs_compaction("never-tracked") is False


class TestDuckDBPersistence:
    """Utilization data survives ContextManager reconstruction with same connection."""

    def test_data_persists_after_reconstruction(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        souls_dir = tmp_path / "souls"

        cm1 = ContextManager(conn, config, souls_dir)
        cm1.track_bytes_sent("inst-persist", 100_000)
        pct_original = cm1.get_utilization("inst-persist")

        # Reconstruct ContextManager with same connection
        cm2 = ContextManager(conn, config, souls_dir)
        pct_after = cm2.get_utilization("inst-persist")

        assert abs(pct_after - pct_original) < 0.01

    def test_multiple_instances_tracked_independently(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        cm.track_bytes_sent("inst-a", 50_000)
        cm.track_bytes_sent("inst-b", 200_000)

        pct_a = cm.get_utilization("inst-a")
        pct_b = cm.get_utilization("inst-b")
        assert pct_b > pct_a


class TestResetCounters:
    """reset_counters re-seeds bytes_sent, clears received, persists reset."""

    def test_reset_clears_utilization(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        cm.track_bytes_sent("inst-1", 300_000)
        assert cm.get_utilization("inst-1") > 0.0

        cm.reset_counters("inst-1")
        assert cm.get_utilization("inst-1") == 0.0

    def test_reset_with_seed_bytes(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        cm = ContextManager(conn, config, tmp_path / "souls")

        cm.track_bytes_sent("inst-1", 500_000)
        high_pct = cm.get_utilization("inst-1")

        # After reset with 500 bytes seed, utilization should be very low
        cm.reset_counters("inst-1", seed_bytes=500)
        low_pct = cm.get_utilization("inst-1")
        assert low_pct < high_pct
        assert low_pct > 0.0  # seed bytes contribute to utilization

    def test_reset_persists_to_duckdb(self, tmp_path: Path):
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        souls_dir = tmp_path / "souls"

        cm1 = ContextManager(conn, config, souls_dir)
        cm1.track_bytes_sent("inst-1", 300_000)
        cm1.reset_counters("inst-1")

        # New ContextManager on same connection should see reset state
        cm2 = ContextManager(conn, config, souls_dir)
        assert cm2.get_utilization("inst-1") == 0.0


# ---------------------------------------------------------------------------
# Helper functions: _parse_memory_bullets, _progressively_compress
# ---------------------------------------------------------------------------


class TestMemoryHelpers:
    """Tests for soul.py helper functions used in the compaction pipeline."""

    def test_parse_memory_bullets_basic(self):
        text = "- [14:22] fixed auth\n- [14:30] ran tests"
        result = _parse_memory_bullets(text)
        assert len(result) == 2
        assert result[0] == MemoryEntry(timestamp="14:22", content="fixed auth")
        assert result[1] == MemoryEntry(timestamp="14:30", content="ran tests")

    def test_parse_memory_bullets_empty_string(self):
        assert _parse_memory_bullets("") == []

    def test_parse_memory_bullets_malformed_lines(self):
        text = "no brackets here\n- also no brackets"
        assert _parse_memory_bullets(text) == []

    def test_parse_memory_bullets_mixed_valid_invalid(self):
        text = "- [10:00] valid entry\nnot a bullet\n- [11:00] another valid"
        result = _parse_memory_bullets(text)
        assert len(result) == 2
        assert result[0].timestamp == "10:00"
        assert result[1].timestamp == "11:00"

    def test_progressively_compress_few_existing_entries(self):
        """When existing <= keep_recent (10), all entries preserved unchanged."""
        existing = [
            MemoryEntry(timestamp=f"10:{i:02d}", content=f"entry {i}")
            for i in range(5)
        ]
        new_entries = [MemoryEntry(timestamp="11:00", content="new entry")]
        result = _progressively_compress(existing, new_entries, keep_recent=10)
        assert len(result) == 6  # all 5 existing + 1 new
        assert result[-1].content == "new entry"

    def test_progressively_compress_many_existing_entries(self):
        """When existing > keep_recent, oldest are compressed into one entry."""
        existing = [
            MemoryEntry(timestamp=f"10:{i:02d}", content=f"entry {i}")
            for i in range(15)
        ]
        new_entries = [MemoryEntry(timestamp="11:00", content="new entry")]
        result = _progressively_compress(existing, new_entries, keep_recent=10)
        # 1 compressed + 10 recent + 1 new = 12
        assert len(result) == 12
        assert "Compacted" in result[0].content
        assert result[-1].content == "new entry"

    def test_progressively_compress_keeps_last_10_detailed(self):
        """The last 10 existing entries appear unchanged after compression."""
        existing = [
            MemoryEntry(timestamp=f"10:{i:02d}", content=f"entry {i}")
            for i in range(15)
        ]
        new_entries = []
        result = _progressively_compress(existing, new_entries, keep_recent=10)
        # result[0] is compressed, result[1:11] are entries 5-14
        recent_contents = [e.content for e in result[1:]]
        expected = [f"entry {i}" for i in range(5, 15)]
        assert recent_contents == expected


# ---------------------------------------------------------------------------
# Compaction pipeline
# ---------------------------------------------------------------------------


def _make_soul_file(tmp_path: Path, instance_id: str = "inst-1") -> tuple[Path, SoulFile]:
    """Create a soul file fixture in tmp_path and return (path, SoulFile)."""
    souls_dir = tmp_path / "souls"
    souls_dir.mkdir(exist_ok=True)
    soul = SoulFile(
        instance_id=instance_id,
        identity="Working on lattice/orchestrator auth module as a senior dev",
        project_context="See .agent-docs/lattice.md",
        current_state=CurrentState(
            completed=["scaffold auth module"],
            in_progress=["implement token refresh"],
            blocked_on=[],
        ),
        preferences="Prefer immutable patterns",
        memory=[
            MemoryEntry(timestamp="09:00", content="started auth work"),
            MemoryEntry(timestamp="09:30", content="wrote unit tests"),
        ],
        compaction_count=0,
    )
    soul_path = souls_dir / f"instance-{instance_id}.md"
    write_soul_atomically(soul_path, soul.to_markdown())
    return soul_path, soul


class TestCompaction:
    """Tests for ContextManager.compact() — the full compaction pipeline."""

    def test_compact_sends_summarization_prompt(self, tmp_path: Path):
        """compact() sends a summarization prompt via write_message."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {
            "result": "- [10:00] fixed auth\n- [10:01] ran tests",
        }

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock) as mock_write, \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = summary_response
            asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )
            # First write_message call should include summarization prompt
            first_call_args = mock_write.call_args_list[0]
            payload = first_call_args[0][1]  # second positional arg is payload
            assert "Summarize" in payload.get("content", "")

    def test_compact_updates_soul_file_on_disk(self, tmp_path: Path):
        """compact() calls write_soul_atomically to persist the updated soul file."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(
            compaction_threshold=55.0,
            window_tokens=128_000,
            verification_enabled=False,
        )
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {"result": "- [10:00] fixed auth\n- [10:01] ran tests"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = summary_response
            asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        # Soul file should have been updated on disk
        assert soul_path.exists()
        updated_text = soul_path.read_text()
        assert "fixed auth" in updated_text

    def test_compact_sends_clear_message(self, tmp_path: Path):
        """compact() sends a clear message via write_message after soul file update."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(
            compaction_threshold=55.0,
            window_tokens=128_000,
            verification_enabled=False,
        )
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {"result": "- [10:00] fixed auth"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock) as mock_write, \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = summary_response
            asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        # One of the write_message calls should include type=clear
        all_payloads = [call[0][1] for call in mock_write.call_args_list]
        has_clear = any(p.get("type") == "clear" for p in all_payloads)
        assert has_clear

    def test_compact_sends_reinject_with_soul_markdown(self, tmp_path: Path):
        """compact() sends reinject message containing updated soul file content."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(
            compaction_threshold=55.0,
            window_tokens=128_000,
            verification_enabled=False,
        )
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {"result": "- [10:00] fixed auth"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock) as mock_write, \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = summary_response
            asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        # One of the write_message calls should include soul markdown content
        all_payloads = [call[0][1] for call in mock_write.call_args_list]
        has_reinject = any(
            "## Identity" in p.get("content", "") for p in all_payloads
        )
        assert has_reinject

    def test_compact_resets_byte_counters(self, tmp_path: Path):
        """compact() resets byte counters re-seeded with soul markdown size."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(
            compaction_threshold=55.0,
            window_tokens=128_000,
            verification_enabled=False,
        )
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        # Drive utilization high
        cm.track_bytes_sent("inst-1", 500_000)
        high_pct = cm.get_utilization("inst-1")

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {"result": "- [10:00] fixed auth"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = summary_response
            asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        low_pct = cm.get_utilization("inst-1")
        assert low_pct < high_pct

    def test_compact_increments_compaction_count(self, tmp_path: Path):
        """compact() increments compaction_count from N to N+1."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(
            compaction_threshold=55.0,
            window_tokens=128_000,
            verification_enabled=False,
        )
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {"result": "- [10:00] fixed auth"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = summary_response
            result = asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        assert result.compaction_count == 1

    def test_compact_updates_duckdb_compaction_count(self, tmp_path: Path):
        """compact() updates compaction_count in DuckDB context_utilization table."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(
            compaction_threshold=55.0,
            window_tokens=128_000,
            verification_enabled=False,
        )
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)
        # Ensure row exists
        cm.track_bytes_sent("inst-1", 1000)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {"result": "- [10:00] fixed auth"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = summary_response
            asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        row = conn.execute(
            "SELECT compaction_count FROM context_utilization WHERE instance_id = ?",
            ["inst-1"],
        ).fetchone()
        assert row is not None
        assert row[0] == 1

    def test_compact_returns_compaction_result(self, tmp_path: Path):
        """compact() returns CompactionResult with instance_id and status."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(
            compaction_threshold=55.0,
            window_tokens=128_000,
            verification_enabled=False,
        )
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {"result": "- [10:00] fixed auth"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = summary_response
            result = asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        assert isinstance(result, CompactionResult)
        assert result.instance_id == "inst-1"
        assert result.status == "PASS"

    def test_compact_timeout_returns_skipped(self, tmp_path: Path):
        """asyncio.TimeoutError during summarization returns CompactionResult(status='SKIPPED')."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        async def raise_timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", side_effect=raise_timeout):
            result = asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        assert result.status == "SKIPPED"
        assert "timeout" in result.detail.lower()

    def test_compact_timeout_does_not_send_clear(self, tmp_path: Path):
        """On timeout, /clear is NOT sent."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        async def raise_timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock) as mock_write, \
             patch("lattice.orchestrator.context.read_message", side_effect=raise_timeout):
            asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        all_payloads = [call[0][1] for call in mock_write.call_args_list]
        has_clear = any(p.get("type") == "clear" for p in all_payloads)
        assert not has_clear

    def test_compact_soul_parse_error_returns_error(self, tmp_path: Path):
        """ValueError from SoulFile.from_markdown() returns CompactionResult(status='ERROR')."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(compaction_threshold=55.0, window_tokens=128_000)
        souls_dir = tmp_path / "souls"
        souls_dir.mkdir(exist_ok=True)
        # Write a malformed soul file
        soul_path = souls_dir / "instance-inst-bad.md"
        soul_path.write_text("## Only One Section\nsome content", encoding="utf-8")
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {"result": "- [10:00] fixed auth"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = summary_response
            result = asyncio.run(
                cm.compact("inst-bad", mock_stdin, mock_stdout)
            )

        assert result.status == "ERROR"


# ---------------------------------------------------------------------------
# Post-reinject verification
# ---------------------------------------------------------------------------


class TestVerification:
    """Tests for ContextManager._verify_reinject() echo-back probe."""

    def _make_cm(self, tmp_path: Path, verification_enabled: bool = True) -> ContextManager:
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(
            compaction_threshold=55.0,
            window_tokens=128_000,
            verification_enabled=verification_enabled,
        )
        souls_dir = tmp_path / "souls"
        souls_dir.mkdir(exist_ok=True)
        return ContextManager(conn, config, souls_dir)

    def _make_soul(self) -> SoulFile:
        return SoulFile(
            instance_id="inst-1",
            identity="Working on lattice/orchestrator auth module",
            project_context="See .agent-docs/lattice.md",
            current_state=CurrentState(
                completed=["scaffold auth module"],
                in_progress=["implement token refresh"],
                blocked_on=[],
            ),
            preferences="Prefer immutable patterns",
            memory=[
                MemoryEntry(timestamp="09:00", content="started auth work"),
            ],
            compaction_count=1,
        )

    def test_verify_reinject_sends_probe(self, tmp_path: Path):
        """_verify_reinject sends 'What is your current task' probe."""
        cm = self._make_cm(tmp_path)
        soul = self._make_soul()

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        response_with_terms = {
            "result": "My current task is to implement token refresh in lattice/orchestrator"
        }

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock) as mock_write, \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = response_with_terms
            asyncio.run(
                cm._verify_reinject("inst-1", soul, mock_stdin, mock_stdout)
            )

        all_payloads = [call[0][1] for call in mock_write.call_args_list]
        probe_sent = any(
            "What is your current task" in p.get("content", "")
            for p in all_payloads
        )
        assert probe_sent

    def test_verify_reinject_pass_when_terms_present(self, tmp_path: Path):
        """Returns PASS when response contains key terms from soul."""
        cm = self._make_cm(tmp_path)
        soul = self._make_soul()

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        response_with_terms = {
            "result": "My current task is to implement token refresh in lattice/orchestrator"
        }

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = response_with_terms
            result = asyncio.run(
                cm._verify_reinject("inst-1", soul, mock_stdin, mock_stdout)
            )

        assert result.status == "PASS"

    def test_verify_reinject_fail_when_no_terms(self, tmp_path: Path):
        """Returns FAIL when response has zero matching terms (after retry)."""
        cm = self._make_cm(tmp_path)
        soul = self._make_soul()

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        # Response with no key terms at all
        empty_response = {"result": "I have no idea what is happening"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = empty_response
            result = asyncio.run(
                cm._verify_reinject("inst-1", soul, mock_stdin, mock_stdout)
            )

        assert result.status == "FAIL"

    def test_verify_reinject_retry_reinjects_then_reprobes(self, tmp_path: Path):
        """On first FAIL, retries by re-injecting soul then re-probing."""
        cm = self._make_cm(tmp_path)
        soul = self._make_soul()

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        call_count = [0]

        async def mock_read(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First probe fails — no key terms
                return {"result": "I have no context"}
            else:
                # Second probe passes — has key terms
                return {"result": "My task is implement token refresh in lattice/orchestrator"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", side_effect=mock_read):
            result = asyncio.run(
                cm._verify_reinject("inst-1", soul, mock_stdin, mock_stdout)
            )

        assert result.status == "PASS"

    def test_verify_reinject_fail_after_two_failures(self, tmp_path: Path):
        """Two consecutive FAILs return CompactionResult(status='FAIL', detail='...retry')."""
        cm = self._make_cm(tmp_path)
        soul = self._make_soul()

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        empty_response = {"result": "nothing useful here"}

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = empty_response
            result = asyncio.run(
                cm._verify_reinject("inst-1", soul, mock_stdin, mock_stdout)
            )

        assert result.status == "FAIL"
        assert "Reinject verification failed after retry" in result.detail

    def test_verify_reinject_timeout_returns_fail(self, tmp_path: Path):
        """asyncio.TimeoutError during verification probe returns FAIL."""
        cm = self._make_cm(tmp_path)
        soul = self._make_soul()

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        async def raise_timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", side_effect=raise_timeout):
            result = asyncio.run(
                cm._verify_reinject("inst-1", soul, mock_stdin, mock_stdout)
            )

        assert result.status == "FAIL"
        assert "timeout" in result.detail.lower()

    def test_compact_skips_verify_when_disabled(self, tmp_path: Path):
        """When verification_enabled=False, compact() skips _verify_reinject."""
        conn = duckdb.connect(":memory:")
        config = ContextManagerConfig(
            compaction_threshold=55.0,
            window_tokens=128_000,
            verification_enabled=False,
        )
        soul_path, _ = _make_soul_file(tmp_path)
        souls_dir = soul_path.parent
        cm = ContextManager(conn, config, souls_dir)

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        summary_response = {"result": "- [10:00] fixed auth"}

        # read_message called only once (for summarization, not verification probe)
        read_call_count = [0]

        async def mock_read(*args, **kwargs):
            read_call_count[0] += 1
            return summary_response

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", side_effect=mock_read):
            result = asyncio.run(
                cm.compact("inst-1", mock_stdin, mock_stdout)
            )

        # Verification disabled: only 1 read (summarization response)
        assert read_call_count[0] == 1
        assert result.status == "PASS"

    def test_verify_reinject_logs_event(self, tmp_path: Path):
        """_verify_reinject logs 'reinject_verification' event (smoke test)."""
        cm = self._make_cm(tmp_path)
        soul = self._make_soul()

        mock_stdin = AsyncMock()
        mock_stdout = AsyncMock()

        response_with_terms = {
            "result": "My current task is implement token refresh in lattice/orchestrator"
        }

        with patch("lattice.orchestrator.context.write_message", new_callable=AsyncMock), \
             patch("lattice.orchestrator.context.read_message", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = response_with_terms
            result = asyncio.run(
                cm._verify_reinject("inst-1", soul, mock_stdin, mock_stdout)
            )

        # Just verify method ran successfully — logging is a side effect
        assert result.status == "PASS"
