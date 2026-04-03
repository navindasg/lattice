"""Orchestrator module public API.

Re-exports data models, CircuitBreaker, ProcessManager for CC instance lifecycle management,
NDJSON protocol helpers, TaskQueue for priority task routing, SoulFile models,
ContextManager for per-instance utilization tracking, and MCP connector types.
"""
from lattice.orchestrator.breaker import CircuitBreaker
from lattice.orchestrator.connectors import (
    BaseConnector,
    ConnectorConfig,
    ConnectorError,
    ConnectorPermissions,
    ConnectorRegistry,
    ConnectorResult,
    ConnectorState,
    GitHubConnector,
    MattermostConnector,
    TavilyConnector,
)
from lattice.orchestrator.manager import (
    ProcessManager,
    ProcessRegistry,
    build_child_env,
    is_process_alive,
    terminate_instance,
)
from lattice.orchestrator.runner import OrchestratorRunner
from lattice.orchestrator.models import (
    BreakerConfig,
    CircuitBreakerState,
    ContextManagerConfig,
    OrchestratorConfig,
    TaskRecord,
)
from lattice.orchestrator.protocol import (
    create_request_envelope,
    drain_stderr,
    parse_response_envelope,
    read_message,
    write_message,
)
from lattice.orchestrator.queue import PrioritizedTask, TaskQueue
from lattice.orchestrator.context import CompactionResult, ContextManager
from lattice.orchestrator.soul import (
    CurrentState,
    MemoryEntry,
    SoulFile,
    write_soul_atomically,
)
from lattice.orchestrator.status import get_connector_status
from lattice.orchestrator.voice import (
    IntentResult,
    IntentRouter,
    RouteResult,
    VoiceConfig,
    VoicePipeline,
)

__all__ = [
    "BreakerConfig",
    "CircuitBreakerState",
    "CircuitBreaker",
    "ContextManagerConfig",
    "OrchestratorConfig",
    "OrchestratorRunner",
    "TaskRecord",
    "ProcessManager",
    "ProcessRegistry",
    "build_child_env",
    "is_process_alive",
    "terminate_instance",
    "write_message",
    "read_message",
    "drain_stderr",
    "create_request_envelope",
    "parse_response_envelope",
    "PrioritizedTask",
    "TaskQueue",
    "SoulFile",
    "MemoryEntry",
    "CurrentState",
    "write_soul_atomically",
    "ContextManager",
    "CompactionResult",
    "IntentResult",
    "IntentRouter",
    "RouteResult",
    "VoiceConfig",
    "VoicePipeline",
    # MCP connectors
    "BaseConnector",
    "ConnectorConfig",
    "ConnectorError",
    "ConnectorPermissions",
    "ConnectorRegistry",
    "ConnectorResult",
    "ConnectorState",
    "TavilyConnector",
    "GitHubConnector",
    "MattermostConnector",
    "get_connector_status",
]
