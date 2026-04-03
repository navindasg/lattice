"""Unit tests for CircuitBreaker and LatticeSettings orchestrator extension.

Tests verify three independent triggers:
- Iteration cap: trips when iteration_count >= iteration_cap
- Wall-clock timeout: trips when elapsed >= wall_clock_timeout_seconds
- Consecutive error limit: trips when consecutive_errors >= consecutive_error_limit

Also tests LatticeSettings extension with orchestrator: OrchestratorConfig field.
"""
import time

import pytest

from lattice.orchestrator.breaker import CircuitBreaker
from lattice.orchestrator.models import BreakerConfig, OrchestratorConfig


class TestCircuitBreakerIterationCap:
    """Tests for the iteration cap trigger."""

    def test_below_cap_not_tripped(self) -> None:
        """4 iterations with cap=5 should not trip the breaker."""
        breaker = CircuitBreaker(instance_id="test-1", config=BreakerConfig(iteration_cap=5))
        for _ in range(4):
            breaker.record_iteration()
        assert breaker.state.tripped is False

    def test_at_cap_trips_breaker(self) -> None:
        """5 iterations with cap=5 should trip the breaker."""
        breaker = CircuitBreaker(instance_id="test-2", config=BreakerConfig(iteration_cap=5))
        for _ in range(5):
            breaker.record_iteration()
        assert breaker.state.tripped is True
        assert breaker.state.trip_reason == "iteration_cap"

    def test_iteration_count_tracked(self) -> None:
        """Iteration count should match the number of record_iteration calls."""
        breaker = CircuitBreaker(instance_id="test-3", config=BreakerConfig(iteration_cap=10))
        for _ in range(3):
            breaker.record_iteration()
        assert breaker.state.iteration_count == 3


class TestCircuitBreakerErrorLimit:
    """Tests for the consecutive error limit trigger."""

    def test_three_errors_trips_breaker(self) -> None:
        """3 consecutive errors with limit=3 should trip the breaker."""
        breaker = CircuitBreaker(
            instance_id="test-4", config=BreakerConfig(consecutive_error_limit=3)
        )
        for _ in range(3):
            breaker.record_error()
        assert breaker.state.tripped is True
        assert breaker.state.trip_reason == "error_limit"

    def test_success_resets_consecutive_errors(self) -> None:
        """2 errors then 1 success should reset consecutive_errors to 0."""
        breaker = CircuitBreaker(
            instance_id="test-5", config=BreakerConfig(consecutive_error_limit=3)
        )
        breaker.record_error()
        breaker.record_error()
        breaker.record_success()
        assert breaker.state.consecutive_errors == 0
        assert breaker.state.tripped is False

    def test_two_errors_not_tripped(self) -> None:
        """2 consecutive errors with limit=3 should not trip."""
        breaker = CircuitBreaker(
            instance_id="test-6", config=BreakerConfig(consecutive_error_limit=3)
        )
        breaker.record_error()
        breaker.record_error()
        assert breaker.state.tripped is False


class TestCircuitBreakerWallClock:
    """Tests for the wall-clock timeout trigger."""

    def test_timeout_exceeded_trips_breaker(self) -> None:
        """After 1.1s with 1s timeout, check_wall_clock should trip the breaker."""
        breaker = CircuitBreaker(
            instance_id="test-7", config=BreakerConfig(wall_clock_timeout_seconds=1)
        )
        time.sleep(1.1)
        state = breaker.check_wall_clock()
        assert state.tripped is True
        assert state.trip_reason == "wall_clock"

    def test_within_timeout_not_tripped(self) -> None:
        """Immediately after creation with 60s timeout, should not be tripped."""
        breaker = CircuitBreaker(
            instance_id="test-8", config=BreakerConfig(wall_clock_timeout_seconds=60)
        )
        state = breaker.check_wall_clock()
        assert state.tripped is False


class TestCircuitBreakerReset:
    """Tests for the reset() method."""

    def test_reset_clears_all_state(self) -> None:
        """reset() should clear iteration_count, consecutive_errors, tripped, and trip_reason."""
        breaker = CircuitBreaker(instance_id="test-9", config=BreakerConfig(iteration_cap=3))
        for _ in range(3):
            breaker.record_iteration()
        assert breaker.state.tripped is True

        breaker.reset()
        assert breaker.state.iteration_count == 0
        assert breaker.state.consecutive_errors == 0
        assert breaker.state.tripped is False
        assert breaker.state.trip_reason is None


class TestCircuitBreakerProperties:
    """Tests for is_tripped property."""

    def test_is_tripped_reflects_state(self) -> None:
        """is_tripped property should return state.tripped."""
        breaker = CircuitBreaker(instance_id="test-10", config=BreakerConfig(iteration_cap=2))
        assert breaker.is_tripped is False
        breaker.record_iteration()
        breaker.record_iteration()
        assert breaker.is_tripped is True

    def test_state_property_returns_current_state(self) -> None:
        """state property should return the current CircuitBreakerState."""
        breaker = CircuitBreaker(instance_id="test-11", config=BreakerConfig())
        state = breaker.state
        assert state.instance_id == "test-11"
        assert state.tripped is False


class TestLatticeSettingsOrchestrator:
    """Tests for LatticeSettings extension with orchestrator field."""

    def test_lattice_settings_has_orchestrator_field(self) -> None:
        """LatticeSettings() should have an orchestrator field of type OrchestratorConfig."""
        from lattice.llm.config import LatticeSettings

        settings = LatticeSettings()
        assert hasattr(settings, "orchestrator")
        assert isinstance(settings.orchestrator, OrchestratorConfig)

    def test_orchestrator_max_instances_default(self) -> None:
        """LatticeSettings().orchestrator.max_instances should default to 3."""
        from lattice.llm.config import LatticeSettings

        settings = LatticeSettings()
        assert settings.orchestrator.max_instances == 3

    def test_orchestrator_breaker_iteration_cap_default(self) -> None:
        """LatticeSettings().orchestrator.breaker.iteration_cap should default to 50."""
        from lattice.llm.config import LatticeSettings

        settings = LatticeSettings()
        assert settings.orchestrator.breaker.iteration_cap == 50
