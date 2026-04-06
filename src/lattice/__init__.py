"""Lattice — codebase intelligence engine and multi-session orchestrator.

Public API surface: import shared types and utilities from here so downstream
packages can use `from lattice import FileAnalysis` without knowing internal
module paths.

Available exports:
    FileAnalysis            — structured result of parsing a single source file
    GraphNode               — a node in the codebase dependency graph
    ImportInfo              — structured representation of a single import statement
    MappingSession          — a codebase mapping run with lifecycle state
    ManagedInstance         — a managed Claude Code worker process
    MapperCommand           — a typed command sent to a mapping session
    LanguageAdapter         — ABC for all AST parser implementations
    TypeScriptAdapter       — TypeScript/JavaScript adapter via ts-morph subprocess bridge
    configure_logging       — structlog configuration helper
    DependencyGraphBuilder  — builds NetworkX DiGraph from FileAnalysis results
    EntryPointDetector      — annotates graph nodes with entry point metadata
    ConfigWiringDetector    — adds config file nodes and config_ref edges
    serialize_graph         — serializes graph to _graph.json-compatible dict
    TestFile                — discovered and classified test file model
    TestType                — Literal["unit", "integration", "e2e"]
    GapEntry                — untested integration seam ranked by centrality
    TestEdgeMapping         — per-test mapping of covered dependency edges
    TestCoverage            — complete test coverage report model
    TestDiscovery           — discovers pytest and jest test files
    TestClassifier          — classifies test files by type
    CoverageBuilder         — computes transitive edge coverage and gap reports
    DirDoc                  — _dir.md shadow tree document model
    ConfidenceSource        — Literal type for confidence source values
    GapSummary              — coverage gap summary nested in DirDoc
    StaticAnalysisLimits    — static analysis limitation flags nested in DirDoc
    parse_dir_doc           — parse a _dir.md file into a DirDoc
    traverse                — collect and sort all _dir.md files under agent_docs_root
    write_dir_doc           — write a DirDoc to its shadow path
    is_stale                — check if a directory has git commits newer than last_analyzed
    last_git_commit_time    — get UTC datetime of last git commit touching a directory
    FleetDispatcher         — parallel wave dispatcher for fleet agents
    FleetCheckpoint         — DuckDB wave progress and token tracking
    WavePlan                — complete fleet execution plan with wave ordering
    DocumentAssembler       — validates AgentResult and writes DirDoc to shadow tree
    SkeletonWriter          — writes test stubs to _test_stubs/ shadow path
    CrossCuttingAnalyzer    — orchestrates cross-cutting analysis across project
    ProjectDoc              — top-level container for cross-cutting analysis results
    EventFlow               — producer-consumer event relationship
    SharedState             — module-level global object shared across modules
    ApiContract             — HTTP route declaration extracted from decorators
    PluginPoint             — plugin/extension point using setuptools or importlib.metadata
    CrossCuttingBlindSpot   — cross-cutting pattern that could not be statically resolved
    write_project_doc       — write a ProjectDoc to _project.md
    parse_project_doc       — parse a _project.md file into a ProjectDoc
    CommandRequest          — typed command request Pydantic model
    CommandResponse         — typed command response Pydantic model
    MapperError             — structured error Pydantic model
    mapper_app              — FastAPI application instance for the Mapper HTTP API
    TaskRecord              — unit of work to be dispatched to a managed CC instance
    CircuitBreakerState     — per-instance circuit breaker state (frozen Pydantic model)
    BreakerConfig           — circuit breaker threshold configuration
    OrchestratorConfig      — fleet-wide orchestrator settings
    CircuitBreaker          — per-instance circuit breaker with three independent triggers
    ProcessManager          — CC instance lifecycle management (spawn/terminate/orphan)
    ProcessRegistry         — DuckDB-backed process instance registry
    TaskQueue               — priority task queue with DuckDB backing
    PrioritizedTask         — wrapper for TaskRecord with priority and sequence ordering
    write_message           — write a JSON message to stdin as NDJSON
    read_message            — read one NDJSON line from stdout
    drain_stderr            — background task: consume stderr so buffer does not block
    create_request_envelope — wrap task payload in request envelope with request_id
    parse_response_envelope — parse response envelope into structured result
    ConnectorRegistry       — runtime MCP connector registry with DuckDB persistence
    ConnectorConfig         — per-connector configuration model
    ConnectorResult         — fetch/write operation result model
    ConnectorState          — connector runtime state model
    ConnectorPermissions    — read/write permission model
    BaseConnector           — abstract base class for all connectors
    ConnectorError          — connector-level exception
    TavilyConnector         — web search connector via Tavily API
    GitHubConnector         — GitHub issues/PRs/CI connector
    MattermostConnector     — Mattermost channel monitoring connector
    get_connector_status    — query connector registry status from DuckDB
    ProjectConfig           — per-project configuration loaded from .lattice/config.yaml
    MapperProjectConfig     — per-project mapper configuration
    ConnectorProjectConfig  — per-project connector permissions and scope
    ModelProfileConfig      — per-project model tier selection
    OrchestratorProjectConfig — per-project orchestrator overrides
"""
from lattice.llm.config import (
    ProjectConfig,
    MapperProjectConfig,
    ConnectorProjectConfig,
    ModelProfileConfig,
    OrchestratorProjectConfig,
)
from lattice.api.app import app as mapper_app
from lattice.api.models import CommandRequest, CommandResponse, MapperError
from lattice.api.stdio import run_stdio_server
from lattice.adapters.base import LanguageAdapter
from lattice.orchestrator import (
    CircuitBreaker,
    PrioritizedTask,
    TaskQueue,
)
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
from lattice.orchestrator.status import get_connector_status
from lattice.orchestrator.models import (
    BreakerConfig,
    CircuitBreakerState,
    OrchestratorConfig,
    TaskRecord,
)
from lattice.orchestrator.manager import ProcessManager, ProcessRegistry
from lattice.orchestrator.protocol import (
    create_request_envelope,
    drain_stderr,
    parse_response_envelope,
    read_message,
    write_message,
)
from lattice.cross_cutting import (
    ApiContract,
    CrossCuttingAnalyzer,
    CrossCuttingBlindSpot,
    EventFlow,
    PluginPoint,
    ProjectDoc,
    SharedState,
    parse_project_doc,
    write_project_doc,
)
from lattice.fleet.assembler import DocumentAssembler
from lattice.fleet.checkpoint import FleetCheckpoint
from lattice.fleet.dispatcher import FleetDispatcher
from lattice.fleet.models import WavePlan
from lattice.fleet.skeleton import SkeletonWriter
from lattice.shadow import (
    ConfidenceSource,
    DirDoc,
    GapSummary,
    StaticAnalysisLimits,
    is_stale,
    last_git_commit_time,
    parse_dir_doc,
    traverse,
    write_dir_doc,
)
from lattice.adapters.python_adapter import PythonAdapter
from lattice.adapters.typescript_adapter import TypeScriptAdapter
from lattice.graph.builder import DependencyGraphBuilder
from lattice.graph.config_wiring import ConfigWiringDetector
from lattice.graph.entry_points import EntryPointDetector
from lattice.graph.serializer import serialize_graph
from lattice.logging import configure_logging
from lattice.models.analysis import FileAnalysis, GraphNode, ImportInfo
from lattice.models.coverage import (
    GapEntry,
    TestCoverage,
    TestEdgeMapping,
    TestFile,
    TestType,
)
from lattice.models.orchestrator import ManagedInstance, MapperCommand
from lattice.models.session import MappingSession
from lattice.testing import CoverageBuilder, TestClassifier, TestDiscovery

