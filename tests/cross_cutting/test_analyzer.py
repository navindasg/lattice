"""Tests for CrossCuttingAnalyzer — GREEN phase (Plan 02).

Tests verify the full cross-file joining, SharedState consumer resolution,
API contract detection, and DirDoc extension.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lattice.cross_cutting.analyzer import (
    CrossCuttingAnalyzer,
    build_cross_cutting_edges,
    compute_cross_cutting_refs,
    enrich_dir_docs_if_present,
)
from lattice.cross_cutting.schema import ApiContract, EventFlow, ProjectDoc, SharedState

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sample_cross_cutting"


def _make_graph_data(
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> dict:
    """Build a minimal _graph.json-compatible dict for tests."""
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


class TestCrossCuttingAnalyzer:
    """Tests for CrossCuttingAnalyzer.analyze() with fixture files."""

    def _make_analyzer(self, tmp_path: Path) -> CrossCuttingAnalyzer:
        # Copy fixtures to tmp_path to keep analysis relative
        fixture_copy = tmp_path / "sample_cross_cutting"
        shutil.copytree(str(FIXTURES_DIR), str(fixture_copy))
        return CrossCuttingAnalyzer(fixture_copy), fixture_copy

    def test_analyze_produces_event_flow(self, tmp_path: Path) -> None:
        """Joined EventFlow: emitter.py (producer) -> handlers.py (consumer)."""
        analyzer, root = self._make_analyzer(tmp_path)
        source_files = [
            root / "events" / "emitter.py",
            root / "events" / "handlers.py",
        ]
        graph_data = _make_graph_data()
        doc = analyzer.analyze(graph_data, source_files)

        # Should have at least one EventFlow joining user.created
        assert len(doc.event_flows) > 0
        event_names = [f.event_name for f in doc.event_flows]
        assert "user.created" in event_names

    def test_event_flow_has_correct_producer_and_consumer(self, tmp_path: Path) -> None:
        """EventFlow should link emitter.py (producer) to handlers.py (consumer)."""
        analyzer, root = self._make_analyzer(tmp_path)
        source_files = [
            root / "events" / "emitter.py",
            root / "events" / "handlers.py",
        ]
        graph_data = _make_graph_data()
        doc = analyzer.analyze(graph_data, source_files)

        user_created = next(
            (f for f in doc.event_flows if f.event_name == "user.created"), None
        )
        assert user_created is not None
        assert "emitter.py" in user_created.producer_module
        assert "handlers.py" in user_created.consumer_module

    def test_analyze_produces_shared_state(self, tmp_path: Path) -> None:
        """APP_REGISTRY in registry.py should be detected as shared state."""
        analyzer, root = self._make_analyzer(tmp_path)
        source_files = [root / "state" / "registry.py"]
        graph_data = _make_graph_data()
        doc = analyzer.analyze(graph_data, source_files)

        assert len(doc.shared_state) > 0
        names = [s.object_name for s in doc.shared_state]
        assert "APP_REGISTRY" in names

    def test_shared_state_consumer_modules_populated(self, tmp_path: Path) -> None:
        """consumer_modules populated from graph_data import edges."""
        analyzer, root = self._make_analyzer(tmp_path)

        rel_registry = "state/registry.py"
        rel_consumers = "state/consumers.py"

        nodes = [
            {
                "id": rel_registry,
                "language": "python",
                "is_entry_point": False,
                "entry_point_type": None,
                "entry_details": None,
                "exports": [],
            },
            {
                "id": rel_consumers,
                "language": "python",
                "is_entry_point": False,
                "entry_point_type": None,
                "entry_details": None,
                "exports": [],
            },
        ]
        edges = [
            {
                "source": rel_registry,
                "target": rel_consumers,
                "import_type": "standard",
            }
        ]
        graph_data = _make_graph_data(nodes=nodes, edges=edges)

        source_files = [
            root / "state" / "registry.py",
            root / "state" / "consumers.py",
        ]
        doc = analyzer.analyze(graph_data, source_files)

        registry_state = next(
            (s for s in doc.shared_state if s.object_name == "APP_REGISTRY"), None
        )
        assert registry_state is not None
        assert len(registry_state.consumer_modules) > 0

    def test_analyze_produces_api_contract(self, tmp_path: Path) -> None:
        """GET /health should be detected as an API contract from routes.py."""
        analyzer, root = self._make_analyzer(tmp_path)
        source_files = [root / "api" / "routes.py"]
        graph_data = _make_graph_data()
        doc = analyzer.analyze(graph_data, source_files)

        assert len(doc.api_contracts) > 0
        paths = [c.path for c in doc.api_contracts]
        assert "/health" in paths

    def test_dynamic_event_produces_blind_spot(self, tmp_path: Path) -> None:
        """Dynamic event name (variable) should produce a blind spot."""
        # Write a fixture file with a dynamic event name
        dynamic_fixture = tmp_path / "dynamic_event.py"
        dynamic_fixture.write_text(
            "bus.emit(event_type, data)\n",
            encoding="utf-8",
        )
        analyzer = CrossCuttingAnalyzer(tmp_path)
        graph_data = _make_graph_data()
        doc = analyzer.analyze(graph_data, [dynamic_fixture])

        assert len(doc.blind_spots) > 0
        assert doc.blind_spots[0].pattern_type == "event_emitter"
        assert doc.blind_spots[0].reason == "dynamic event name"

    def test_empty_source_files_returns_valid_empty_doc(self, tmp_path: Path) -> None:
        """analyze() on empty list returns valid ProjectDoc with empty lists."""
        analyzer = CrossCuttingAnalyzer(tmp_path)
        graph_data = _make_graph_data()
        doc = analyzer.analyze(graph_data, [])

        assert doc.event_flows == []
        assert doc.api_contracts == []
        assert doc.shared_state == []
        assert doc.plugin_points == []
        assert doc.blind_spots == []

    def test_syntax_error_file_is_skipped_non_fatally(self, tmp_path: Path) -> None:
        """SyntaxError in a file should be skipped without crashing."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def broken(:\n    pass\n", encoding="utf-8")
        good_file = tmp_path / "good.py"
        good_file.write_text("bus.emit('x', 1)\n", encoding="utf-8")

        analyzer = CrossCuttingAnalyzer(tmp_path)
        graph_data = _make_graph_data()
        # Should not raise, and should still process good.py
        doc = analyzer.analyze(graph_data, [bad_file, good_file])
        # good.py should produce a producer event
        assert isinstance(doc, ProjectDoc)


