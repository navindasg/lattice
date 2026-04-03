"""Tests for the fleet wave planner and fleet data models.

TDD RED phase: These tests define the expected behavior of:
- build_directory_dag(): aggregates file-level edges to directory-level
- plan_waves(): topological wave ordering (leaf-first)
- format_wave_plan(): human-readable wave plan display
- Fleet frozen Pydantic models (Wave, WavePlan, AgentResult, DirectoryContext)
"""
from datetime import datetime, timezone

import networkx as nx
import pytest

from lattice.fleet.models import (
    AgentResult,
    DirectoryContext,
    Wave,
    WavePlan,
)
from lattice.fleet.planner import build_directory_dag, format_wave_plan, plan_waves


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


def test_wave_model_is_frozen():
    """Pydantic v2 frozen model raises ValidationError on normal attribute assignment."""
    from pydantic import ValidationError
    wave = Wave(index=0, directories=frozenset({"src/a"}), estimated_input_tokens=100)
    with pytest.raises(ValidationError):
        wave.index = 1  # type: ignore[misc]


def test_wave_plan_model_is_frozen():
    from pydantic import ValidationError
    wave = Wave(index=0, directories=frozenset({"src/a"}), estimated_input_tokens=100)
    plan = WavePlan(waves=[wave], total_estimated_tokens=100, run_id="test-run")
    with pytest.raises(ValidationError):
        plan.run_id = "other"  # type: ignore[misc]


def test_agent_result_model_is_frozen():
    from pydantic import ValidationError
    result = AgentResult(
        directory="src/a",
        failed=False,
        error=None,
        dir_doc=None,
        test_stubs=[],
        input_tokens=50,
        output_tokens=100,
    )
    with pytest.raises(ValidationError):
        result.failed = True  # type: ignore[misc]


def test_directory_context_model_is_frozen():
    from pydantic import ValidationError
    ctx = DirectoryContext(
        directory="src/a",
        files=[],
        inbound_edges=[],
        outbound_edges=[],
        gap_entries=[],
        child_summaries=[],
        developer_hints=[],
        is_entry_point=False,
    )
    with pytest.raises(ValidationError):
        ctx.directory = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_directory_dag tests
# ---------------------------------------------------------------------------


def _make_file_graph(*edges: tuple[str, str]) -> nx.DiGraph:
    """Helper: create file-level graph from (source, target) tuples."""
    g = nx.DiGraph()
    for src, tgt in edges:
        g.add_edge(src, tgt)
    return g


def _add_nodes(g: nx.DiGraph, *nodes: str) -> None:
    """Helper: add isolated nodes to graph."""
    for node in nodes:
        g.add_node(node)


def test_build_directory_dag_single_edge():
    """File edge src/models/user.py -> src/auth/session.py produces dir edge models->auth."""
    g = _make_file_graph(
        ("src/models/user.py", "src/auth/session.py"),
    )
    dag = build_directory_dag(g)
    assert dag.has_edge("src/models", "src/auth")


def test_build_directory_dag_removes_self_loops():
    """Files within same directory do not produce self-loop edges."""
    g = _make_file_graph(
        ("src/auth/session.py", "src/auth/utils.py"),
    )
    dag = build_directory_dag(g)
    assert not dag.has_edge("src/auth", "src/auth")
    # The directory should still be a node
    assert "src/auth" in dag.nodes


def test_build_directory_dag_isolated_directories():
    """Files with no cross-directory edges produce zero-edge directory nodes."""
    g = nx.DiGraph()
    _add_nodes(g, "src/utils/helpers.py", "src/utils/format.py")
    dag = build_directory_dag(g)
    assert "src/utils" in dag.nodes
    # No edges (isolated)
    assert dag.out_degree("src/utils") == 0
    assert dag.in_degree("src/utils") == 0


def test_build_directory_dag_deduplicates_edges():
    """Multiple file edges between same directories produce a single dir edge."""
    g = _make_file_graph(
        ("src/api/routes.py", "src/models/user.py"),
        ("src/api/handlers.py", "src/models/post.py"),
    )
    dag = build_directory_dag(g)
    assert dag.number_of_edges("src/api", "src/models") == 1


# ---------------------------------------------------------------------------
# plan_waves tests
# ---------------------------------------------------------------------------


def test_plan_waves_empty_graph():
    """Empty graph produces empty wave list."""
    dag = nx.DiGraph()
    waves = plan_waves(dag)
    assert waves == []


def test_plan_waves_single_isolated_dir():
    """Single node with no edges lands in wave 0."""
    dag = nx.DiGraph()
    dag.add_node("src/utils")
    waves = plan_waves(dag)
    assert len(waves) == 1
    assert waves[0].index == 0
    assert "src/utils" in waves[0].directories


