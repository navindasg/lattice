"""Circuit breaker for CC instance runaway prevention.

Three independent triggers:
- Iteration cap: tool-call count from stdout messages (default 50)
- Wall-clock timeout: total task duration (default 30 minutes)
- Consecutive error count: sequential error responses (default 3)

All thresholds configurable via BreakerConfig in lattice.yaml orchestrator: section.
Breaker is stateful per-instance; uses frozen CircuitBreakerState with model_copy.
"""
from __future__ import annotations

import time

import structlog

from lattice.orchestrator.models import BreakerConfig, CircuitBreakerState

log = structlog.get_logger(__name__)


class CircuitBreaker:
    """Per-instance circuit breaker with three independent triggers.

    Args:
        instance_id: The managed instance this breaker protects.
        config: Breaker thresholds (iteration_cap, wall_clock_timeout_seconds, consecutive_error_limit).
    """

    def __init__(self, instance_id: str, config: BreakerConfig) -> None:
        self._config = config
        self._state = CircuitBreakerState(instance_id=instance_id)
        self._start_time = time.monotonic()

    @property
    def state(self) -> CircuitBreakerState:
        """Return the current immutable CircuitBreakerState."""
        return self._state

    @property
    def is_tripped(self) -> bool:
        """Return True if the breaker has been tripped."""
        return self._state.tripped

    def record_iteration(self) -> CircuitBreakerState:
        """Record one tool-call iteration. Trips breaker if iteration_cap reached."""
        new_count = self._state.iteration_count + 1
        if new_count >= self._config.iteration_cap:
            self._state = self._state.model_copy(update={
                "iteration_count": new_count,
                "tripped": True,
                "trip_reason": "iteration_cap",
            })
            log.warning(
                "breaker_tripped",
                instance_id=self._state.instance_id,
                reason="iteration_cap",
                count=new_count,
                cap=self._config.iteration_cap,
            )
        else:
            self._state = self._state.model_copy(update={"iteration_count": new_count})
        return self._state

    def record_error(self) -> CircuitBreakerState:
        """Record a consecutive error. Trips breaker if consecutive_error_limit reached."""
        new_errors = self._state.consecutive_errors + 1
        if new_errors >= self._config.consecutive_error_limit:
            self._state = self._state.model_copy(update={
                "consecutive_errors": new_errors,
                "tripped": True,
                "trip_reason": "error_limit",
            })
            log.warning(
                "breaker_tripped",
                instance_id=self._state.instance_id,
                reason="error_limit",
                errors=new_errors,
                limit=self._config.consecutive_error_limit,
            )
        else:
            self._state = self._state.model_copy(update={"consecutive_errors": new_errors})
        return self._state

    def record_success(self) -> CircuitBreakerState:
        """Record a successful response. Resets consecutive error count to 0."""
        self._state = self._state.model_copy(update={"consecutive_errors": 0})
        return self._state

    def check_wall_clock(self) -> CircuitBreakerState:
        """Check if wall-clock timeout has been exceeded."""
        elapsed = time.monotonic() - self._start_time
        if elapsed >= self._config.wall_clock_timeout_seconds:
            self._state = self._state.model_copy(update={
                "tripped": True,
                "trip_reason": "wall_clock",
            })
            log.warning(
                "breaker_tripped",
                instance_id=self._state.instance_id,
                reason="wall_clock",
                elapsed_s=round(elapsed, 1),
                timeout_s=self._config.wall_clock_timeout_seconds,
            )
        return self._state

    def reset(self) -> CircuitBreakerState:
        """Reset breaker to initial state. Restarts wall-clock timer."""
        self._state = CircuitBreakerState(instance_id=self._state.instance_id)
        self._start_time = time.monotonic()
        return self._state
