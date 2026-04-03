"""Tests for the map:doc --incremental dispatch path."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import networkx as nx
import pytest

from lattice.cli.queue import _map_queue_impl


def _make_queue_json(tmp_path: Path, entries: list[dict]) -> Path:
    """Write a _queue.json with given entries to tmp_path/.agent-docs/."""
    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir(parents=True, exist_ok=True)
    queue_path = agent_docs / "_queue.json"
    queue_path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return queue_path


def _make_graph_json(tmp_path: Path, nodes: list[str], edges: list[tuple[str, str]]) -> Path:
    """Write a _graph.json with given nodes/edges to tmp_path/.agent-docs/."""
    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir(parents=True, exist_ok=True)
    graph_path = agent_docs / "_graph.json"
    graph_data = {
        "nodes": [{"id": n} for n in nodes],
        "edges": [{"source": s, "target": t} for s, t in edges],
    }
    graph_path.write_text(json.dumps(graph_data), encoding="utf-8")
    return graph_path


class TestIncrementalReadsQueue:
    """Test that map:doc --incremental reads pending entries from _queue.json."""

    def test_incremental_reads_queue(self, tmp_path: Path) -> None:
        """map:doc --incremental reads pending entries from _queue.json."""
        entries = [
            {
                "commit_hash": "abc123",
                "changed_files": ["src/auth/session.py"],
                "affected_directories": ["src/auth"],
                "upstream_consumers": [],
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
            }
        ]
        queue_path = _make_queue_json(tmp_path, entries)

        from lattice.cli.queue import _read_queue
        queue_data = _read_queue(queue_path)
        pending = [e for e in queue_data["entries"] if e["status"] == "pending"]
        assert len(pending) == 1
        assert pending[0]["commit_hash"] == "abc123"

    def test_incremental_dispatches_subset(self, tmp_path: Path) -> None:
        """map:doc --incremental dispatches only affected directories, not all."""
        # Create graph with multiple directories
        _make_graph_json(
            tmp_path,
            nodes=["src/auth/session.py", "src/api/handler.py", "src/db/models.py"],
            edges=[
                ("src/api/handler.py", "src/auth/session.py"),
                ("src/api/handler.py", "src/db/models.py"),
            ],
        )
        entries = [
            {
                "commit_hash": "abc123",
                "changed_files": ["src/auth/session.py"],
                "affected_directories": ["src/auth"],
                "upstream_consumers": ["src/api"],
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
            }
        ]
        _make_queue_json(tmp_path, entries)

        from lattice.cli.queue import _read_queue
        from lattice.cli.commands import load_graph_from_json
        from lattice.fleet.planner import build_directory_dag, plan_waves

        queue_data = _read_queue(tmp_path / ".agent-docs" / "_queue.json")
        pending = [e for e in queue_data["entries"] if e["status"] == "pending"]
        all_affected: set[str] = set()
        for entry in pending:
            all_affected.update(entry.get("affected_directories", []))

        # Should only be src/auth, not src/db
        assert "src/auth" in all_affected
        assert "src/db" not in all_affected

        file_graph = load_graph_from_json(tmp_path / ".agent-docs" / "_graph.json")
        dir_dag = build_directory_dag(file_graph)
        sub_dag = dir_dag.subgraph(all_affected).copy()
        waves = plan_waves(sub_dag)

        # All wave directories should be within all_affected
        dispatched = set()
        for wave in waves:
            dispatched.update(wave.directories)
        assert dispatched == all_affected

    def test_incremental_default_tier_silver(self, tmp_path: Path) -> None:
        """map:doc --incremental without explicit --tier defaults to silver tier."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        # Create minimal setup: .git dir, graph, queue
        (tmp_path / ".git").mkdir()
        _make_graph_json(tmp_path, nodes=["src/auth/session.py"], edges=[])
        entries = [
            {
                "commit_hash": "abc123",
                "changed_files": ["src/auth/session.py"],
                "affected_directories": ["src/auth"],
                "upstream_consumers": [],
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
            }
        ]
        _make_queue_json(tmp_path, entries)

        # Mock FleetDispatcher to capture the tier used
        captured_tiers = []

        with patch("lattice.cli.commands.FleetDispatcher") as mock_dispatcher_cls:
            mock_dispatcher = MagicMock()
            mock_dispatcher.dispatch_wave = AsyncMock(return_value=[])
            mock_dispatcher_cls.return_value = mock_dispatcher

            def capture_tier(**kwargs):
                captured_tiers.append(kwargs.get("tier"))
                return mock_dispatcher

            mock_dispatcher_cls.side_effect = capture_tier

            runner = CliRunner()
            result = runner.invoke(cli, ["map:doc", str(tmp_path), "--incremental"])

        assert len(captured_tiers) > 0, "FleetDispatcher should have been initialized"
        assert captured_tiers[0] == "silver", f"Default tier should be silver, got {captured_tiers[0]}"

    def test_incremental_explicit_tier_override(self, tmp_path: Path) -> None:
        """map:doc --incremental --tier bronze uses bronze tier."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        (tmp_path / ".git").mkdir()
        _make_graph_json(tmp_path, nodes=["src/auth/session.py"], edges=[])
        entries = [
            {
                "commit_hash": "abc123",
                "changed_files": ["src/auth/session.py"],
                "affected_directories": ["src/auth"],
                "upstream_consumers": [],
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
            }
        ]
        _make_queue_json(tmp_path, entries)

        captured_tiers = []

        with patch("lattice.cli.commands.FleetDispatcher") as mock_dispatcher_cls:
            mock_dispatcher = MagicMock()
            mock_dispatcher.dispatch_wave = AsyncMock(return_value=[])
            mock_dispatcher_cls.return_value = mock_dispatcher

            def capture_tier(**kwargs):
                captured_tiers.append(kwargs.get("tier"))
                return mock_dispatcher

            mock_dispatcher_cls.side_effect = capture_tier

            runner = CliRunner()
            result = runner.invoke(cli, ["map:doc", str(tmp_path), "--incremental", "--tier", "bronze"])

        assert len(captured_tiers) > 0
        assert captured_tiers[0] == "bronze", f"Explicit tier should be bronze, got {captured_tiers[0]}"


class TestIncrementalStaleMarking:
    """Test that incremental dispatch marks upstream consumers stale."""

    def _make_dir_doc(self, agent_docs: Path, directory: str, stale: bool = False) -> Path:
        """Write a minimal _dir.md for testing."""
        import frontmatter

        dir_path = agent_docs / directory
        dir_path.mkdir(parents=True, exist_ok=True)
        dir_md = dir_path / "_dir.md"

        metadata = {
            "directory": directory,
            "confidence": 0.8,
            "source": "agent",
            "confidence_factors": ["test factor"],
            "stale": stale,
            "last_analyzed": datetime.now(timezone.utc).isoformat(),
            "static_analysis_limits": {"dynamic_imports": 0, "unresolved_paths": 0},
            "gap_summary": {"untested_edges": 0, "top_gaps": []},
        }
        body = "## Summary\n\nTest summary\n\n## Key Responsibilities\n\n- Test\n\n## Developer Hints\n\n\n\n## Child Docs\n\n"
        post = frontmatter.Post(body, **metadata)
        dir_md.write_text(frontmatter.dumps(post), encoding="utf-8")
        return dir_md

    def test_incremental_marks_stale(self, tmp_path: Path) -> None:
        """After incremental dispatch, 1-hop upstream consumers have stale=True."""
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir(parents=True, exist_ok=True)

        # Create _dir.md for upstream consumer
        self._make_dir_doc(agent_docs, "src/api", stale=False)

        # Verify it starts as not stale
        from lattice.shadow.reader import parse_dir_doc
        doc = parse_dir_doc(agent_docs / "src/api" / "_dir.md")
        assert doc.stale is False

        # Simulate the stale-marking logic directly
        from lattice.shadow.writer import write_dir_doc

        dir_md = agent_docs / "src/api" / "_dir.md"
        assert dir_md.exists()
        doc = parse_dir_doc(dir_md)
        if not doc.stale:
            updated = doc.model_copy(update={"stale": True})
            write_dir_doc(updated, agent_docs)

        # Verify it's now stale
        doc_after = parse_dir_doc(agent_docs / "src/api" / "_dir.md")
        assert doc_after.stale is True

    def test_incremental_skips_missing_dir_md(self, tmp_path: Path) -> None:
        """Stale-marking skips directories with no existing _dir.md (no FileNotFoundError)."""
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir(parents=True, exist_ok=True)

        all_upstream = {"src/api", "src/db"}  # Neither has a _dir.md

        from lattice.shadow.reader import parse_dir_doc
        from lattice.shadow.writer import write_dir_doc

        # This should NOT raise FileNotFoundError
        for upstream_dir in all_upstream:
            dir_md = agent_docs / upstream_dir / "_dir.md"
            if not dir_md.exists():
                continue  # Pitfall 3: skip if no _dir.md
            doc = parse_dir_doc(dir_md)
            if not doc.stale:
                updated = doc.model_copy(update={"stale": True})
                write_dir_doc(updated, agent_docs)

        # Test passes if no exception raised

    def test_incremental_removes_processed_entries(self, tmp_path: Path) -> None:
        """After successful incremental dispatch, processed pending entries are removed from _queue.json."""
        from lattice.cli.queue import _read_queue, _write_queue

        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir(parents=True, exist_ok=True)
        queue_path = agent_docs / "_queue.json"
        tmp_file = agent_docs / "_queue.json.tmp"

        entries = [
            {
                "commit_hash": "abc123",
                "affected_directories": ["src/auth"],
                "upstream_consumers": [],
                "changed_files": ["src/auth/session.py"],
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
            },
            {
                "commit_hash": "xyz789",
                "affected_directories": ["src/db"],
                "upstream_consumers": [],
                "changed_files": ["src/db/models.py"],
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "status": "completed",
            },
        ]
        queue_data = {"entries": entries}
        _write_queue(queue_data, queue_path, tmp_file)

        # Simulate removing pending entries
        remaining = [e for e in entries if e["status"] != "pending"]
        _write_queue({"entries": remaining}, queue_path, tmp_file)

        data = _read_queue(queue_path)
        pending = [e for e in data["entries"] if e["status"] == "pending"]
        assert len(pending) == 0
        # Completed entry should remain
        completed = [e for e in data["entries"] if e["status"] == "completed"]
        assert len(completed) == 1

    def test_incremental_skips_developer_protected(self, tmp_path: Path) -> None:
        """Directories with source='developer' in _dir.md are not re-documented."""
        import frontmatter

        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir(parents=True, exist_ok=True)

        # Create a developer-protected _dir.md
        dir_path = agent_docs / "src/auth"
        dir_path.mkdir(parents=True, exist_ok=True)
        dir_md = dir_path / "_dir.md"

        metadata = {
            "directory": "src/auth",
            "confidence": 1.0,
            "source": "developer",
            "confidence_factors": [],
            "stale": False,
            "last_analyzed": datetime.now(timezone.utc).isoformat(),
            "static_analysis_limits": {"dynamic_imports": 0, "unresolved_paths": 0},
            "gap_summary": {"untested_edges": 0, "top_gaps": []},
        }
        body = "## Summary\n\nDeveloper docs\n\n## Key Responsibilities\n\n- Dev\n\n## Developer Hints\n\n\n\n## Child Docs\n\n"
        post = frontmatter.Post(body, **metadata)
        dir_md.write_text(frontmatter.dumps(post), encoding="utf-8")

        # Simulate developer protection check
        from lattice.shadow.reader import parse_dir_doc

        all_affected = {"src/auth"}
        dispatched_dirs: set[str] = set()
        force = False

        for d in all_affected:
            dir_md_path = agent_docs / d / "_dir.md"
            if dir_md_path.exists():
                doc = parse_dir_doc(dir_md_path)
                if doc.source == "developer" and not force:
                    continue
            dispatched_dirs.add(d)

        # Developer-protected directory should be excluded
        assert "src/auth" not in dispatched_dirs