__all__ = [
    "FileAnalysis",
    "GraphNode",
    "ImportInfo",
    "MappingSession",
    "ManagedInstance",
    "MapperCommand",
    "LanguageAdapter",
    "PythonAdapter",
    "TypeScriptAdapter",
    "configure_logging",
    "DependencyGraphBuilder",
    "EntryPointDetector",
    "ConfigWiringDetector",
    "serialize_graph",
    "TestFile",
    "TestType",
    "GapEntry",
    "TestEdgeMapping",
    "TestCoverage",
    "TestDiscovery",
    "TestClassifier",
    "CoverageBuilder",
    "DirDoc",
    "ConfidenceSource",
    "GapSummary",
    "StaticAnalysisLimits",
    "parse_dir_doc",
    "traverse",
    "write_dir_doc",
    "is_stale",
    "last_git_commit_time",
    "FleetDispatcher",
    "FleetCheckpoint",
    "WavePlan",
    "DocumentAssembler",
    "SkeletonWriter",
    "CrossCuttingAnalyzer",
    "ProjectDoc",
    "EventFlow",
    "SharedState",
    "ApiContract",
    "PluginPoint",
    "CrossCuttingBlindSpot",
    "write_project_doc",
    "parse_project_doc",
    "CommandRequest",
    "CommandResponse",
    "MapperError",
    "mapper_app",
    "run_stdio_server",
    "TaskRecord",
    "CircuitBreakerState",
    "BreakerConfig",
    "OrchestratorConfig",
    "CircuitBreaker",
    "ProcessManager",
    "ProcessRegistry",
    "TaskQueue",
    "PrioritizedTask",
    "write_message",
    "read_message",
    "drain_stderr",
    "create_request_envelope",
    "parse_response_envelope",
    # MCP connectors
    "ConnectorRegistry",
    "ConnectorConfig",
    "ConnectorResult",
    "ConnectorState",
    "ConnectorPermissions",
    "BaseConnector",
    "ConnectorError",
    "TavilyConnector",
    "GitHubConnector",
    "MattermostConnector",
    "get_connector_status",
    # Per-project config models
    "ProjectConfig",
    "MapperProjectConfig",
    "ConnectorProjectConfig",
    "ModelProfileConfig",
    "OrchestratorProjectConfig",
]
