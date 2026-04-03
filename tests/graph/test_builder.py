"""Tests for DependencyGraphBuilder.

Tests that graph builder constructs a NetworkX DiGraph from FileAnalysis objects
with correct node attributes, edge filtering, and edge attributes.
"""
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pytest

from lattice.graph.builder import DependencyGraphBuilder
from lattice.models.analysis import FileAnalysis, ImportInfo


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_analysis(
    path: str,
    language: str = "python",
    imports: list[ImportInfo] | None = None,
    exports: list[str] | None = None,
    has_main_guard: bool = False,
) -> FileAnalysis:
    return FileAnalysis(
        path=path,
        language=language,
        imports=imports or [],
        exports=exports or [],
        has_main_guard=has_main_guard,
        analyzed_at=_now(),
    )


def make_import(
    module: str,
    import_type: str = "standard",
    resolved_path: str | None = None,
    is_external: bool = False,
    line_number: int = 1,
    raw_expression: str | None = None,
) -> ImportInfo:
    return ImportInfo(
        module=module,
        import_type=import_type,
        resolved_path=resolved_path,
        is_external=is_external,
        line_number=line_number,
        raw_expression=raw_expression,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDependencyGraphBuilder:
    def setup_method(self) -> None:
        self.builder = DependencyGraphBuilder()
        self.project_root = Path("/project")

    def test_empty_analyses_produces_empty_graph(self) -> None:
        graph = self.builder.build([], self.project_root)
        assert isinstance(graph, nx.DiGraph)
        assert graph.number_of_nodes() == 0
        assert graph.number_of_edges() == 0

    def test_single_file_becomes_node(self) -> None:
        analysis = make_analysis("src/main.py", language="python")
        graph = self.builder.build([analysis], self.project_root)
        assert graph.number_of_nodes() == 1
        assert "src/main.py" in graph.nodes

    def test_node_key_is_relative_path(self) -> None:
        analysis = make_analysis("src/utils.py")
        graph = self.builder.build([analysis], self.project_root)
        assert list(graph.nodes)[0] == "src/utils.py"

    def test_node_attributes_language(self) -> None:
        analysis = make_analysis("src/main.py", language="python")
        graph = self.builder.build([analysis], self.project_root)
        node_data = graph.nodes["src/main.py"]
        assert node_data["language"] == "python"

    def test_node_attributes_is_entry_point_defaults_false(self) -> None:
        analysis = make_analysis("src/main.py")
        graph = self.builder.build([analysis], self.project_root)
        node_data = graph.nodes["src/main.py"]
        assert node_data["is_entry_point"] is False

    def test_node_attributes_entry_point_type_defaults_none(self) -> None:
        analysis = make_analysis("src/main.py")
        graph = self.builder.build([analysis], self.project_root)
        node_data = graph.nodes["src/main.py"]
        assert node_data["entry_point_type"] is None

    def test_node_attributes_entry_details_defaults_none(self) -> None:
        analysis = make_analysis("src/main.py")
        graph = self.builder.build([analysis], self.project_root)
        node_data = graph.nodes["src/main.py"]
        assert node_data["entry_details"] is None

    def test_node_carries_has_main_guard(self) -> None:
        analysis = make_analysis("src/main.py", has_main_guard=True)
        graph = self.builder.build([analysis], self.project_root)
        assert graph.nodes["src/main.py"]["has_main_guard"] is True

    def test_node_carries_exports(self) -> None:
        analysis = make_analysis("src/utils.py", exports=["helper", "parse"])
        graph = self.builder.build([analysis], self.project_root)
        assert graph.nodes["src/utils.py"]["exports"] == ["helper", "parse"]

    def test_internal_import_creates_edge(self) -> None:
        utils = make_analysis("src/utils.py")
        main = make_analysis(
            "src/main.py",
            imports=[
                make_import(
                    module="src.utils",
                    import_type="standard",
                    resolved_path="src/utils.py",
                    is_external=False,
                )
            ],
        )
        graph = self.builder.build([utils, main], self.project_root)
        assert graph.has_edge("src/main.py", "src/utils.py")

    def test_external_import_does_not_create_edge(self) -> None:
        main = make_analysis(
            "src/main.py",
            imports=[
                make_import(
                    module="os",
                    import_type="standard",
                    is_external=True,
                )
            ],
        )
        graph = self.builder.build([main], self.project_root)
        assert graph.number_of_edges() == 0

    def test_dynamic_import_does_not_create_edge(self) -> None:
        main = make_analysis(
            "src/main.py",
            imports=[
                make_import(
                    module="some_module",
                    import_type="dynamic",
                    resolved_path="src/some_module.py",
                    is_external=False,
                )
            ],
        )
        other = make_analysis("src/some_module.py")
        graph = self.builder.build([main, other], self.project_root)
        assert graph.number_of_edges() == 0

    def test_edge_has_import_type_attribute(self) -> None:
        utils = make_analysis("src/utils.py")
        main = make_analysis(
            "src/main.py",
            imports=[
                make_import(
                    module="src.utils",
                    import_type="relative",
                    resolved_path="src/utils.py",
                    is_external=False,
                )
            ],
        )
        graph = self.builder.build([utils, main], self.project_root)
        edge_data = graph.edges["src/main.py", "src/utils.py"]
        assert edge_data["import_type"] == "relative"

    def test_unresolved_import_does_not_create_edge(self) -> None:
        """Imports without resolved_path should not create edges."""
        main = make_analysis(
            "src/main.py",
            imports=[
                make_import(
                    module="unknown",
                    import_type="standard",
                    resolved_path=None,
                    is_external=False,
                )
            ],
        )
        graph = self.builder.build([main], self.project_root)
        assert graph.number_of_edges() == 0

    def test_import_to_unknown_node_does_not_create_edge(self) -> None:
        """Edges only created when resolved_path points to a known node."""
        main = make_analysis(
            "src/main.py",
            imports=[
                make_import(
                    module="src.missing",
                    import_type="standard",
                    resolved_path="src/missing.py",
                    is_external=False,
                )
            ],
        )
        # src/missing.py is not in analyses
        graph = self.builder.build([main], self.project_root)
        assert graph.number_of_edges() == 0

    def test_multiple_files_multiple_edges(self) -> None:
        a = make_analysis("src/a.py")
        b = make_analysis("src/b.py")
        c = make_analysis(
            "src/c.py",
            imports=[
                make_import(
                    module="src.a",
                    import_type="standard",
                    resolved_path="src/a.py",
                    is_external=False,
                ),
                make_import(
                    module="src.b",
                    import_type="standard",
                    resolved_path="src/b.py",
                    is_external=False,
                ),
            ],
        )
        graph = self.builder.build([a, b, c], self.project_root)
        assert graph.number_of_nodes() == 3
        assert graph.number_of_edges() == 2
        assert graph.has_edge("src/c.py", "src/a.py")
        assert graph.has_edge("src/c.py", "src/b.py")

    def test_typescript_node_has_correct_language(self) -> None:
        analysis = make_analysis("src/app.ts", language="typescript")
        graph = self.builder.build([analysis], self.project_root)
        assert graph.nodes["src/app.ts"]["language"] == "typescript"
