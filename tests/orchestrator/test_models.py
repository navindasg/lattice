"""Tests for orchestrator data models.

Covers:
- ManagedInstance extension: created_at, last_heartbeat, error_reason fields
- TaskRecord: frozen Pydantic model with priority/status validation
- CircuitBreakerState: frozen model with trip_reason Literal
- BreakerConfig: defaults for iteration_cap, wall_clock_timeout_seconds, consecutive_error_limit
- OrchestratorConfig: defaults and nested BreakerConfig
"""
import pytest
from pydantic import ValidationError

from lattice.models.orchestrator import ManagedInstance
from lattice.orchestrator.models import (
    BreakerConfig,
    CircuitBreakerState,
    OrchestratorConfig,
    TaskRecord,
)


# ---------------------------------------------------------------------------
# ManagedInstance extension
# ---------------------------------------------------------------------------


class TestManagedInstanceExtension:
    def test_created_at_can_be_set_as_string(self):
        instance = ManagedInstance(created_at="2026-01-01T00:00:00+00:00")
        assert instance.created_at == "2026-01-01T00:00:00+00:00"

    def test_created_at_has_default(self):
        instance = ManagedInstance()
        assert instance.created_at is not None
        assert isinstance(instance.created_at, str)
        assert len(instance.created_at) > 0

    def test_last_heartbeat_defaults_to_none(self):
        instance = ManagedInstance()
        assert instance.last_heartbeat is None

    def test_error_reason_defaults_to_none(self):
        instance = ManagedInstance()
        assert instance.error_reason is None

    def test_model_copy_update_last_heartbeat(self):
        instance = ManagedInstance()
        updated = instance.model_copy(update={"last_heartbeat": "2026-01-01T00:00:00+00:00"})
        assert updated.last_heartbeat == "2026-01-01T00:00:00+00:00"
        assert updated is not instance

    def test_existing_status_validation_still_passes(self):
        for status in ("idle", "running", "stopped", "crashed"):
            instance = ManagedInstance(status=status)
            assert instance.status == status

    def test_invalid_status_still_raises(self):
        with pytest.raises(ValidationError):
            ManagedInstance(status="active")

    def test_frozen_still_enforced(self):
        instance = ManagedInstance()
        with pytest.raises(Exception):
            instance.status = "running"

    def test_uuid_generation_still_works(self):
        import uuid
        instance = ManagedInstance()
        parsed = uuid.UUID(instance.id)
        assert str(parsed) == instance.id


# ---------------------------------------------------------------------------
# TaskRecord
# ---------------------------------------------------------------------------


class TestTaskRecordValid:
    def test_creates_with_task_id_and_payload(self):
        record = TaskRecord(task_id="t1", payload='{"cmd":"test"}')
        assert record.task_id == "t1"
        assert record.payload == '{"cmd":"test"}'

    def test_default_priority_is_normal(self):
        record = TaskRecord(payload='{"cmd":"test"}')
        assert record.priority == "normal"

    def test_default_status_is_pending(self):
        record = TaskRecord(payload='{"cmd":"test"}')
        assert record.status == "pending"

    def test_priority_high_validates(self):
        record = TaskRecord(payload='{}', priority="high")
        assert record.priority == "high"

    def test_priority_low_validates(self):
        record = TaskRecord(payload='{}', priority="low")
        assert record.priority == "low"

    def test_status_completed_validates(self):
        record = TaskRecord(payload='{}', status="completed")
        assert record.status == "completed"

    def test_created_at_auto_generated(self):
        record = TaskRecord(payload='{}')
        assert record.created_at is not None
        assert isinstance(record.created_at, str)
        assert len(record.created_at) > 0

    def test_assigned_at_defaults_to_none(self):
        record = TaskRecord(payload='{}')
        assert record.assigned_at is None

    def test_completed_at_defaults_to_none(self):
        record = TaskRecord(payload='{}')
        assert record.completed_at is None

    def test_error_defaults_to_none(self):
        record = TaskRecord(payload='{}')
        assert record.error is None

    def test_instance_id_defaults_to_none(self):
        record = TaskRecord(payload='{}')
        assert record.instance_id is None


