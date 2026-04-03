"""ConfigWiringDetector — adds config file nodes and config_ref edges to the graph.

Scans the project root for configuration files and adds them as graph nodes.
Connects source modules that reference config keys to the corresponding config
node via directed edges with import_type="config_ref".

Config files detected:
    .env                 — key=value pairs; config_type="env"
    docker-compose.yml   — Docker Compose services; config_type="docker"
    docker-compose.yaml  — same (alternate extension)
    config.yaml          — generic YAML config; config_type="yaml"
    config.yml           — same (alternate extension)

Detection strategy for edges:
    Source modules referencing config keys are detected via FileAnalysis.imports
    with import_type="config_ref". This avoids re-parsing the AST here and
    relies on the adapter's classification.

NOTE: Graph node mutation is a documented exception to immutability rules.
NetworkX stores node attributes in a mutable dict; ConfigWiringDetector mutates
them in-place to add config nodes and edges.
"""
from __future__ import annotations

from pathlib import Path

import networkx as nx

from lattice.models.analysis import FileAnalysis


# Config file name -> config_type mapping
_CONFIG_FILES: dict[str, str] = {
    ".env": "env",
    "docker-compose.yml": "docker",
    "docker-compose.yaml": "docker",
    "config.yaml": "yaml",
    "config.yml": "yaml",
}


def _parse_env_keys(env_path: Path) -> list[str]:
    """Parse a .env file and return a list of key names.

    Handles KEY=value lines; skips comments and blank lines.
    """
    keys: list[str] = []
    try:
        text = env_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key = line.split("=", 1)[0].strip()
                if key:
                    keys.append(key)
    except OSError:
        pass
    return keys


def _parse_docker_services(docker_path: Path) -> list[str]:
    """Parse a docker-compose file and return service names."""
    try:
        import yaml  # pyyaml already a project dependency
        text = docker_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if isinstance(data, dict) and "services" in data:
            services = data["services"]
            if isinstance(services, dict):
                return list(services.keys())
    except Exception:
        pass
    return []


class ConfigWiringDetector:
    """Adds config file nodes and config_ref edges to the dependency graph.

    Usage::

        detector = ConfigWiringDetector()
        detector.detect(project_root, graph, analyses)
        # graph now includes config nodes; source modules have config_ref edges
    """

    def detect(
        self,
        project_root: Path,
        graph: nx.DiGraph,
        analyses: list[FileAnalysis],
    ) -> None:
        """Add config nodes and edges in-place.

        Args:
            project_root: Directory to scan for config files.
            graph: DiGraph to mutate; config nodes and edges are added.
            analyses: FileAnalysis list to scan for config_ref imports.
        """
        # --- Phase 1: discover config files and add as nodes ---
        config_nodes: dict[str, str] = {}  # node_key -> config_type

        for filename, config_type in _CONFIG_FILES.items():
            candidate = project_root / filename
            if not candidate.exists():
                continue

            node_key = filename
            extra: dict[str, object] = {}

            if config_type == "env":
                env_keys = _parse_env_keys(candidate)
                extra["env_keys"] = env_keys
            elif config_type == "docker":
                services = _parse_docker_services(candidate)
                extra["services"] = services

            graph.add_node(
                node_key,
                language="config",
                config_type=config_type,
                **extra,
            )
            config_nodes[node_key] = config_type

        if not config_nodes:
            return

        # --- Phase 2: add config_ref edges from source modules ---
        for analysis in analyses:
            # Determine this analysis's node key in the graph
            source_key = self._find_node_key(analysis.path, graph)
            if source_key is None:
                continue

            # Check if any imports are config_refs
            has_config_ref = any(
                imp.import_type == "config_ref" for imp in analysis.imports
            )
            if not has_config_ref:
                continue

            # Connect to all detected config nodes (primary: .env)
            # Prefer .env if present; otherwise connect to first config node
            env_keys = [k for k, ct in config_nodes.items() if ct == "env"]
            targets = env_keys if env_keys else list(config_nodes.keys())

            for target_key in targets:
                if not graph.has_edge(source_key, target_key):
                    graph.add_edge(source_key, target_key, import_type="config_ref")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_node_key(path: str, graph: nx.DiGraph) -> str | None:
        """Find the graph node key corresponding to a FileAnalysis path."""
        # Direct match first
        if path in graph.nodes:
            return path
        # Suffix match (handles absolute vs relative paths)
        for node_key in graph.nodes:
            if path.endswith(node_key) or node_key.endswith(path):
                return node_key
        return None
