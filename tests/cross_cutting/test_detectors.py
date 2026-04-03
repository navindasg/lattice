"""Test stubs for cross_cutting detector classes — RED phase (Wave 0).

Imports from lattice.cross_cutting.detectors which does not yet exist.
These tests will fail with ImportError until Wave 1 implements the detectors.
"""
import ast
from pathlib import Path

import pytest

from lattice.cross_cutting.detectors import (
    ApiContractDetector,
    EventFlowDetector,
    PluginPointDetector,
    SharedStateDetector,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sample_cross_cutting"


class TestEventFlowDetector:
    def test_finds_producer_in_emitter_fixture(self):
        path = FIXTURES_DIR / "events" / "emitter.py"
        tree = ast.parse(path.read_text())
        detector = EventFlowDetector()
        result = detector.detect(tree, "events/emitter.py")
        producer_names = [name for name, _line in result.producers]
        assert "user.created" in producer_names

    def test_finds_consumer_in_handlers_fixture(self):
        path = FIXTURES_DIR / "events" / "handlers.py"
        tree = ast.parse(path.read_text())
        detector = EventFlowDetector()
        result = detector.detect(tree, "events/handlers.py")
        consumer_names = [name for name, _line in result.consumers]
        assert "user.created" in consumer_names
        assert "user.deleted" in consumer_names

    def test_dynamic_event_name_produces_blind_spot(self):
        source = """
bus = EventBus()
event_type = get_event()
bus.emit(event_type, data)
"""
        tree = ast.parse(source)
        detector = EventFlowDetector()
        result = detector.detect(tree, "dynamic_events.py")
        assert len(result.blind_spots) > 0
        assert result.blind_spots[0].reason == "dynamic event name"


class TestSharedStateDetector:
    def test_finds_global_registry(self):
        path = FIXTURES_DIR / "state" / "registry.py"
        tree = ast.parse(path.read_text())
        detector = SharedStateDetector()
        results = detector.detect(tree, "state/registry.py")
        names = [r.object_name for r in results]
        assert "APP_REGISTRY" in names
        registry = next(r for r in results if r.object_name == "APP_REGISTRY")
        assert registry.pattern_type in ("global_registry", "module_global")

    def test_ignores_function_local_assignments(self):
        source = """
def process():
    local_cache = {}
    local_registry = {}
    return local_cache
"""
        tree = ast.parse(source)
        detector = SharedStateDetector()
        results = detector.detect(tree, "local_vars.py")
        # Function-local assignments should not be detected as shared state
        assert len(results) == 0


class TestApiContractDetector:
    def test_finds_flask_routes(self):
        path = FIXTURES_DIR / "api" / "routes.py"
        tree = ast.parse(path.read_text())
        detector = ApiContractDetector()
        results = detector.detect(tree, "api/routes.py")
        paths = [r.path for r in results]
        assert "/health" in paths
        assert "/users" in paths

    def test_detects_flask_framework(self):
        path = FIXTURES_DIR / "api" / "routes.py"
        tree = ast.parse(path.read_text())
        detector = ApiContractDetector()
        results = detector.detect(tree, "api/routes.py")
        assert len(results) > 0
        assert all(r.framework == "flask" for r in results)


class TestPluginPointDetector:
    def test_finds_importlib_metadata_pattern(self):
        path = FIXTURES_DIR / "plugins" / "loader.py"
        tree = ast.parse(path.read_text())
        detector = PluginPointDetector()
        results = detector.detect(tree, "plugins/loader.py")
        assert len(results) > 0
        assert results[0].pattern_type == "importlib_metadata"
        assert results[0].group == "myapp.plugins"
