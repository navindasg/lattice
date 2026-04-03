"""Tests for serialize_graph function.

Tests that the serializer produces a correct _graph.json-compatible dict
with metadata, nodes, and edges sections.
"""
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pytest

from lattice.graph.builder import DependencyGraphBuilder
from lattice.graph.serializer import serialize_graph
from lattice.models.analysis import FileAnalysis, ImportInfo


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def make_dynamic_import(module: str, line_number: int = 5, raw_expression: str = "importlib.import_module('x')") -> ImportInfo:
    return ImportInfo(
        module=module,
        import_type="dynamic",
        line_number=line_number,
        raw_expression=raw_expression,
    )


def build_graph(analyses: list[FileAnalysis]) -> nx.DiGraph:
    builder = DependencyGraphBuilder()
    return builder.build(analyses, Path("/project"))


class TestSerializeGraph:
    def test_output_has_three_top_level_sections(self) -> None:
        graph = build_graph([])
        result = serialize_graph(graph, [])
        assert "metadata" in result
        assert "nodes" in result
        assert "edges" in result

    def test_metadata_has_required_keys(self) -> None:
        graph = build_graph([])
        result = serialize_graph(graph, [])
        metadata = result["metadata"]
        assert "analyzed_at" in metadata
        assert "file_count" in metadata
        assert "languages" in metadata
        assert "blind_spots" in metadata

    def test_metadata_analyzed_at_is_iso_string(self) -> None:
        graph = build_graph([])
        result = serialize_graph(graph, [])
        analyzed_at = result["metadata"]["analyzed_at"]
        assert isinstance(analyzed_at, str)
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(analyzed_at)
        assert parsed.tzinfo is not None  # timezone-aware

    def test_metadata_file_count(self) -> None:
        analyses = [make_analysis("a.py"), make_analysis("b.py")]
        graph = build_graph(analyses)
        result = serialize_graph(graph, analyses)
        assert result["metadata"]["file_count"] == 2

    def test_metadata_languages_count(self) -> None:
        analyses = [
            make_analysis("a.py", language="python"),
            make_analysis("b.py", language="python"),
            make_analysis("c.ts", language="typescript"),
        ]
        graph = build_graph(analyses)
        result = serialize_graph(graph, analyses)
        languages = result["metadata"]["languages"]
        assert languages["python"] == 2
        assert languages["typescript"] == 1

    def test_metadata_blind_spots_from_dynamic_imports(self) -> None:
        analysis = make_analysis(
            "src/main.py",
            imports=[
                make_dynamic_import("some_module", line_number=10, raw_expression="importlib.import_module('some_module')"),
            ],
        )
        graph = build_graph([analysis])
        result = serialize_graph(graph, [analysis])

        blind_spots = result["metadata"]["blind_spots"]
        assert len(blind_spots) == 1
        assert blind_spots[0]["file"] == "src/main.py"
        assert blind_spots[0]["line"] == 10
        assert blind_spots[0]["expression"] == "importlib.import_module('some_module')"

    def test_metadata_blind_spots_empty_when_no_dynamic(self) -> None:
        analysis = make_analysis("src/utils.py")
        graph = build_graph([analysis])
        result = serialize_graph(graph, [analysis])
        assert result["metadata"]["blind_spots"] == []

    def test_nodes_list_contains_all_graph_nodes(self) -> None:
        analyses = [make_analysis("a.py"), make_analysis("b.py")]
        graph = build_graph(analyses)
        result = serialize_graph(graph, analyses)
        node_ids = [n["id"] for n in result["nodes"]]
        assert "a.py" in node_ids
        assert "b.py" in node_ids

    def test_node_has_required_fields(self) -> None:
        analysis = make_analysis("src/main.py", language="python", exports=["run"])
        graph = build_graph([analysis])
        result = serialize_graph(graph, [analysis])
        node = result["nodes"][0]
        assert node["id"] == "src/main.py"
        assert node["language"] == "python"
        assert "is_entry_point" in node
        assert "entry_point_type" in node
        assert "entry_details" in node
        assert node["exports"] == ["run"]

    def test_edges_list_contains_internal_imports(self) -> None:
        utils = make_analysis("src/utils.py")
        main = make_analysis(
            "src/main.py",
            imports=[
                ImportInfo(
                    module="src.utils",
                    import_type="standard",
                    resolved_path="src/utils.py",
                    is_external=False,
                    line_number=1,
                )
            ],
        )
        graph = build_graph([utils, main])
        result = serialize_graph(graph, [utils, main])
        assert len(result["edges"]) == 1
        edge = result["edges"][0]
        assert edge["source"] == "src/main.py"
        assert edge["target"] == "src/utils.py"
        assert edge["import_type"] == "standard"

    def test_empty_graph_produces_empty_nodes_and_edges(self) -> None:
        graph = build_graph([])
        result = serialize_graph(graph, [])
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_multiple_dynamic_imports_all_in_blind_spots(self) -> None:
        analysis = make_analysis(
            "src/loader.py",
            imports=[
                make_dynamic_import("mod_a", line_number=5, raw_expression="importlib.import_module('mod_a')"),
                make_dynamic_import("mod_b", line_number=10, raw_expression="__import__('mod_b')"),
            ],
        )
        graph = build_graph([analysis])
        result = serialize_graph(graph, [analysis])
        assert len(result["metadata"]["blind_spots"]) == 2
