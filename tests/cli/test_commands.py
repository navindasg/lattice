"""Tests for the lattice CLI commands.

Tests use Click's CliRunner to invoke the CLI without spawning subprocesses.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from lattice.cli.commands import cli


SAMPLE_PYTHON_FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_python"
SAMPLE_CROSS_CUTTING_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "sample_cross_cutting"
)


class TestMapInitCommand:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_map_init_creates_graph_json(self, tmp_path: Path) -> None:
        """Running map:init on an empty directory creates a valid _graph.json."""
        result = self.runner.invoke(cli, ["map:init", str(tmp_path)])
        assert result.exit_code == 0, result.output
        graph_path = tmp_path / ".agent-docs" / "_graph.json"
        assert graph_path.exists()

    def test_map_init_output_is_valid_json(self, tmp_path: Path) -> None:
        result = self.runner.invoke(cli, ["map:init", str(tmp_path)])
        assert result.exit_code == 0, result.output
        graph_path = tmp_path / ".agent-docs" / "_graph.json"
        with graph_path.open() as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_map_init_json_has_three_sections(self, tmp_path: Path) -> None:
        result = self.runner.invoke(cli, ["map:init", str(tmp_path)])
        assert result.exit_code == 0
        graph_path = tmp_path / ".agent-docs" / "_graph.json"
        with graph_path.open() as f:
            data = json.load(f)
        assert "metadata" in data
        assert "nodes" in data
        assert "edges" in data

    def test_map_init_prints_file_count(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')\n")
        result = self.runner.invoke(cli, ["map:init", str(tmp_path)])
        assert result.exit_code == 0
        assert "1" in result.output  # file count

    def test_map_init_prints_output_path(self, tmp_path: Path) -> None:
        result = self.runner.invoke(cli, ["map:init", str(tmp_path)])
        assert result.exit_code == 0
        assert "Output:" in result.output or "_graph.json" in result.output

    def test_map_init_on_sample_python_fixture(self, tmp_path: Path) -> None:
        """End-to-end test on the sample_python fixture."""
        # Run against the fixture and write output to tmp_path to avoid polluting fixtures
        # We copy the fixture files to tmp_path
        import shutil
        fixture_copy = tmp_path / "sample_python"
        shutil.copytree(str(SAMPLE_PYTHON_FIXTURE), str(fixture_copy))

        result = self.runner.invoke(cli, ["map:init", str(fixture_copy)])
        assert result.exit_code == 0, result.output

        graph_path = fixture_copy / ".agent-docs" / "_graph.json"
        assert graph_path.exists()

        with graph_path.open() as f:
            data = json.load(f)

        assert data["metadata"]["file_count"] > 0
        assert "python" in data["metadata"]["languages"]
        node_ids = [n["id"] for n in data["nodes"]]
        # main.py and routes.py should be in the graph
        python_files = [n for n in node_ids if n.endswith(".py")]
        assert len(python_files) > 0

    def test_map_init_sample_python_detects_entry_points(self, tmp_path: Path) -> None:
        """main.py should be detected as main entry point."""
        import shutil
        fixture_copy = tmp_path / "sample_python"
        shutil.copytree(str(SAMPLE_PYTHON_FIXTURE), str(fixture_copy))

        result = self.runner.invoke(cli, ["map:init", str(fixture_copy)])
        assert result.exit_code == 0, result.output

        graph_path = fixture_copy / ".agent-docs" / "_graph.json"
        with graph_path.open() as f:
            data = json.load(f)

        entry_points = [n for n in data["nodes"] if n["is_entry_point"]]
        assert len(entry_points) > 0

    def test_map_init_sample_python_has_blind_spots(self, tmp_path: Path) -> None:
        """main.py fixture has dynamic imports; should appear in blind_spots."""
        import shutil
        fixture_copy = tmp_path / "sample_python"
        shutil.copytree(str(SAMPLE_PYTHON_FIXTURE), str(fixture_copy))

        result = self.runner.invoke(cli, ["map:init", str(fixture_copy)])
        assert result.exit_code == 0, result.output

        graph_path = fixture_copy / ".agent-docs" / "_graph.json"
        with graph_path.open() as f:
            data = json.load(f)

        assert len(data["metadata"]["blind_spots"]) > 0

    def test_map_init_stdout_shows_entry_points_count(self, tmp_path: Path) -> None:
        """CLI prints entry points count to stdout."""
        (tmp_path / "main.py").write_text(
            "if __name__ == '__main__':\n    pass\n"
        )
        result = self.runner.invoke(cli, ["map:init", str(tmp_path)])
        assert result.exit_code == 0
        # Should mention entry points in output
        output_lower = result.output.lower()
        assert "entry" in output_lower or "output" in output_lower

    def test_map_init_skips_pycache_and_venv(self, tmp_path: Path) -> None:
        """Files in __pycache__, .venv, and node_modules are skipped."""
        (tmp_path / "main.py").write_text("x = 1\n")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "main.cpython-312.pyc").write_bytes(b"fake bytecode")
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "fake.py").write_text("x = 1\n")

        result = self.runner.invoke(cli, ["map:init", str(tmp_path)])
        assert result.exit_code == 0

        graph_path = tmp_path / ".agent-docs" / "_graph.json"
        with graph_path.open() as f:
            data = json.load(f)

        # Only main.py should be in the graph, not venv files or .pyc files
        node_ids = [n["id"] for n in data["nodes"]]
        assert not any("__pycache__" in nid for nid in node_ids)
        assert not any(".venv" in nid for nid in node_ids)

    def test_map_init_language_breakdown_in_output(self, tmp_path: Path) -> None:
        """CLI prints language breakdown."""
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        result = self.runner.invoke(cli, ["map:init", str(tmp_path)])
        assert result.exit_code == 0
        assert "Python" in result.output or "python" in result.output


# ---------------------------------------------------------------------------
# Helper: build a minimal _graph.json for map:gaps tests
# ---------------------------------------------------------------------------

def _make_graph_json(nodes: list[dict], edges: list[dict]) -> dict:
    """Return a minimal _graph.json-compatible dict."""
    from datetime import datetime, timezone
    return {
        "metadata": {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "file_count": len(nodes),
            "languages": {"python": len(nodes)},
            "blind_spots": [],
        },
        "nodes": nodes,
        "edges": edges,
    }


def _write_graph_json(target: Path, graph_data: dict) -> Path:
    """Write _graph.json to target/.agent-docs/ and return its path."""
    agent_docs = target / ".agent-docs"
    agent_docs.mkdir(parents=True, exist_ok=True)
    graph_path = agent_docs / "_graph.json"
    graph_path.write_text(json.dumps(graph_data, indent=2))
    return graph_path


# ---------------------------------------------------------------------------
# TestMapGapsCommand
# ---------------------------------------------------------------------------


class TestMapGapsCommand:
    """Tests for the `lattice map:gaps` CLI command."""

    def setup_method(self) -> None:
        self.runner = CliRunner()

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_missing_graph_json_exits_with_error(self, tmp_path: Path) -> None:
        """map:gaps fails with clear error when _graph.json does not exist."""
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code != 0
        assert "map:init" in result.output

    def test_missing_graph_json_error_mentions_graph_path(
        self, tmp_path: Path
    ) -> None:
        """Error message mentions the missing graph path."""
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code != 0
        assert "_graph.json" in result.output or "No dependency graph" in result.output

    # ------------------------------------------------------------------
    # Successful runs
    # ------------------------------------------------------------------

    def _setup_minimal_project(self, tmp_path: Path) -> None:
        """Create a minimal project with _graph.json and a test file."""
        nodes = [
            {
                "id": "src/app.py",
                "language": "python",
                "is_entry_point": True,
                "entry_point_type": "main",
                "entry_details": None,
                "exports": [],
            },
            {
                "id": "src/db.py",
                "language": "python",
                "is_entry_point": False,
                "entry_point_type": None,
                "entry_details": None,
                "exports": [],
            },
        ]
        edges = [
            {"source": "src/app.py", "target": "src/db.py", "import_type": "standard"}
        ]
        graph_data = _make_graph_json(nodes, edges)
        _write_graph_json(tmp_path, graph_data)

        # Create the actual source files so TypeScript/Python adapters don't fail
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "app.py").write_text("from src.db import DB\n")
        (src_dir / "db.py").write_text("class DB: pass\n")

        # Create an integration test file
        tests_dir = tmp_path / "tests" / "integration"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_app.py").write_text(
            "from src.app import *\n\ndef test_app(): pass\n"
        )

    def test_valid_run_writes_test_coverage_json(self, tmp_path: Path) -> None:
        """map:gaps writes _test_coverage.json on success."""
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        coverage_path = tmp_path / ".agent-docs" / "_test_coverage.json"
        assert coverage_path.exists()

    def test_valid_run_output_has_metadata_section(self, tmp_path: Path) -> None:
        """_test_coverage.json has a metadata section."""
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        coverage_path = tmp_path / ".agent-docs" / "_test_coverage.json"
        data = json.loads(coverage_path.read_text())
        assert "metadata" in data

    def test_valid_run_output_has_test_files_section(self, tmp_path: Path) -> None:
        """_test_coverage.json has a test_files section."""
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        data = json.loads(
            (tmp_path / ".agent-docs" / "_test_coverage.json").read_text()
        )
        assert "test_files" in data

    def test_valid_run_output_has_covered_edges_section(
        self, tmp_path: Path
    ) -> None:
        """_test_coverage.json has a covered_edges section."""
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        data = json.loads(
            (tmp_path / ".agent-docs" / "_test_coverage.json").read_text()
        )
        assert "covered_edges" in data

    def test_valid_run_output_has_gaps_section(self, tmp_path: Path) -> None:
        """_test_coverage.json has a gaps section."""
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        data = json.loads(
            (tmp_path / ".agent-docs" / "_test_coverage.json").read_text()
        )
        assert "gaps" in data

    # ------------------------------------------------------------------
    # Summary header in stdout
    # ------------------------------------------------------------------

    def test_output_contains_tests_discovered_line(self, tmp_path: Path) -> None:
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Tests discovered:" in result.output

    def test_output_contains_total_edges_line(self, tmp_path: Path) -> None:
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Total edges:" in result.output

    def test_output_contains_covered_edges_line(self, tmp_path: Path) -> None:
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Covered edges:" in result.output

    def test_output_contains_uncovered_edges_line(self, tmp_path: Path) -> None:
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Uncovered edges:" in result.output

    def test_output_contains_output_path_line(self, tmp_path: Path) -> None:
        self._setup_minimal_project(tmp_path)
        result = self.runner.invoke(cli, ["map:gaps", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Output:" in result.output

    # ------------------------------------------------------------------
    # --top flag
    # ------------------------------------------------------------------

    def _setup_multi_edge_project(self, tmp_path: Path) -> None:
        """Create project with 5+ edges so --top can be tested."""
        nodes = [
            {
                "id": f"src/mod{i}.py",
                "language": "python",
                "is_entry_point": (i == 0),
                "entry_point_type": "main" if i == 0 else None,
                "entry_details": None,
                "exports": [],
            }
            for i in range(6)
        ]
        edges = [
            {
                "source": f"src/mod{i}.py",
                "target": f"src/mod{i+1}.py",
                "import_type": "standard",
            }
            for i in range(5)
        ]
        graph_data = _make_graph_json(nodes, edges)
        _write_graph_json(tmp_path, graph_data)

        # Actual source files
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        for i in range(6):
            (src_dir / f"mod{i}.py").write_text(f"# mod{i}\n")

        # No integration tests → all edges remain uncovered (good for --top test)
        tests_dir = tmp_path / "tests" / "unit"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_unit.py").write_text("def test_pass(): pass\n")

    def test_top_flag_limits_gaps_in_output_json(self, tmp_path: Path) -> None:
        """--top N limits gap entries in _test_coverage.json to at most N."""
        self._setup_multi_edge_project(tmp_path)
        result = self.runner.invoke(
            cli, ["map:gaps", str(tmp_path), "--top", "2"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(
            (tmp_path / ".agent-docs" / "_test_coverage.json").read_text()
        )
        assert len(data["gaps"]) <= 2


# ---------------------------------------------------------------------------
# TestMapCrossCommand
# ---------------------------------------------------------------------------


def _make_cross_graph_json(nodes: list[dict], edges: list[dict]) -> dict:
    """Return a minimal _graph.json dict for map:cross tests."""
    return {
        "metadata": {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "file_count": len(nodes),
            "languages": {"python": len(nodes)},
            "blind_spots": [],
        },
        "nodes": nodes,
        "edges": edges,
    }


class TestMapCrossCommand:
    """Tests for the `lattice map:cross` CLI command."""

    def setup_method(self) -> None:
        self.runner = CliRunner()

    def _setup_fixture_project(self, tmp_path: Path) -> Path:
        """Copy sample_cross_cutting fixture and write a minimal _graph.json."""
        project_dir = tmp_path / "project"
        shutil.copytree(str(SAMPLE_CROSS_CUTTING_FIXTURE), str(project_dir))

        # Write a minimal _graph.json so map:cross can proceed
        nodes = [
            {
                "id": "events/emitter.py",
                "language": "python",
                "is_entry_point": False,
                "entry_point_type": None,
                "entry_details": None,
                "exports": [],
            },
            {
                "id": "events/handlers.py",
                "language": "python",
                "is_entry_point": False,
                "entry_point_type": None,
                "entry_details": None,
                "exports": [],
            },
        ]
        edges = [
            {
                "source": "events/emitter.py",
                "target": "events/handlers.py",
                "import_type": "standard",
            }
        ]
        graph_data = _make_cross_graph_json(nodes=nodes, edges=edges)
        agent_docs = project_dir / ".agent-docs"
        agent_docs.mkdir(parents=True, exist_ok=True)
        (agent_docs / "_graph.json").write_text(
            json.dumps(graph_data, indent=2), encoding="utf-8"
        )
        return project_dir

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_missing_graph_json_exits_with_nonzero_code(
        self, tmp_path: Path
    ) -> None:
        """map:cross fails with non-zero exit when _graph.json missing."""
        result = self.runner.invoke(cli, ["map:cross", str(tmp_path)])
        assert result.exit_code != 0

    def test_missing_graph_json_error_mentions_map_init(
        self, tmp_path: Path
    ) -> None:
        """Error message references `map:init` for user guidance."""
        result = self.runner.invoke(cli, ["map:cross", str(tmp_path)])
        assert result.exit_code != 0
        assert "map:init" in result.output

    # ------------------------------------------------------------------
    # Successful runs
    # ------------------------------------------------------------------

    def test_exits_with_code_zero_on_valid_input(self, tmp_path: Path) -> None:
        """map:cross exits 0 on a valid fixture directory."""
        project_dir = self._setup_fixture_project(tmp_path)
        result = self.runner.invoke(cli, ["map:cross", str(project_dir)])
        assert result.exit_code == 0, result.output

    def test_writes_project_md_to_agent_docs(self, tmp_path: Path) -> None:
        """_project.md is written to .agent-docs/ after map:cross."""
        project_dir = self._setup_fixture_project(tmp_path)
        self.runner.invoke(cli, ["map:cross", str(project_dir)])
        project_md = project_dir / ".agent-docs" / "_project.md"
        assert project_md.exists()

    def test_stdout_contains_event_flows_line(self, tmp_path: Path) -> None:
        """stdout contains 'Event flows:' summary line."""
        project_dir = self._setup_fixture_project(tmp_path)
        result = self.runner.invoke(cli, ["map:cross", str(project_dir)])
        assert result.exit_code == 0, result.output
        assert "Event flows:" in result.output

    def test_stdout_contains_all_summary_lines(self, tmp_path: Path) -> None:
        """stdout contains all five summary categories."""
        project_dir = self._setup_fixture_project(tmp_path)
        result = self.runner.invoke(cli, ["map:cross", str(project_dir)])
        assert result.exit_code == 0, result.output
        assert "Shared state:" in result.output
        assert "API contracts:" in result.output
        assert "Plugin points:" in result.output
        assert "Blind spots:" in result.output

    def test_stdout_contains_output_path(self, tmp_path: Path) -> None:
        """stdout contains Output: path to _project.md."""
        project_dir = self._setup_fixture_project(tmp_path)
        result = self.runner.invoke(cli, ["map:cross", str(project_dir)])
        assert result.exit_code == 0, result.output
        assert "Output:" in result.output
        assert "_project.md" in result.output

    def test_augments_graph_json_with_cross_cutting_edges(
        self, tmp_path: Path
    ) -> None:
        """_graph.json gains a cross_cutting_edges section after map:cross."""
        project_dir = self._setup_fixture_project(tmp_path)
        self.runner.invoke(cli, ["map:cross", str(project_dir)])
        graph_path = project_dir / ".agent-docs" / "_graph.json"
        data = json.loads(graph_path.read_text())
        assert "cross_cutting_edges" in data

    def test_empty_codebase_produces_valid_project_md(
        self, tmp_path: Path
    ) -> None:
        """map:cross on empty codebase (no Python files) produces valid empty _project.md."""
        # Just an empty directory with _graph.json
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir(parents=True)
        graph_data = _make_cross_graph_json(nodes=[], edges=[])
        (agent_docs / "_graph.json").write_text(
            json.dumps(graph_data, indent=2), encoding="utf-8"
        )

        result = self.runner.invoke(cli, ["map:cross", str(tmp_path)])
        assert result.exit_code == 0, result.output

        project_md = agent_docs / "_project.md"
        assert project_md.exists()
        # File should have some content (at minimum the YAML frontmatter)
        content = project_md.read_text()
        assert len(content) > 0