def test_plan_waves_linear_chain():
    """Linear chain A->B->C produces 3 waves: C in wave 0, B in wave 1, A in wave 2.

    Edge direction: A depends on B (A imports B), B depends on C.
    So C has no outgoing deps (leaf), B depends on C, A depends on B.
    Wave 0 = leaves (depended upon), Wave 2 = root (importer).
    """
    # A -> B -> C means A imports B, B imports C
    # In bottom-up doc order: C first (wave 0), B next (wave 1), A last (wave 2)
    dag = nx.DiGraph()
    dag.add_edge("src/A", "src/B")  # A depends on B
    dag.add_edge("src/B", "src/C")  # B depends on C
    waves = plan_waves(dag)
    assert len(waves) == 3
    # Wave 0 = C (no outgoing edges = depended upon, no dependencies itself)
    assert "src/C" in waves[0].directories
    # Wave 1 = B
    assert "src/B" in waves[1].directories
    # Wave 2 = A
    assert "src/A" in waves[2].directories


def test_plan_waves_diamond_pattern():
    """Diamond: A->B, A->C, B->D, C->D groups correctly.

    Wave 0: D (no outgoing deps)
    Wave 1: B, C (both depend on D only)
    Wave 2: A (depends on B and C)
    """
    dag = nx.DiGraph()
    dag.add_edge("src/A", "src/B")
    dag.add_edge("src/A", "src/C")
    dag.add_edge("src/B", "src/D")
    dag.add_edge("src/C", "src/D")
    waves = plan_waves(dag)
    assert len(waves) == 3
    assert "src/D" in waves[0].directories
    assert "src/B" in waves[1].directories
    assert "src/C" in waves[1].directories
    assert "src/A" in waves[2].directories


def test_plan_waves_isolated_dirs_in_wave_0():
    """Isolated directories (no edges) land in wave 0."""
    dag = nx.DiGraph()
    dag.add_node("src/standalone")
    dag.add_edge("src/A", "src/B")
    waves = plan_waves(dag)
    # standalone should be in wave 0 (alongside src/B)
    wave_0_dirs = waves[0].directories
    assert "src/standalone" in wave_0_dirs


def test_plan_waves_cyclic_graph_raises():
    """Cyclic directory graph raises ValueError."""
    dag = nx.DiGraph()
    dag.add_edge("src/A", "src/B")
    dag.add_edge("src/B", "src/A")
    with pytest.raises(ValueError, match="[Cc]ycle"):
        plan_waves(dag)


def test_plan_waves_wave_indices():
    """Wave objects have correct sequential indices starting at 0."""
    dag = nx.DiGraph()
    dag.add_edge("src/A", "src/B")
    waves = plan_waves(dag)
    for i, wave in enumerate(waves):
        assert wave.index == i


def test_plan_waves_estimated_tokens_positive():
    """Wave estimated_input_tokens is >= 0 (zero is acceptable for empty waves)."""
    dag = nx.DiGraph()
    dag.add_node("src/utils")
    waves = plan_waves(dag)
    assert all(wave.estimated_input_tokens >= 0 for wave in waves)


# ---------------------------------------------------------------------------
# format_wave_plan tests
# ---------------------------------------------------------------------------


def test_format_wave_plan_non_empty():
    """format_wave_plan returns a non-empty string."""
    wave = Wave(index=0, directories=frozenset({"src/a", "src/b"}), estimated_input_tokens=5000)
    plan = WavePlan(waves=[wave], total_estimated_tokens=5000, run_id="test-123")
    result = format_wave_plan(plan, tier="silver")
    assert isinstance(result, str)
    assert len(result) > 0


def test_format_wave_plan_contains_wave_info():
    """format_wave_plan output contains wave index and directory count."""
    wave = Wave(index=0, directories=frozenset({"src/a", "src/b"}), estimated_input_tokens=5000)
    plan = WavePlan(waves=[wave], total_estimated_tokens=5000, run_id="test-123")
    result = format_wave_plan(plan, tier="silver")
    # Should mention wave 0 or Wave 0
    assert "0" in result
    # Should mention directory count (2)
    assert "2" in result


def test_format_wave_plan_contains_token_info():
    """format_wave_plan output mentions token estimation."""
    wave = Wave(index=0, directories=frozenset({"src/a"}), estimated_input_tokens=10000)
    plan = WavePlan(waves=[wave], total_estimated_tokens=10000, run_id="test-456")
    result = format_wave_plan(plan, tier="silver")
    # Should contain some reference to tokens
    assert "token" in result.lower() or "10" in result


def test_format_wave_plan_bronze_tier():
    """format_wave_plan works with bronze tier (Ollama)."""
    wave = Wave(index=0, directories=frozenset({"src/a"}), estimated_input_tokens=1000)
    plan = WavePlan(waves=[wave], total_estimated_tokens=1000, run_id="test-789")
    result = format_wave_plan(plan, tier="bronze")
    assert isinstance(result, str)
    assert len(result) > 0
