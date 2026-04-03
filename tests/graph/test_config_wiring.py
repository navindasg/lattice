"""Tests for ConfigWiringDetector.

Tests that config wiring detector correctly:
- Adds config file nodes to the graph with language="config" and config_type
- Connects source modules referencing env vars to config nodes via config_ref edges
- Parses docker-compose.yml for service definitions
"""
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pytest

from lattice.graph.builder import DependencyGraphBuilder
from lattice.graph.config_wiring import ConfigWiringDetector
from lattice.models.analysis import FileAnalysis, ImportInfo


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_analysis(
    path: str,
    language: str = "python",
    imports: list[ImportInfo] | None = None,
) -> FileAnalysis:
    return FileAnalysis(
        path=path,
        language=language,
        imports=imports or [],
        analyzed_at=_now(),
    )


def make_config_ref_import(module: str, raw_expression: str, line_number: int = 5) -> ImportInfo:
    return ImportInfo(
        module=module,
        import_type="config_ref",
        line_number=line_number,
        raw_expression=raw_expression,
    )


def build_graph(analyses: list[FileAnalysis]) -> nx.DiGraph:
    builder = DependencyGraphBuilder()
    return builder.build(analyses, Path("/project"))


class TestConfigWiringDetector:
    def setup_method(self) -> None:
        self.detector = ConfigWiringDetector()

    def test_env_file_node_added(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("DATABASE_URL=postgres://localhost/db\nSECRET_KEY=abc123\n")

        graph = build_graph([])
        self.detector.detect(tmp_path, graph, [])

        assert ".env" in graph.nodes
        assert graph.nodes[".env"]["language"] == "config"
        assert graph.nodes[".env"]["config_type"] == "env"

    def test_docker_compose_node_added(self, tmp_path: Path) -> None:
        docker_file = tmp_path / "docker-compose.yml"
        docker_file.write_text(
            "version: '3'\nservices:\n  web:\n    build: .\n    environment:\n      - DATABASE_URL\n"
        )

        graph = build_graph([])
        self.detector.detect(tmp_path, graph, [])

        assert "docker-compose.yml" in graph.nodes
        assert graph.nodes["docker-compose.yml"]["language"] == "config"
        assert graph.nodes["docker-compose.yml"]["config_type"] == "docker"

    def test_yaml_config_node_added(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("database:\n  host: localhost\n  port: 5432\n")

        graph = build_graph([])
        self.detector.detect(tmp_path, graph, [])

        assert "config.yaml" in graph.nodes
        assert graph.nodes["config.yaml"]["language"] == "config"
        assert graph.nodes["config.yaml"]["config_type"] == "yaml"

    def test_config_ref_import_creates_edge(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("DATABASE_URL=postgres://localhost/db\n")

        analysis = make_analysis(
            "src/db.py",
            imports=[make_config_ref_import(module="DATABASE_URL", raw_expression="os.getenv('DATABASE_URL')")],
        )
        graph = build_graph([analysis])
        self.detector.detect(tmp_path, graph, [analysis])

        # Edge should exist from db.py to .env
        assert graph.has_edge("src/db.py", ".env")
        edge_data = graph.edges["src/db.py", ".env"]
        assert edge_data["import_type"] == "config_ref"

    def test_no_config_files_empty(self, tmp_path: Path) -> None:
        """When no config files present, no config nodes added."""
        graph = build_graph([])
        self.detector.detect(tmp_path, graph, [])

        assert graph.number_of_nodes() == 0

    def test_docker_compose_yaml_variant(self, tmp_path: Path) -> None:
        """docker-compose.yaml (with .yaml extension) should also be detected."""
        docker_file = tmp_path / "docker-compose.yaml"
        docker_file.write_text("version: '3'\nservices:\n  app:\n    build: .\n")

        graph = build_graph([])
        self.detector.detect(tmp_path, graph, [])

        assert "docker-compose.yaml" in graph.nodes
        assert graph.nodes["docker-compose.yaml"]["config_type"] == "docker"

    def test_env_file_parsed_for_keys(self, tmp_path: Path) -> None:
        """Env file parsed; keys stored in node metadata."""
        env_file = tmp_path / ".env"
        env_file.write_text("API_KEY=secret\nDEBUG=true\n")

        graph = build_graph([])
        self.detector.detect(tmp_path, graph, [])

        node_data = graph.nodes[".env"]
        assert "env_keys" in node_data
        assert "API_KEY" in node_data["env_keys"]
        assert "DEBUG" in node_data["env_keys"]

    def test_multiple_config_ref_imports_to_same_env_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=val1\nKEY2=val2\n")

        analysis = make_analysis(
            "src/settings.py",
            imports=[
                make_config_ref_import(module="KEY1", raw_expression="os.getenv('KEY1')", line_number=5),
                make_config_ref_import(module="KEY2", raw_expression="os.environ['KEY2']", line_number=6),
            ],
        )
        graph = build_graph([analysis])
        self.detector.detect(tmp_path, graph, [analysis])

        # Only one edge to the env file (idempotent)
        assert graph.has_edge("src/settings.py", ".env")