class TestTaskRecordInvalid:
    def test_priority_urgent_raises(self):
        with pytest.raises(ValidationError):
            TaskRecord(payload='{}', priority="urgent")

    def test_status_active_raises(self):
        with pytest.raises(ValidationError):
            TaskRecord(payload='{}', status="active")


class TestTaskRecordFrozen:
    def test_is_frozen(self):
        record = TaskRecord(payload='{}')
        with pytest.raises(Exception):
            record.status = "completed"

    def test_frozen_config_set(self):
        assert TaskRecord.model_config.get("frozen") is True


# ---------------------------------------------------------------------------
# CircuitBreakerState
# ---------------------------------------------------------------------------


class TestCircuitBreakerStateValid:
    def test_creates_with_instance_id(self):
        state = CircuitBreakerState(instance_id="i1")
        assert state.instance_id == "i1"

    def test_iteration_count_defaults_to_zero(self):
        state = CircuitBreakerState(instance_id="i1")
        assert state.iteration_count == 0

    def test_consecutive_errors_defaults_to_zero(self):
        state = CircuitBreakerState(instance_id="i1")
        assert state.consecutive_errors == 0

    def test_tripped_defaults_to_false(self):
        state = CircuitBreakerState(instance_id="i1")
        assert state.tripped is False

    def test_trip_reason_defaults_to_none(self):
        state = CircuitBreakerState(instance_id="i1")
        assert state.trip_reason is None

    def test_trip_reason_iteration_cap_validates(self):
        state = CircuitBreakerState(instance_id="i1", trip_reason="iteration_cap")
        assert state.trip_reason == "iteration_cap"

    def test_trip_reason_wall_clock_validates(self):
        state = CircuitBreakerState(instance_id="i1", trip_reason="wall_clock")
        assert state.trip_reason == "wall_clock"

    def test_trip_reason_error_limit_validates(self):
        state = CircuitBreakerState(instance_id="i1", trip_reason="error_limit")
        assert state.trip_reason == "error_limit"


class TestCircuitBreakerStateInvalid:
    def test_invalid_trip_reason_raises(self):
        with pytest.raises(ValidationError):
            CircuitBreakerState(instance_id="i1", trip_reason="timeout")


class TestCircuitBreakerStateFrozen:
    def test_is_frozen(self):
        state = CircuitBreakerState(instance_id="i1")
        with pytest.raises(Exception):
            state.tripped = True

    def test_frozen_config_set(self):
        assert CircuitBreakerState.model_config.get("frozen") is True


# ---------------------------------------------------------------------------
# BreakerConfig
# ---------------------------------------------------------------------------


class TestBreakerConfigDefaults:
    def test_iteration_cap_default(self):
        config = BreakerConfig()
        assert config.iteration_cap == 50

    def test_wall_clock_timeout_seconds_default(self):
        config = BreakerConfig()
        assert config.wall_clock_timeout_seconds == 1800

    def test_consecutive_error_limit_default(self):
        config = BreakerConfig()
        assert config.consecutive_error_limit == 3

    def test_iteration_cap_override(self):
        config = BreakerConfig(iteration_cap=100)
        assert config.iteration_cap == 100


# ---------------------------------------------------------------------------
# OrchestratorConfig
# ---------------------------------------------------------------------------


class TestOrchestratorConfigDefaults:
    def test_max_instances_default(self):
        config = OrchestratorConfig()
        assert config.max_instances == 3

    def test_idle_timeout_seconds_default(self):
        config = OrchestratorConfig()
        assert config.idle_timeout_seconds == 60

    def test_max_queue_depth_default(self):
        config = OrchestratorConfig()
        assert config.max_queue_depth == 20

    def test_breaker_is_breaker_config(self):
        config = OrchestratorConfig()
        assert isinstance(config.breaker, BreakerConfig)

    def test_breaker_has_defaults(self):
        config = OrchestratorConfig()
        assert config.breaker.iteration_cap == 50
        assert config.breaker.wall_clock_timeout_seconds == 1800
        assert config.breaker.consecutive_error_limit == 3

    def test_max_instances_override(self):
        config = OrchestratorConfig(max_instances=5)
        assert config.max_instances == 5
