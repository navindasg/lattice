"""Fleet subdomain for the agent fleet dispatcher.

Public API:
    Wave              — a parallelizable wave of directories
    WavePlan          — complete execution plan
    AgentResult       — outcome of one directory agent run
    DirectoryContext  — assembled context for one directory prompt
    build_directory_dag — aggregate file edges to directory edges
    plan_waves          — produce topological wave ordering
    format_wave_plan    — human-readable wave plan display
    PromptBuilder       — assembles investigation prompts with token estimation
    FleetCheckpoint   — DuckDB wave progress and token tracking
    FleetDispatcher   — LangGraph Send API parallel wave dispatcher
    DocumentAssembler — validates AgentResult and writes DirDoc to shadow tree
    SkeletonWriter    — writes test stubs to _test_stubs/ shadow path
"""
from lattice.fleet.assembler import DocumentAssembler
from lattice.fleet.checkpoint import FleetCheckpoint
from lattice.fleet.dispatcher import FleetDispatcher
from lattice.fleet.models import (
    AgentResult,
    DirectoryContext,
    Wave,
    WavePlan,
)
from lattice.fleet.planner import build_directory_dag, format_wave_plan, plan_waves
from lattice.fleet.prompt import PromptBuilder
from lattice.fleet.skeleton import SkeletonWriter

__all__ = [
    "Wave",
    "WavePlan",
    "AgentResult",
    "DirectoryContext",
    "build_directory_dag",
    "plan_waves",
    "format_wave_plan",
    "PromptBuilder",
    "FleetCheckpoint",
    "FleetDispatcher",
    "DocumentAssembler",
    "SkeletonWriter",
]
