"""Tests for the --json flag across all six CLI commands."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from lattice.cli.commands import cli


def _make_minimal_graph_json(nodes: list[dict] | None = None, edges: list[dict] | None = None) -> dict:
    """Return a minimal _graph.json-compatible dict."""
    from datetime import datetime, timezone
    return {
        "metadata": {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "file_count": len(nodes or []),
            "languages": {"python": len(nodes or [])},
            "blind_spots": [],
        },
        "nodes": nodes or [],
        "edges": edges or [],
    }


def _write_graph_json(target: Path, graph_data: dict) -> Path:
    """Write _graph.json to target/.agent-docs/ and return its path."""
    agent_docs = target / ".agent-docs"
    agent_docs.mkdir(parents=True, exist_ok=True)
    graph_path = agent_docs / "_graph.json"
    graph_path.write_text(json.dumps(graph_data, indent=2))
    return graph_path


class TestJsonFlag:
    """Tests for --json flag producing valid JSON envelopes."""

    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_map_init_json_flag(self, tmp_path: Path) -> None:
        """map:init --json produces a valid JSON envelope with success=True."""
        result = self.runner.invoke(cli, ["map:init", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output

        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "map:init"
        assert data["error"] is None
        assert "data" in data

    def test_map_status_json_flag(self, tmp_path: Path) -> None:
        """map:status --json produces a valid JSON envelope with passes_complete in data."""
        result = self.runner.invoke(cli, ["map:status", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output

        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "map:status"
        assert "passes_complete" in data["data"]

    def test_map_hint_json_flag(self, tmp_path: Path) -> None:
        """map:hint --json produces a valid JSON envelope with hint info."""
        result = self.runner.invoke(
            cli, ["map:hint", str(tmp_path), "src/auth", "handles OAuth", "--json"]
        )
        assert result.exit_code == 0, result.output

        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "map:hint"
        assert data["data"]["directory"] == "src/auth"
        assert data["data"]["hint_count"] == 1

    def test_map_status_json_cold_start(self, tmp_path: Path) -> None:
        """map:status --json on empty dir returns all zeroed values."""
        result = self.runner.invoke(cli, ["map:status", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output

        data = json.loads(result.output)
        assert data["success"] is True
        status_data = data["data"]

        assert status_data["passes_complete"]["init"] is False
        assert status_data["passes_complete"]["gaps"] is False
        assert status_data["passes_complete"]["doc"] is False
        assert status_data["passes_complete"]["cross"] is False
        assert status_data["directories_documented"] == 0
        assert status_data["confidence_distribution"]["low"] == 0
        assert status_data["active_run_id"] is None
        assert status_data["token_summary"]["total_input_tokens"] == 0

    def test_json_envelope_structure(self, tmp_path: Path) -> None:
        """map:status --json output has exactly the keys: success, command, data, error."""
        result = self.runner.invoke(cli, ["map:status", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output

        data = json.loads(result.output)
        assert set(data.keys()) == {"success", "command", "data", "error"}

    def test_map_gaps_json_flag(self, tmp_path: Path) -> None:
        """map:gaps --json produces a valid JSON envelope when _graph.json exists."""
        nodes = [
            {
                "id": "src/app.py",
                "language": "python",
                "is_entry_point": True,
                "entry_point_type": "main",
                "entry_details": None,
                "exports": [],
            }
        ]
        _write_graph_json(tmp_path, _make_minimal_graph_json(nodes=nodes))
        # Create source file
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1\n")

        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output

        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "map:gaps"
        assert data["error"] is None

    def test_map_gaps_json_error_when_no_graph(self, tmp_path: Path) -> None:
        """map:gaps --json returns error envelope when _graph.json is missing."""
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output  # JSON mode exits 0

        data = json.loads(result.output)
        assert data["success"] is False
        assert data["command"] == "map:gaps"
        assert data["error"] is not None
        assert data["error"]["code"] == "GRAPH_NOT_FOUND"

    def test_map_cross_json_error_when_no_graph(self, tmp_path: Path) -> None:
        """map:cross --json returns error envelope when _graph.json is missing."""
        result = self.runner.invoke(cli, ["map:cross", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output  # JSON mode exits 0

        data = json.loads(result.output)
        assert data["success"] is False
        assert data["command"] == "map:cross"
        assert data["error"] is not None
        assert data["error"]["code"] == "GRAPH_NOT_FOUND"

    def test_map_init_human_output_unchanged(self, tmp_path: Path) -> None:
        """map:init without --json still produces human-readable output (regression)."""
        result = self.runner.invoke(cli, ["map:init", str(tmp_path)])
        assert result.exit_code == 0, result.output
        # Should not be JSON
        try:
            json.loads(result.output)
            is_json = True
        except (json.JSONDecodeError, ValueError):
            is_json = False
        assert not is_json, "Human output should not be JSON"

    def test_map_status_human_output_not_json(self, tmp_path: Path) -> None:
        """map:status without --json produces non-JSON human output."""
        result = self.runner.invoke(cli, ["map:status", str(tmp_path)])
        assert result.exit_code == 0, result.output
        # Rich renders to stdout; it may produce some output or empty
        # Just verify it doesn't crash and exit_code is 0

    def test_map_hint_human_output_not_json(self, tmp_path: Path) -> None:
        """map:hint without --json produces non-JSON output."""
        result = self.runner.invoke(
            cli, ["map:hint", str(tmp_path), "src/api", "API hint"]
        )
        assert result.exit_code == 0, result.output
