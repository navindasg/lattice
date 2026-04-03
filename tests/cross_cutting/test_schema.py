"""Test stubs for cross_cutting schema models — RED phase (Wave 0).

Imports from lattice.cross_cutting.schema which does not yet exist.
These tests will fail with ImportError until Wave 1 implements the schema.
"""
import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from lattice.cross_cutting.schema import (
    ApiContract,
    CrossCuttingBlindSpot,
    EventFlow,
    PluginPoint,
    ProjectDoc,
    SharedState,
)


class TestEventFlow:
    def test_valid_event_flow_creates_successfully(self):
        flow = EventFlow(
            event_name="user.created",
            producer_module="events/emitter.py",
            consumer_module="events/handlers.py",
            pattern_type="event_emitter",
            producer_line=28,
        )
        assert flow.event_name == "user.created"
        assert flow.producer_module == "events/emitter.py"
        assert flow.consumer_module == "events/handlers.py"
        assert flow.producer_line == 28
        assert flow.consumer_line is None

    def test_event_flow_is_frozen(self):
        flow = EventFlow(
            event_name="user.created",
            producer_module="events/emitter.py",
            consumer_module="events/handlers.py",
            pattern_type="event_emitter",
            producer_line=1,
        )
        with pytest.raises((ValidationError, TypeError)):
            flow.event_name = "mutated"  # type: ignore[misc]


class TestSharedState:
    def test_valid_shared_state_creates_successfully(self):
        state = SharedState(
            object_name="APP_REGISTRY",
            owner_module="state/registry.py",
            pattern_type="global_registry",
        )
        assert state.object_name == "APP_REGISTRY"
        assert state.owner_module == "state/registry.py"

    def test_consumer_modules_defaults_to_empty_list(self):
        state = SharedState(
            object_name="APP_REGISTRY",
            owner_module="state/registry.py",
            pattern_type="global_registry",
        )
        assert state.consumer_modules == []


class TestApiContract:
    def test_valid_api_contract_creates_successfully(self):
        contract = ApiContract(
            method="GET",
            path="/health",
            handler_module="api/routes.py",
        )
        assert contract.method == "GET"
        assert contract.path == "/health"
        assert contract.handler_module == "api/routes.py"

    def test_framework_defaults_to_unknown(self):
        contract = ApiContract(
            method="POST",
            path="/users",
            handler_module="api/routes.py",
        )
        assert contract.framework == "unknown"


class TestPluginPoint:
    def test_valid_plugin_point_creates_successfully(self):
        point = PluginPoint(
            group="myapp.plugins",
            name="my_plugin",
            target_module="plugins/loader.py",
            pattern_type="importlib_metadata",
        )
        assert point.group == "myapp.plugins"
        assert point.name == "my_plugin"
        assert point.target_module == "plugins/loader.py"


class TestCrossCuttingBlindSpot:
    def test_valid_blind_spot_creates_successfully(self):
        spot = CrossCuttingBlindSpot(
            file="events/emitter.py",
            line=10,
            pattern_type="event_emitter",
            reason="dynamic event name",
        )
        assert spot.file == "events/emitter.py"
        assert spot.line == 10
        assert spot.reason == "dynamic event name"


class TestProjectDoc:
    def test_empty_lists_is_valid(self):
        from datetime import datetime, timezone
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )
        assert doc.event_flows == []
        assert doc.shared_state == []
        assert doc.api_contracts == []
        assert doc.plugin_points == []
        assert doc.blind_spots == []

    def test_round_trips_through_model_dump_validate(self):
        from datetime import datetime, timezone
        flow = EventFlow(
            event_name="user.created",
            producer_module="events/emitter.py",
            consumer_module="events/handlers.py",
            pattern_type="event_emitter",
            producer_line=28,
        )
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            event_flows=[flow],
        )
        dumped = doc.model_dump()
        restored = ProjectDoc.model_validate(dumped)
        assert restored.event_flows[0].event_name == "user.created"

    def test_rejects_invalid_literal_values(self):
        with pytest.raises(ValidationError):
            EventFlow(
                event_name="test",
                producer_module="x.py",
                consumer_module="y.py",
                pattern_type="invalid_pattern",  # type: ignore[arg-type]
                producer_line=1,
            )
        with pytest.raises(ValidationError):
            SharedState(
                object_name="X",
                owner_module="x.py",
                pattern_type="invalid_pattern",  # type: ignore[arg-type]
            )

    def test_all_models_are_frozen(self):
        from datetime import datetime, timezone
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises((ValidationError, TypeError)):
            doc.analyzed_at = "mutated"  # type: ignore[misc]
