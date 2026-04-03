"""Tests for EntryPointDetector.

Tests that entry point detector correctly annotates graph nodes with
is_entry_point, entry_point_type, and entry_details from:
- has_main_guard node attribute (set by builder from FileAnalysis.has_main_guard)
- decorator-based entry points via FileAnalysis.imports with import_type="decorator"
"""
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pytest

from lattice.graph.builder import DependencyGraphBuilder
from lattice.graph.entry_points import EntryPointDetector
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


def make_import(
    module: str,
    import_type: str = "decorator",
    line_number: int = 5,
    raw_expression: str | None = None,
) -> ImportInfo:
    return ImportInfo(
        module=module,
        import_type=import_type,
        line_number=line_number,
        raw_expression=raw_expression,
    )


def build_graph(analyses: list[FileAnalysis]) -> nx.DiGraph:
    builder = DependencyGraphBuilder()
    return builder.build(analyses, Path("/project"))


class TestEntryPointDetector:
    def setup_method(self) -> None:
        self.detector = EntryPointDetector()

    def test_main_guard_detected_from_node_attribute(self) -> None:
        """EntryPointDetector reads has_main_guard from graph node, not FileAnalysis."""
        analysis = make_analysis("src/main.py", has_main_guard=True)
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/main.py"]
        assert node["is_entry_point"] is True
        assert node["entry_point_type"] == "main"

    def test_no_main_guard_not_detected(self) -> None:
        analysis = make_analysis("src/utils.py", has_main_guard=False)
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/utils.py"]
        assert node["is_entry_point"] is False

    def test_route_decorator_detected(self) -> None:
        analysis = make_analysis(
            "src/routes.py",
            imports=[make_import(module="app.route", import_type="decorator", line_number=10)],
        )
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/routes.py"]
        assert node["is_entry_point"] is True
        assert node["entry_point_type"] == "route"

    def test_get_decorator_detected_as_route(self) -> None:
        analysis = make_analysis(
            "src/api.py",
            imports=[make_import(module="router.get", import_type="decorator", line_number=5)],
        )
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/api.py"]
        assert node["is_entry_point"] is True
        assert node["entry_point_type"] == "route"

    def test_click_command_detected_as_cli(self) -> None:
        analysis = make_analysis(
            "src/cli.py",
            imports=[make_import(module="click.command", import_type="decorator", line_number=8)],
        )
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/cli.py"]
        assert node["is_entry_point"] is True
        assert node["entry_point_type"] == "cli"

    def test_click_group_detected_as_cli(self) -> None:
        analysis = make_analysis(
            "src/cli.py",
            imports=[make_import(module="click.group", import_type="decorator", line_number=3)],
        )
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/cli.py"]
        assert node["is_entry_point"] is True
        assert node["entry_point_type"] == "cli"

    def test_celery_task_detected_as_event_listener(self) -> None:
        analysis = make_analysis(
            "src/tasks.py",
            imports=[make_import(module="celery.task", import_type="decorator", line_number=12)],
        )
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/tasks.py"]
        assert node["is_entry_point"] is True
        assert node["entry_point_type"] == "event_listener"

    def test_non_decorator_imports_not_detected(self) -> None:
        """Standard imports should not trigger entry point detection."""
        analysis = make_analysis(
            "src/utils.py",
            imports=[
                ImportInfo(
                    module="os",
                    import_type="standard",
                    is_external=True,
                    line_number=1,
                )
            ],
        )
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/utils.py"]
        assert node["is_entry_point"] is False

    def test_multiple_decorators_first_wins(self) -> None:
        """If a file has both route and cli decorators, the first detected wins."""
        analysis = make_analysis(
            "src/mixed.py",
            imports=[
                make_import(module="app.route", import_type="decorator", line_number=5),
                make_import(module="click.command", import_type="decorator", line_number=20),
            ],
        )
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/mixed.py"]
        assert node["is_entry_point"] is True
        # route comes first by line number
        assert node["entry_point_type"] == "route"

    def test_main_guard_takes_precedence_over_decorator(self) -> None:
        """has_main_guard=True sets entry_point_type='main' regardless of decorators."""
        analysis = make_analysis(
            "src/main.py",
            has_main_guard=True,
            imports=[
                make_import(module="app.route", import_type="decorator", line_number=10)
            ],
        )
        graph = build_graph([analysis])
        self.detector.detect(graph, [analysis])

        node = graph.nodes["src/main.py"]
        assert node["is_entry_point"] is True
        assert node["entry_point_type"] == "main"

    def test_unrelated_file_unchanged(self) -> None:
        main = make_analysis("src/main.py", has_main_guard=True)
        utils = make_analysis("src/utils.py")
        graph = build_graph([main, utils])
        self.detector.detect(graph, [main, utils])

        assert graph.nodes["src/utils.py"]["is_entry_point"] is False

    def test_detect_uses_sample_python_fixture(self, tmp_path: Path) -> None:
        """Integration check: main.py fixture has main guard, routes.py has route."""
        main_analysis = make_analysis("main.py", has_main_guard=True)
        routes_analysis = make_analysis(
            "routes.py",
            imports=[make_import(module="app.route", import_type="decorator", line_number=10)],
        )
        graph = build_graph([main_analysis, routes_analysis])
        self.detector.detect(graph, [main_analysis, routes_analysis])

        assert graph.nodes["main.py"]["entry_point_type"] == "main"
        assert graph.nodes["routes.py"]["entry_point_type"] == "route"
