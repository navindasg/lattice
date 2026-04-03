"""Tests for FleetDispatcher with LangGraph Send API and error isolation.

TDD RED phase: tests are written before implementation.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import duckdb
import pytest
from langchain_core.messages import AIMessage

from lattice.fleet.dispatcher import FleetDispatcher
from lattice.fleet.models import AgentResult, Wave
from lattice.persistence.checkpointer import create_checkpointer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_wave(dirs: list[str], index: int = 0) -> Wave:
    return Wave(index=index, directories=frozenset(dirs), estimated_input_tokens=1000)


def _make_anthropic_response(input_tokens: int = 100, output_tokens: int = 50) -> AIMessage:
    """Create a mock AIMessage with Anthropic-style usage metadata."""
    return AIMessage(
        content='{"directory": "src/auth", "confidence": 0.9, "source": "agent", '
        '"confidence_factors": ["full contents reviewed"], '
        '"summary": "Auth module", "responsibilities": ["JWT"], '
        '"developer_hints": [], "child_refs": [], '
        '"static_analysis_limits": {"dynamic_imports": 0, "unresolved_paths": 0}, '
        '"gap_summary": {"untested_edges": 0, "top_gaps": []}, "test_stubs": []}',
        response_metadata={"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}},
    )


def _make_openai_response(prompt_tokens: int = 80, completion_tokens: int = 40) -> AIMessage:
    """Create a mock AIMessage with OpenAI-style usage metadata."""
    return AIMessage(
        content='{"directory": "src/utils", "confidence": 0.85, "source": "agent", '
        '"confidence_factors": ["full contents reviewed"], '
        '"summary": "Utils module", "responsibilities": ["helpers"], '
        '"developer_hints": [], "child_refs": [], '
        '"static_analysis_limits": {"dynamic_imports": 0, "unresolved_paths": 0}, '
        '"gap_summary": {"untested_edges": 0, "top_gaps": []}, "test_stubs": []}',
        response_metadata={"usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}},
    )


def _make_checkpointer():
    """Create an in-memory DuckDB checkpointer for tests."""
    conn = duckdb.connect(":memory:")
    from langgraph.checkpoint.duckdb import DuckDBSaver

    saver = DuckDBSaver(conn)
    saver.setup()
    return saver, conn


def _make_dispatcher(mock_model, concurrency_cap: int = 8) -> FleetDispatcher:
    """Create a FleetDispatcher with a mocked model factory."""
    import networkx as nx
    from pathlib import Path
    from unittest.mock import patch

    file_graph = nx.DiGraph()
    saver, conn = _make_checkpointer()

    from lattice.fleet.checkpoint import FleetCheckpoint

    fleet_checkpoint = FleetCheckpoint(conn)

    dispatcher = FleetDispatcher(
        tier="silver",
        project_root=Path("/tmp/test_project"),
        file_graph=file_graph,
        coverage_data={},
        agent_docs_root=Path("/tmp/.agent-docs"),
        checkpoint=fleet_checkpoint,
        concurrency_cap=concurrency_cap,
        _checkpointer=saver,
        _model_override=mock_model,
    )
    return dispatcher


# ---------------------------------------------------------------------------
# Task 1 Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wave_dispatch_produces_results_for_all_dirs():
    """Wave with 3 directories produces 3 AgentResult entries."""
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=_make_anthropic_response())

    dispatcher = _make_dispatcher(mock_model)
    wave = _make_wave(["src/auth", "src/utils", "src/models"])

    results = await dispatcher.dispatch_wave(wave)

    assert len(results) == 3
    assert all(isinstance(r, AgentResult) for r in results)


@pytest.mark.asyncio
async def test_failure_isolation_one_dir_fails_others_succeed():
    """One failing directory still produces results for the other 2 directories.

    Replaces _investigate_directory_async at the instance level so that
    'src/auth' raises (simulating all retries exhausted) and others succeed.
    dispatch_wave must still return 3 results, with exactly 1 failed.
    """
    dirs = ["src/auth", "src/utils", "src/models"]
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=_make_anthropic_response())

    dispatcher = _make_dispatcher(mock_model)

    async def fake_investigate(directory: str) -> AgentResult:
        if directory == "src/auth":
            raise RuntimeError("persistent LLM failure after all retries")
        return AgentResult(directory=directory, failed=False, input_tokens=100, output_tokens=50)

    # Replace at instance level using a bound-method-style lambda
    dispatcher._investigate_directory_async = fake_investigate  # type: ignore[method-assign]

    wave = _make_wave(dirs)
    results = await dispatcher.dispatch_wave(wave)

    assert len(results) == 3
    failed = [r for r in results if r.failed]
    succeeded = [r for r in results if not r.failed]
    assert len(failed) == 1
    assert len(succeeded) == 2
    assert failed[0].error is not None


@pytest.mark.asyncio
async def test_concurrency_cap_limits_simultaneous_calls():
    """Concurrency cap is respected — max N calls in flight at once."""
    max_concurrent = 0
    current_concurrent = 0
    concurrency_cap = 3

    async def mock_ainvoke(messages, **kwargs):
        nonlocal max_concurrent, current_concurrent
        current_concurrent += 1
        max_concurrent = max(max_concurrent, current_concurrent)
        await asyncio.sleep(0.01)  # yield to let other tasks start
        current_concurrent -= 1
        return _make_anthropic_response()

    mock_model = AsyncMock()
    mock_model.ainvoke = mock_ainvoke

    dispatcher = _make_dispatcher(mock_model, concurrency_cap=concurrency_cap)
    # 6 dirs — without cap, all 6 could start at once
    wave = _make_wave(["src/a", "src/b", "src/c", "src/d", "src/e", "src/f"])

    results = await dispatcher.dispatch_wave(wave)

    assert len(results) == 6
    assert max_concurrent <= concurrency_cap


@pytest.mark.asyncio
async def test_token_extraction_anthropic_format():
    """Token usage extraction works for Anthropic field names."""
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=_make_anthropic_response(input_tokens=150, output_tokens=75))

    dispatcher = _make_dispatcher(mock_model)
    wave = _make_wave(["src/auth"])

    results = await dispatcher.dispatch_wave(wave)

    assert len(results) == 1
    result = results[0]
    assert not result.failed
    assert result.input_tokens == 150
    assert result.output_tokens == 75


@pytest.mark.asyncio
async def test_token_extraction_openai_format():
    """Token usage extraction works for OpenAI field names (prompt_tokens/completion_tokens)."""
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(
        return_value=_make_openai_response(prompt_tokens=80, completion_tokens=40)
    )

    dispatcher = _make_dispatcher(mock_model)
    wave = _make_wave(["src/utils"])

    results = await dispatcher.dispatch_wave(wave)

    assert len(results) == 1
    result = results[0]
    assert not result.failed
    assert result.input_tokens == 80
    assert result.output_tokens == 40


@pytest.mark.asyncio
async def test_retry_fires_on_transient_error():
    """Retry fires on transient error, succeeds on second attempt."""
    attempt_count = 0

    async def mock_ainvoke(messages, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            raise RuntimeError("transient timeout")
        return _make_anthropic_response()

    mock_model = AsyncMock()
    mock_model.ainvoke = mock_ainvoke

    dispatcher = _make_dispatcher(mock_model)
    wave = _make_wave(["src/auth"])

    results = await dispatcher.dispatch_wave(wave)

    assert len(results) == 1
    assert not results[0].failed
    assert attempt_count == 2  # failed once, succeeded on retry


@pytest.mark.asyncio
async def test_successful_response_populates_dir_doc():
    """Successful LLM response with valid JSON populates dir_doc on AgentResult."""
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=_make_anthropic_response())

    dispatcher = _make_dispatcher(mock_model)
    wave = _make_wave(["src/auth"])

    results = await dispatcher.dispatch_wave(wave)

    assert len(results) == 1
    result = results[0]
    assert not result.failed
    assert result.dir_doc is not None
    assert result.dir_doc.directory == "src/auth"
    assert result.dir_doc.confidence == 0.9
    assert result.dir_doc.source == "agent"


@pytest.mark.asyncio
async def test_invalid_dirdoc_schema_marks_not_failed():
    """Valid JSON that fails DirDoc validation produces dir_doc=None but failed=False.

    source='agent' with confidence_factors=[] violates the model_validator,
    but this is a doc-not-produced outcome, not a dispatch failure.
    """
    bad_content = (
        '{"directory": "src/auth", "confidence": 0.9, "source": "agent", '
        '"confidence_factors": [], '  # violates: must be non-empty for source='agent'
        '"summary": "Auth module", "responsibilities": ["JWT"], '
        '"developer_hints": [], "child_refs": [], '
        '"static_analysis_limits": {"dynamic_imports": 0, "unresolved_paths": 0}, '
        '"gap_summary": {"untested_edges": 0, "top_gaps": []}, "test_stubs": []}'
    )
    bad_response = AIMessage(
        content=bad_content,
        response_metadata={"usage": {"input_tokens": 100, "output_tokens": 50}},
    )
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=bad_response)

    dispatcher = _make_dispatcher(mock_model)
    wave = _make_wave(["src/auth"])

    results = await dispatcher.dispatch_wave(wave)

    assert len(results) == 1
    result = results[0]
    assert not result.failed
    assert result.dir_doc is None


@pytest.mark.asyncio
async def test_wave_completion_recorded_in_checkpoint():
    """After dispatch_wave completes, wave status is written to FleetCheckpoint."""
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=_make_anthropic_response())

    import networkx as nx
    from pathlib import Path

    file_graph = nx.DiGraph()
    saver, conn = _make_checkpointer()
    from lattice.fleet.checkpoint import FleetCheckpoint

    fleet_checkpoint = FleetCheckpoint(conn)

    dispatcher = FleetDispatcher(
        tier="silver",
        project_root=Path("/tmp/test_project"),
        file_graph=file_graph,
        coverage_data={},
        agent_docs_root=Path("/tmp/.agent-docs"),
        checkpoint=fleet_checkpoint,
        concurrency_cap=8,
        _checkpointer=saver,
        _model_override=mock_model,
    )

    run_id = "test-run-123"
    wave = _make_wave(["src/auth", "src/utils"], index=0)

    results = await dispatcher.dispatch_wave(wave, run_id=run_id)

    assert len(results) == 2
    completed_waves = fleet_checkpoint.get_completed_waves(run_id)
    assert 0 in completed_waves


# ---------------------------------------------------------------------------
# Task 1 (08-02): Developer-protected skip and IDK double-pass tests
# ---------------------------------------------------------------------------


def _make_dir_doc_yaml_with_source(
    directory: str,
    source: str,
    confidence: float = 0.8,
) -> str:
    """Create a _dir.md file with specified source field for developer-protected tests."""
    from datetime import datetime, timezone
    last_analyzed = datetime.now(timezone.utc).isoformat()
    confidence_factors = '["reviewed"]' if source == "agent" else "[]"
    return f"""---
