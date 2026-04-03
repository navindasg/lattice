"""Tests for the map:queue command and queue module core logic."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pytest

from lattice.cli.queue import _map_queue_impl, _read_queue, _write_queue


class TestReadWriteQueue:
    """Tests for _read_queue and _write_queue helpers."""

    def test_read_queue_missing_file(self, tmp_path: Path) -> None:
        """_read_queue returns {'entries': []} for a missing file."""
        queue_path = tmp_path / "_queue.json"
        result = _read_queue(queue_path)
        assert result == {"entries": []}

    def test_read_queue_corrupt_file(self, tmp_path: Path) -> None:
        """_read_queue returns {'entries': []} for a corrupt JSON file."""
        queue_path = tmp_path / "_queue.json"
        queue_path.write_text("not valid json", encoding="utf-8")
        result = _read_queue(queue_path)
        assert result == {"entries": []}

    def test_read_queue_valid_file(self, tmp_path: Path) -> None:
        """_read_queue parses and returns valid JSON dict."""
        queue_path = tmp_path / "_queue.json"
        data = {"entries": [{"commit_hash": "abc123", "status": "pending"}]}
        queue_path.write_text(json.dumps(data), encoding="utf-8")
        result = _read_queue(queue_path)
        assert result == data

    def test_write_queue_atomic(self, tmp_path: Path) -> None:
        """_write_queue uses tmp file then os.replace — tmp file should not persist."""
        queue_path = tmp_path / "_queue.json"
        tmp_path2 = tmp_path / "_queue.json.tmp"
        data = {"entries": []}
        _write_queue(data, queue_path, tmp_path2)
        assert queue_path.exists()
        assert not tmp_path2.exists(), "Temp file should be removed after atomic replace"

    def test_write_queue_content_correct(self, tmp_path: Path) -> None:
        """_write_queue writes valid JSON with correct content."""
        queue_path = tmp_path / "_queue.json"
        tmp_file = tmp_path / "_queue.json.tmp"
        data = {"entries": [{"commit_hash": "abc", "status": "pending"}]}
        _write_queue(data, queue_path, tmp_file)
        written = json.loads(queue_path.read_text(encoding="utf-8"))
        assert written == data


class TestMapQueueImpl:
    """Tests for _map_queue_impl core logic."""

    def test_queue_writes_entry(self, tmp_path: Path) -> None:
        """_map_queue_impl writes _queue.json with correct schema fields."""
        result = _map_queue_impl(tmp_path, "abc123", ["src/auth/session.py"])

        queue_path = tmp_path / ".agent-docs" / "_queue.json"
        assert queue_path.exists(), "_queue.json should be created"

        data = json.loads(queue_path.read_text(encoding="utf-8"))
        assert "entries" in data
        assert len(data["entries"]) == 1
        entry = data["entries"][0]
        assert entry["commit_hash"] == "abc123"
        assert "changed_files" in entry
        assert "affected_directories" in entry
        assert "upstream_consumers" in entry
        assert "queued_at" in entry
        assert entry["status"] == "pending"

    def test_queue_computes_affected_dirs(self, tmp_path: Path) -> None:
        """_map_queue_impl with changed file 'src/auth/session.py' sets affected_directories=['src/auth']."""
        _map_queue_impl(tmp_path, "abc123", ["src/auth/session.py"])

        queue_path = tmp_path / ".agent-docs" / "_queue.json"
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        entry = data["entries"][0]
        assert "src/auth" in entry["affected_directories"]

    def test_queue_no_graph_graceful(self, tmp_path: Path) -> None:
        """_map_queue_impl when _graph.json missing still writes queue entry with empty upstream_consumers."""
        result = _map_queue_impl(tmp_path, "abc123", ["src/auth/session.py"])

        queue_path = tmp_path / ".agent-docs" / "_queue.json"
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        entry = data["entries"][0]
        assert entry["upstream_consumers"] == []
        assert result["upstream_stale"] == []

    def test_queue_computes_upstream_consumers(self, tmp_path: Path) -> None:
        """_map_queue_impl with graph sets upstream_consumers from 1-hop predecessors."""
        # Create a simple graph where src/api imports from src/auth
        # In the graph: src/api/handler.py -> src/auth/session.py
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir(parents=True, exist_ok=True)
        graph_data = {
            "nodes": [
                {"id": "src/api/handler.py"},
                {"id": "src/auth/session.py"},
            ],
            "edges": [
                {"source": "src/api/handler.py", "target": "src/auth/session.py"}
            ],
        }
        graph_path = agent_docs / "_graph.json"
        graph_path.write_text(json.dumps(graph_data), encoding="utf-8")

        result = _map_queue_impl(tmp_path, "abc123", ["src/auth/session.py"])

        # src/api is an upstream consumer of src/auth (depends on src/auth)
        assert "src/api" in result["upstream_stale"]

        queue_path = agent_docs / "_queue.json"
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        entry = data["entries"][0]
        assert "src/api" in entry["upstream_consumers"]

    def test_queue_coalesces_same_dirs(self, tmp_path: Path) -> None:
        """Two _map_queue_impl calls with overlapping affected_directories produce one merged entry."""
        _map_queue_impl(tmp_path, "abc111", ["src/auth/session.py"])
        _map_queue_impl(tmp_path, "abc222", ["src/auth/models.py"])

        queue_path = tmp_path / ".agent-docs" / "_queue.json"
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        # Both files are in src/auth — should be coalesced into one entry
        pending = [e for e in data["entries"] if e["status"] == "pending"]
        assert len(pending) == 1
        entry = pending[0]
        # Changed files should be union
        assert "src/auth/session.py" in entry["changed_files"]
        assert "src/auth/models.py" in entry["changed_files"]

    def test_queue_coalesces_keeps_earliest_queued_at(self, tmp_path: Path) -> None:
        """Coalescing keeps the earliest queued_at timestamp."""
        _map_queue_impl(tmp_path, "abc111", ["src/auth/session.py"])
        first_queued_at = json.loads(
            (tmp_path / ".agent-docs" / "_queue.json").read_text()
        )["entries"][0]["queued_at"]

        _map_queue_impl(tmp_path, "abc222", ["src/auth/models.py"])
        data = json.loads((tmp_path / ".agent-docs" / "_queue.json").read_text())
        merged_entry = data["entries"][0]
        assert merged_entry["queued_at"] == first_queued_at

    def test_queue_atomic_write(self, tmp_path: Path) -> None:
        """_queue.json written via atomic tmp file (no .tmp file persists)."""
        _map_queue_impl(tmp_path, "abc123", ["src/a.py"])

        tmp_file = tmp_path / ".agent-docs" / "_queue.json.tmp"
        assert not tmp_file.exists(), "Temp file should not persist after write"

    def test_queue_return_value(self, tmp_path: Path) -> None:
        """_map_queue_impl returns dict with queued_directories, upstream_stale, commit_hash."""
        result = _map_queue_impl(tmp_path, "abc123", ["src/auth/session.py"])

        assert "queued_directories" in result
        assert "upstream_stale" in result
        assert result["commit_hash"] == "abc123"
        assert "src/auth" in result["queued_directories"]

    def test_queue_multiple_files_multiple_dirs(self, tmp_path: Path) -> None:
        """Multiple changed files in different directories produce multiple affected_directories."""
        result = _map_queue_impl(
            tmp_path,
            "abc123",
            ["src/auth/session.py", "src/api/handler.py"],
        )

        assert "src/auth" in result["queued_directories"]
        assert "src/api" in result["queued_directories"]

    def test_queue_test_status_triggered_for_test_files(self, tmp_path: Path) -> None:
        """_map_queue_impl sets test_status_triggered=True when test files are changed."""
        result = _map_queue_impl(tmp_path, "abc123", ["tests/cli/test_hooks.py"])
        assert result.get("test_status_triggered") is True

    def test_queue_test_status_not_triggered_for_non_test_files(self, tmp_path: Path) -> None:
        """_map_queue_impl sets test_status_triggered=False for non-test files."""
        result = _map_queue_impl(tmp_path, "abc123", ["src/auth/session.py"])
        assert result.get("test_status_triggered") is False


class TestMapperCommandIncludes:
    """Tests that MapperCommand accepts map:queue and map:test-status."""

    def test_mapper_command_includes_queue(self) -> None:
        """MapperCommand accepts 'map:queue' as a valid command."""
        from lattice.models.orchestrator import MapperCommand
        cmd = MapperCommand(command="map:queue")
        assert cmd.command == "map:queue"

    def test_mapper_command_includes_test_status(self) -> None:
        """MapperCommand accepts 'map:test-status' as a valid command."""
        from lattice.models.orchestrator import MapperCommand
        cmd = MapperCommand(command="map:test-status")
        assert cmd.command == "map:test-status"


# ---------------------------------------------------------------------------
# map:test-status tests
# ---------------------------------------------------------------------------


def _make_minimal_graph(tmp_path: Path) -> Path:
    """Create a minimal _graph.json for test-status tests.

    Graph: src/auth/session.py -> src/db/models.py  (cross-dir edge)
    """
    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir(parents=True, exist_ok=True)
    graph_data = {
        "nodes": [
            {"id": "src/auth/session.py", "is_entry_point": False},
            {"id": "src/db/models.py", "is_entry_point": False},
        ],
        "edges": [
            {
                "source": "src/auth/session.py",
                "target": "src/db/models.py",
                "import_type": "standard",
            }
        ],
    }
    graph_path = agent_docs / "_graph.json"
    graph_path.write_text(json.dumps(graph_data), encoding="utf-8")
    return agent_docs


def _make_dir_md(agent_docs: Path, directory: str) -> None:
    """Write a minimal _dir.md file for testing."""
    from datetime import datetime, timezone
    from lattice.shadow.schema import DirDoc
    from lattice.shadow.writer import write_dir_doc

    doc = DirDoc(
        directory=directory,
        confidence=0.7,
        source="static",
        confidence_factors=[],
        last_analyzed=datetime.now(timezone.utc),
    )
    write_dir_doc(doc, agent_docs)


class TestMapTestStatusImpl:
    """Tests for _map_test_status_impl core logic."""

    def test_test_status_no_graph_returns_error(self, tmp_path: Path) -> None:
        """_map_test_status_impl returns GRAPH_NOT_FOUND when _graph.json missing."""
        from lattice.cli.queue import _map_test_status_impl

        result = _map_test_status_impl(tmp_path)
        assert result.get("error") == "GRAPH_NOT_FOUND"
        assert result.get("updated_directories") == []

    def test_test_status_updates_coverage_json(self, tmp_path: Path) -> None:
        """_map_test_status_impl writes _test_coverage.json."""
        from unittest.mock import patch, MagicMock
        from lattice.cli.queue import _map_test_status_impl

        agent_docs = _make_minimal_graph(tmp_path)

        # Mock TestDiscovery/TestClassifier/CoverageBuilder to avoid real test files
        with patch("lattice.cli.queue.TestDiscovery") as MockDiscovery, \
             patch("lattice.cli.queue.TestClassifier") as MockClassifier, \
             patch("lattice.cli.queue.CoverageBuilder") as MockCovBuilder:
            mock_discovery = MockDiscovery.return_value
            mock_discovery.discover.return_value = []
            mock_classifier = MockClassifier.return_value
            mock_classifier.classify_all.return_value = []
            mock_coverage = MagicMock()
            mock_coverage.covered_edges = []
            mock_coverage.gaps = []
            mock_builder = MockCovBuilder.return_value
            mock_builder.build.return_value = mock_coverage
            MockCovBuilder.serialize.return_value = {"metadata": {}, "test_files": [], "covered_edges": [], "gaps": []}

            _map_test_status_impl(tmp_path)

        coverage_path = agent_docs / "_test_coverage.json"
        assert coverage_path.exists(), "_test_coverage.json should be written"
        data = json.loads(coverage_path.read_text())
        assert "metadata" in data

    def test_test_status_does_not_change_last_analyzed(self, tmp_path: Path) -> None:
        """_map_test_status_impl does not update last_analyzed in _dir.md files."""
        from unittest.mock import patch, MagicMock
        from datetime import datetime, timezone
        from lattice.cli.queue import _map_test_status_impl
        from lattice.shadow.reader import parse_dir_doc
        from lattice.shadow.schema import DirDoc
        from lattice.shadow.writer import write_dir_doc

        agent_docs = _make_minimal_graph(tmp_path)
        _make_dir_md(agent_docs, "src/auth")

        # Record last_analyzed before
        dir_md = agent_docs / "src" / "auth" / "_dir.md"
        original_doc = parse_dir_doc(dir_md)
        original_last_analyzed = original_doc.last_analyzed

        with patch("lattice.cli.queue.TestDiscovery") as MockDiscovery, \
             patch("lattice.cli.queue.TestClassifier") as MockClassifier, \
             patch("lattice.cli.queue.CoverageBuilder") as MockCovBuilder:
            mock_discovery = MockDiscovery.return_value
            mock_discovery.discover.return_value = []
            mock_classifier = MockClassifier.return_value
            mock_classifier.classify_all.return_value = []
            mock_coverage = MagicMock()
            mock_coverage.covered_edges = [{"source": "src/auth/session.py", "target": "src/db/models.py"}]
            mock_coverage.gaps = []
            mock_builder = MockCovBuilder.return_value
            mock_builder.build.return_value = mock_coverage
            MockCovBuilder.serialize.return_value = {"metadata": {}, "test_files": [], "covered_edges": [], "gaps": []}

            _map_test_status_impl(tmp_path)

        # Re-read and confirm last_analyzed unchanged
        updated_doc = parse_dir_doc(dir_md)
        assert updated_doc.last_analyzed == original_last_analyzed

    def test_test_status_updates_integration_points(self, tmp_path: Path) -> None:
        """_map_test_status_impl sets integration_points with TESTED/UNTESTED status."""
        from unittest.mock import patch, MagicMock
        from lattice.cli.queue import _map_test_status_impl
        from lattice.shadow.reader import parse_dir_doc

        agent_docs = _make_minimal_graph(tmp_path)
        _make_dir_md(agent_docs, "src/auth")

        dir_md = agent_docs / "src" / "auth" / "_dir.md"

        with patch("lattice.cli.queue.TestDiscovery") as MockDiscovery, \
             patch("lattice.cli.queue.TestClassifier") as MockClassifier, \
             patch("lattice.cli.queue.CoverageBuilder") as MockCovBuilder:
            mock_discovery = MockDiscovery.return_value
            mock_discovery.discover.return_value = []
            mock_classifier = MockClassifier.return_value
            mock_classifier.classify_all.return_value = []
            mock_coverage = MagicMock()
            # The edge from auth -> db is covered
            mock_coverage.covered_edges = [{"source": "src/auth/session.py", "target": "src/db/models.py"}]
            mock_coverage.gaps = []
            mock_builder = MockCovBuilder.return_value
            mock_builder.build.return_value = mock_coverage
            MockCovBuilder.serialize.return_value = {
                "metadata": {}, "test_files": [],
                "covered_edges": [{"source": "src/auth/session.py", "target": "src/db/models.py"}],
                "gaps": []
            }

            result = _map_test_status_impl(tmp_path)

        updated_doc = parse_dir_doc(dir_md)
        assert len(updated_doc.integration_points) > 0
        # Find the TESTED point
        tested = [p for p in updated_doc.integration_points if p["status"] == "TESTED"]
        assert len(tested) > 0

    def test_test_status_no_llm_calls(self, tmp_path: Path) -> None:
        """_map_test_status_impl function does not import FleetDispatcher or get_model."""
        import inspect
        from lattice.cli import queue as queue_module

        source = inspect.getsource(queue_module)
        # The _map_test_status_impl function should not reference LLM modules
        fn_source = source[source.find("def _map_test_status_impl"):]
        # Find end of this function (next def at same indentation)
        next_fn_pos = fn_source.find("\ndef ", 1)
        if next_fn_pos > 0:
            fn_source = fn_source[:next_fn_pos]
        assert "FleetDispatcher" not in fn_source
        assert "get_model" not in fn_source

    def test_test_status_json_output_envelope(self, tmp_path: Path) -> None:
        """map:test-status --json outputs success_response envelope with updated_directories."""
        from unittest.mock import patch, MagicMock
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        _make_minimal_graph(tmp_path)

        with patch("lattice.cli.queue.TestDiscovery") as MockDiscovery, \
             patch("lattice.cli.queue.TestClassifier") as MockClassifier, \
             patch("lattice.cli.queue.CoverageBuilder") as MockCovBuilder:
            mock_discovery = MockDiscovery.return_value
            mock_discovery.discover.return_value = []
            mock_classifier = MockClassifier.return_value
            mock_classifier.classify_all.return_value = []
            mock_coverage = MagicMock()
            mock_coverage.covered_edges = []
            mock_coverage.gaps = []
            mock_builder = MockCovBuilder.return_value
            mock_builder.build.return_value = mock_coverage
            MockCovBuilder.serialize.return_value = {"metadata": {}, "test_files": [], "covered_edges": [], "gaps": []}

            runner = CliRunner()
            result = runner.invoke(cli, ["map:test-status", str(tmp_path), "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data.get("success") is True
        assert "updated_directories" in data.get("data", {})

    def test_test_status_cli_summary_output(self, tmp_path: Path) -> None:
        """map:test-status (no --json) prints 'Updated N directories' summary."""
        from unittest.mock import patch, MagicMock
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        _make_minimal_graph(tmp_path)

        with patch("lattice.cli.queue.TestDiscovery") as MockDiscovery, \
             patch("lattice.cli.queue.TestClassifier") as MockClassifier, \
             patch("lattice.cli.queue.CoverageBuilder") as MockCovBuilder:
            mock_discovery = MockDiscovery.return_value
            mock_discovery.discover.return_value = []
            mock_classifier = MockClassifier.return_value
            mock_classifier.classify_all.return_value = []
            mock_coverage = MagicMock()
            mock_coverage.covered_edges = []
            mock_coverage.gaps = []
            mock_builder = MockCovBuilder.return_value
            mock_builder.build.return_value = mock_coverage
            MockCovBuilder.serialize.return_value = {"metadata": {}, "test_files": [], "covered_edges": [], "gaps": []}

            runner = CliRunner()
            result = runner.invoke(cli, ["map:test-status", str(tmp_path)])

        assert result.exit_code == 0
        assert "Updated" in result.output


class TestQueueTestStatusTriggering:
    """Tests for map:queue triggering map:test-status when test files change."""

    def test_queue_triggers_test_status_for_test_file(self, tmp_path: Path) -> None:
        """_map_queue_impl returns test_status_triggered=True when test files are in changed set."""
        result = _map_queue_impl(tmp_path, "abc123", ["tests/auth/test_auth.py"])
        assert result.get("test_status_triggered") is True

    def test_queue_no_test_status_for_source_files(self, tmp_path: Path) -> None:
        """_map_queue_impl returns test_status_triggered=False for source files only."""
        result = _map_queue_impl(tmp_path, "abc123", ["src/auth/session.py"])
        assert result.get("test_status_triggered") is False

    def test_queue_calls_test_status_when_graph_exists_and_test_file(self, tmp_path: Path) -> None:
        """_map_queue_impl calls _map_test_status_impl when test files change and _graph.json exists."""
        from unittest.mock import patch, MagicMock
        agent_docs = _make_minimal_graph(tmp_path)

        with patch("lattice.cli.queue._map_test_status_impl") as mock_test_status:
            mock_test_status.return_value = {"updated_directories": [], "total_covered_edges": 0}
            result = _map_queue_impl(tmp_path, "abc123", ["tests/test_auth.py"])

        mock_test_status.assert_called_once()
        assert result.get("test_status_triggered") is True