class TestBuildCrossCuttingEdges:
    """Tests for build_cross_cutting_edges() helper."""

    def test_converts_event_flow_to_edge_dict(self) -> None:
        """EventFlow should produce an edge dict with type=event_flow."""
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            event_flows=[
                EventFlow(
                    event_name="user.created",
                    producer_module="emitter.py",
                    consumer_module="handlers.py",
                    pattern_type="event_emitter",
                    producer_line=1,
                )
            ],
        )
        edges = build_cross_cutting_edges(doc)

        assert len(edges) > 0
        assert edges[0]["type"] == "event_flow"
        assert edges[0]["source"] == "emitter.py"
        assert edges[0]["target"] == "handlers.py"
        assert edges[0]["label"] == "user.created"

    def test_converts_shared_state_to_edge_per_consumer(self) -> None:
        """SharedState with two consumers produces two edges."""
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            shared_state=[
                SharedState(
                    object_name="APP_REGISTRY",
                    owner_module="registry.py",
                    consumer_modules=["consumers.py", "services.py"],
                    pattern_type="global_registry",
                )
            ],
        )
        edges = build_cross_cutting_edges(doc)

        assert len(edges) == 2
        for edge in edges:
            assert edge["type"] == "shared_state"
            assert edge["source"] == "registry.py"
            assert edge["label"] == "APP_REGISTRY"

    def test_converts_project_doc_to_edge_dicts(self) -> None:
        """Full ProjectDoc with EventFlow produces non-empty edge list."""
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            event_flows=[
                EventFlow(
                    event_name="user.created",
                    producer_module="emitter.py",
                    consumer_module="handlers.py",
                    pattern_type="event_emitter",
                    producer_line=1,
                )
            ],
        )
        edges = build_cross_cutting_edges(doc)
        assert len(edges) > 0
        assert edges[0]["type"] == "event_flow"

    def test_empty_doc_returns_empty_list(self) -> None:
        """Empty ProjectDoc produces empty edge list."""
        doc = ProjectDoc(analyzed_at=datetime.now(timezone.utc).isoformat())
        edges = build_cross_cutting_edges(doc)
        assert edges == []


class TestComputeCrossCuttingRefs:
    """Tests for compute_cross_cutting_refs() helper."""

    def test_returns_refs_for_participating_directory(self) -> None:
        """Directory containing producer returns event:...:producer ref."""
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            event_flows=[
                EventFlow(
                    event_name="user.created",
                    producer_module="events/emitter.py",
                    consumer_module="events/handlers.py",
                    pattern_type="event_emitter",
                    producer_line=1,
                )
            ],
        )
        refs = compute_cross_cutting_refs(doc, "events")

        assert len(refs) > 0
        assert "event:user.created:producer" in refs
        assert "event:user.created:consumer" in refs

    def test_returns_empty_for_non_participating_directory(self) -> None:
        """Directory not in any pattern returns empty list."""
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            event_flows=[
                EventFlow(
                    event_name="user.created",
                    producer_module="events/emitter.py",
                    consumer_module="events/handlers.py",
                    pattern_type="event_emitter",
                    producer_line=1,
                )
            ],
        )
        refs = compute_cross_cutting_refs(doc, "unrelated")
        assert refs == []

    def test_returns_api_ref_for_api_directory(self) -> None:
        """Directory with API contract returns api:METHOD:path ref."""
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            api_contracts=[
                ApiContract(
                    method="GET",
                    path="/health",
                    handler_module="api/routes.py",
                    handler_function="health_check",
                    framework="flask",
                )
            ],
        )
        refs = compute_cross_cutting_refs(doc, "api")
        assert "api:GET:/health" in refs


class TestDirDocExtension:
    """Tests for DirDoc.cross_cutting_refs backward compatibility."""

    def test_dirdoc_accepts_cross_cutting_refs(self) -> None:
        """DirDoc can be created with cross_cutting_refs field."""
        from lattice.shadow.schema import DirDoc

        doc = DirDoc(
            directory="events",
            confidence=0.8,
            source="static",
            last_analyzed=datetime.now(timezone.utc),
            cross_cutting_refs=["event:user.created:producer"],
        )
        assert doc.cross_cutting_refs == ["event:user.created:producer"]

    def test_dirdoc_defaults_to_empty_list(self) -> None:
        """DirDoc without cross_cutting_refs defaults to empty list (backward compat)."""
        from lattice.shadow.schema import DirDoc

        doc = DirDoc(
            directory="events",
            confidence=0.8,
            source="static",
            last_analyzed=datetime.now(timezone.utc),
        )
        assert doc.cross_cutting_refs == []