directory: {directory}
confidence: {confidence}
source: {source}
confidence_factors: {confidence_factors}
stale: false
last_analyzed: "{last_analyzed}"
static_analysis_limits:
  dynamic_imports: 0
  unresolved_paths: 0
gap_summary:
  untested_edges: 0
  top_gaps: []
---

## Summary
Test directory

## Key Responsibilities
- test responsibility

## Developer Hints

## Child Docs
"""


def _make_dispatcher_with_agent_docs(
    mock_model,
    agent_docs_root,
    tmp_project_root,
    force: bool = False,
    concurrency_cap: int = 8,
) -> FleetDispatcher:
    """Create a FleetDispatcher with real paths for developer-protection tests."""
    import networkx as nx

    file_graph = nx.DiGraph()
    saver, conn = _make_checkpointer()

    from lattice.fleet.checkpoint import FleetCheckpoint

    fleet_checkpoint = FleetCheckpoint(conn)

    dispatcher = FleetDispatcher(
        tier="silver",
        project_root=tmp_project_root,
        file_graph=file_graph,
        coverage_data={},
        agent_docs_root=agent_docs_root,
        checkpoint=fleet_checkpoint,
        concurrency_cap=concurrency_cap,
        force=force,
        _checkpointer=saver,
        _model_override=mock_model,
    )
    return dispatcher


@pytest.mark.asyncio
async def test_developer_protected_skip(tmp_path):
    """Developer-protected directory is skipped without making an LLM call.

    When a _dir.md exists with source='developer' and force=False, the dispatcher
    returns AgentResult(failed=False, dir_doc=None) without calling ainvoke.
    """
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=_make_anthropic_response())

    agent_docs = tmp_path / ".agent-docs"
    dir_shadow = agent_docs / "src" / "auth"
    dir_shadow.mkdir(parents=True)
    (dir_shadow / "_dir.md").write_text(
        _make_dir_doc_yaml_with_source("src/auth", source="developer")
    )

    dispatcher = _make_dispatcher_with_agent_docs(
        mock_model, agent_docs, tmp_path, force=False
    )
    wave = _make_wave(["src/auth"])
    results = await dispatcher.dispatch_wave(wave)

    assert len(results) == 1
    result = results[0]
    assert not result.failed
    assert result.dir_doc is None
    # LLM should NOT have been called
    mock_model.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_developer_protected_force(tmp_path):
    """When force=True, developer-protected directories are investigated normally."""
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=_make_anthropic_response())

    agent_docs = tmp_path / ".agent-docs"
    dir_shadow = agent_docs / "src" / "auth"
    dir_shadow.mkdir(parents=True)
    (dir_shadow / "_dir.md").write_text(
        _make_dir_doc_yaml_with_source("src/auth", source="developer")
    )

    # Also create the actual source dir so PromptBuilder can find it
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "session.py").write_text("class Session: pass\n")

    dispatcher = _make_dispatcher_with_agent_docs(
        mock_model, agent_docs, tmp_path, force=True
    )
    wave = _make_wave(["src/auth"])

    # Use absolute path since prompt builder needs real directory
    abs_wave = _make_wave([str(tmp_path / "src" / "auth")])
    results = await dispatcher.dispatch_wave(abs_wave)

    assert len(results) == 1
    # LLM SHOULD have been called (force=True bypasses protection)
    assert mock_model.ainvoke.called


@pytest.mark.asyncio
async def test_is_developer_protected_returns_true_for_developer_source(tmp_path):
    """_is_developer_protected returns True when _dir.md has source='developer'."""
    mock_model = AsyncMock()
    agent_docs = tmp_path / ".agent-docs"
    dir_shadow = agent_docs / "src" / "auth"
    dir_shadow.mkdir(parents=True)
    (dir_shadow / "_dir.md").write_text(
        _make_dir_doc_yaml_with_source("src/auth", source="developer")
    )

    dispatcher = _make_dispatcher_with_agent_docs(mock_model, agent_docs, tmp_path)
    assert dispatcher._is_developer_protected("src/auth") is True


@pytest.mark.asyncio
async def test_is_developer_protected_returns_false_no_dir_md(tmp_path):
    """_is_developer_protected returns False when _dir.md does not exist."""
    mock_model = AsyncMock()
    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir()

    dispatcher = _make_dispatcher_with_agent_docs(mock_model, agent_docs, tmp_path)
    assert dispatcher._is_developer_protected("src/nonexistent") is False


@pytest.mark.asyncio
async def test_is_developer_protected_returns_false_for_agent_source(tmp_path):
    """_is_developer_protected returns False when _dir.md has source='agent'."""
    mock_model = AsyncMock()
    agent_docs = tmp_path / ".agent-docs"
    dir_shadow = agent_docs / "src" / "auth"
    dir_shadow.mkdir(parents=True)
    (dir_shadow / "_dir.md").write_text(
        _make_dir_doc_yaml_with_source("src/auth", source="agent")
    )

    dispatcher = _make_dispatcher_with_agent_docs(mock_model, agent_docs, tmp_path)
    assert dispatcher._is_developer_protected("src/auth") is False


def test_load_idk_directories_returns_set(tmp_path):
    """_load_idk_directories returns directories with idk-type entries from _hints.json."""
    import json

    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir()

    hints = {
        "src/auth": [{"type": "idk", "stored_at": "2024-01-01T00:00:00Z"}],
        "src/utils": [{"type": "hint", "text": "utility functions", "stored_at": "2024-01-01T00:00:00Z"}],
    }
    (agent_docs / "_hints.json").write_text(json.dumps(hints))

    mock_model = AsyncMock()
    dispatcher = _make_dispatcher_with_agent_docs(mock_model, agent_docs, tmp_path)

    idk_dirs = dispatcher._load_idk_directories()
    assert "src/auth" in idk_dirs
    assert "src/utils" not in idk_dirs


def test_load_idk_directories_returns_empty_when_no_hints_file(tmp_path):
    """_load_idk_directories returns empty set when _hints.json does not exist."""
    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir()

    mock_model = AsyncMock()
    dispatcher = _make_dispatcher_with_agent_docs(mock_model, agent_docs, tmp_path)

    idk_dirs = dispatcher._load_idk_directories()
    assert idk_dirs == set()


@pytest.mark.asyncio
async def test_idk_double_pass(tmp_path):
    """IDK directory runs 2 passes and picks the higher-confidence result.

    Pass 1 (angle=integration) returns confidence=0.4.
    Pass 2 (angle=data_flow) returns confidence=0.8.
    Expected: result with confidence=0.8 is returned.
    """
    import json as _json

    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir()

    # Register src/auth as IDK
    hints = {"src/auth": [{"type": "idk", "stored_at": "2024-01-01T00:00:00Z"}]}
    (agent_docs / "_hints.json").write_text(_json.dumps(hints))

    # Create the actual source directory
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "session.py").write_text("class Session: pass\n")

    low_confidence_response = AIMessage(
        content=_json.dumps({
            "directory": "src/auth",
            "confidence": 0.4,
            "source": "agent",
            "confidence_factors": ["limited context"],
            "summary": "Auth module (pass 1)",
            "responsibilities": ["JWT"],
            "developer_hints": [],
            "child_refs": [],
            "static_analysis_limits": {"dynamic_imports": 0, "unresolved_paths": 0},
            "gap_summary": {"untested_edges": 0, "top_gaps": []},
            "test_stubs": [],
        }),
        response_metadata={"usage": {"input_tokens": 100, "output_tokens": 50}},
    )
    high_confidence_response = AIMessage(
        content=_json.dumps({
            "directory": "src/auth",
            "confidence": 0.8,
            "source": "agent",
            "confidence_factors": ["full context"],
            "summary": "Auth module (pass 2)",
            "responsibilities": ["JWT", "Sessions"],
            "developer_hints": [],
            "child_refs": [],
            "static_analysis_limits": {"dynamic_imports": 0, "unresolved_paths": 0},
            "gap_summary": {"untested_edges": 0, "top_gaps": []},
            "test_stubs": [],
        }),
        response_metadata={"usage": {"input_tokens": 120, "output_tokens": 60}},
    )

    call_count = 0

    async def mock_ainvoke(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return low_confidence_response
        return high_confidence_response

    mock_model = AsyncMock()
    mock_model.ainvoke = mock_ainvoke

    dispatcher = _make_dispatcher_with_agent_docs(mock_model, agent_docs, tmp_path)

    # Use absolute path for the wave so PromptBuilder finds the directory
    abs_wave = _make_wave([str(tmp_path / "src" / "auth")])
    results = await dispatcher.dispatch_wave(abs_wave)

    assert len(results) == 1
    result = results[0]
    assert not result.failed
    assert result.dir_doc is not None
    # Higher confidence result from pass 2 must be selected
    assert result.dir_doc.confidence == 0.8
    # Both passes ran
    assert call_count == 2


@pytest.mark.asyncio
async def test_idk_structlog(tmp_path, capsys):
    """IDK directory dispatch logs idk_mode=True, search_radius=2, passes=2 via structlog."""
    import json as _json
    import structlog
    from structlog.testing import capture_logs

    agent_docs = tmp_path / ".agent-docs"
    agent_docs.mkdir()

    hints = {"src/auth": [{"type": "idk", "stored_at": "2024-01-01T00:00:00Z"}]}
    (agent_docs / "_hints.json").write_text(_json.dumps(hints))

    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "session.py").write_text("class Session: pass\n")

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=_make_anthropic_response())

    dispatcher = _make_dispatcher_with_agent_docs(mock_model, agent_docs, tmp_path)

    abs_wave = _make_wave([str(tmp_path / "src" / "auth")])

    with capture_logs() as log_entries:
        await dispatcher.dispatch_wave(abs_wave)

    # Find idk_mode log entry
    idk_entries = [e for e in log_entries if e.get("event") == "idk_mode"]
    assert len(idk_entries) >= 1
    entry = idk_entries[0]
    assert entry.get("idk_mode") is True
    assert entry.get("search_radius") == 2
    assert entry.get("passes") == 2
