"""Tests for the fleet PromptBuilder with token estimation.

TDD RED phase: These tests define the expected behavior of:
- PromptBuilder.build(): assembles 6 context sections into messages
- PromptBuilder.estimate_tokens(): pre-dispatch cost display without reading file contents
- Edge cases: namespace packages, missing directories, binary files
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pytest

from lattice.fleet.prompt import PromptBuilder


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


def _make_dir_doc_yaml(
    directory: str = "src/auth",
    summary: str = "Authentication module",
    responsibilities: list[str] | None = None,
    developer_hints: list[str] | None = None,
) -> str:
    """Create minimal valid _dir.md content for tests."""
    responsibilities = responsibilities or ["Handle JWT tokens", "Session management"]
    developer_hints = developer_hints or []
    last_analyzed = datetime.now(timezone.utc).isoformat()

    resp_bullets = "\n".join(f"- {r}" for r in responsibilities)
    hints_bullets = "\n".join(f"- {h}" for h in developer_hints)
    hints_section = f"\n## Developer Hints\n{hints_bullets}" if developer_hints else ""

    return f"""---
directory: {directory}
confidence: 0.8
source: static
last_analyzed: "{last_analyzed}"
---

## Summary
{summary}

## Key Responsibilities
{resp_bullets}{hints_section}
"""


def _make_file_graph_with_edges(
    source_files: list[str],
    target_files: list[str],
) -> nx.DiGraph:
    """Create file graph where source_files import target_files."""
    g = nx.DiGraph()
    for src in source_files:
        g.add_node(src)
    for tgt in target_files:
        g.add_node(tgt)
    for src, tgt in zip(source_files, target_files):
        g.add_edge(src, tgt)
    return g


def _empty_graph() -> nx.DiGraph:
    return nx.DiGraph()


# ---------------------------------------------------------------------------
# Test: PromptBuilder instantiation
# ---------------------------------------------------------------------------


def test_prompt_builder_instantiates():
    """PromptBuilder is a stateless builder with no constructor args."""
    builder = PromptBuilder()
    assert builder is not None


# ---------------------------------------------------------------------------
# Test: build() returns correct structure
# ---------------------------------------------------------------------------


def test_build_returns_messages_and_tokens(tmp_path: Path):
    """build() returns (messages: list[dict], estimated_tokens: int) tuple."""
    # Create a directory with one Python file
    src_dir = tmp_path / "src" / "utils"
    src_dir.mkdir(parents=True)
    (src_dir / "helpers.py").write_text("def add(a, b):\n    return a + b\n")

    builder = PromptBuilder()
    messages, tokens = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    assert isinstance(messages, list)
    assert len(messages) >= 2  # system + user at minimum
    assert isinstance(tokens, int)


def test_build_messages_have_role_and_content(tmp_path: Path):
    """Each message dict has 'role' and 'content' keys."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "module.py").write_text("x = 1\n")

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    for msg in messages:
        assert "role" in msg
        assert "content" in msg
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_build_includes_file_contents(tmp_path: Path):
    """User message includes full content of files in the directory."""
    src_dir = tmp_path / "src" / "auth"
    src_dir.mkdir(parents=True)
    unique_content = "SECRET_UNIQUE_STRING_12345"
    (src_dir / "session.py").write_text(f"# Auth module\n{unique_content}\n")

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    # Full file content must appear in user message
    user_content = messages[1]["content"]
    assert unique_content in user_content


def test_build_token_estimate_positive_for_nonempty(tmp_path: Path):
    """Token estimation returns > 0 for non-empty directory."""
    src_dir = tmp_path / "src" / "models"
    src_dir.mkdir(parents=True)
    (src_dir / "user.py").write_text("class User:\n    name: str\n    email: str\n")

    builder = PromptBuilder()
    _, tokens = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    assert tokens > 0


# ---------------------------------------------------------------------------
# Test: graph edges section
# ---------------------------------------------------------------------------


def test_build_includes_inbound_edges(tmp_path: Path):
    """Graph context section includes inbound edges (what imports files in this dir)."""
    src_dir = tmp_path / "src" / "models"
    src_dir.mkdir(parents=True)
    (src_dir / "user.py").write_text("class User: pass\n")

    # api/routes.py imports models/user.py
    file_graph = nx.DiGraph()
    file_graph.add_edge("src/api/routes.py", "src/models/user.py")
    file_graph.add_node("src/models/user.py")

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=file_graph,
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    user_content = messages[1]["content"]
    # Inbound edge source should appear
    assert "src/api/routes.py" in user_content or "api/routes.py" in user_content


def test_build_includes_outbound_edges(tmp_path: Path):
    """Graph context section includes outbound edges (what this dir imports)."""
    src_dir = tmp_path / "src" / "api"
    src_dir.mkdir(parents=True)
    (src_dir / "routes.py").write_text("from src.models.user import User\n")

    # api/routes.py imports models/user.py
    file_graph = nx.DiGraph()
    file_graph.add_edge("src/api/routes.py", "src/models/user.py")

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=file_graph,
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    user_content = messages[1]["content"]
    # Outbound edge target should appear
    assert "src/models/user.py" in user_content or "models/user.py" in user_content


# ---------------------------------------------------------------------------
# Test: child _dir.md summaries
# ---------------------------------------------------------------------------


def test_build_includes_child_dir_summaries(tmp_path: Path):
    """Child _dir.md summaries are included when present."""
    # Parent directory being analyzed
    parent_dir = tmp_path / "src"
    parent_dir.mkdir(parents=True)
    (parent_dir / "__init__.py").write_text("")

    # Child directory with _dir.md in agent-docs shadow
    agent_docs = tmp_path / ".agent-docs"
    child_shadow = agent_docs / "src" / "auth"
    child_shadow.mkdir(parents=True)
    child_dir_md = child_shadow / "_dir.md"
    child_dir_md.write_text(_make_dir_doc_yaml(
        directory="src/auth",
        summary="Handles JWT authentication",
        responsibilities=["Issue JWT tokens", "Validate sessions"],
    ))

    # Actual child directory must exist on disk
    (tmp_path / "src" / "auth").mkdir(parents=True, exist_ok=True)

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(parent_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=agent_docs,
    )

    user_content = messages[1]["content"]
    # Child summary should appear
    assert "Handles JWT authentication" in user_content


def test_build_child_summaries_include_only_summary_and_responsibilities(tmp_path: Path):
    """Child summaries include summary + responsibilities only (not full DirDoc)."""
    parent_dir = tmp_path / "src"
    parent_dir.mkdir(parents=True)
    (parent_dir / "__init__.py").write_text("")

    agent_docs = tmp_path / ".agent-docs"
    child_shadow = agent_docs / "src" / "models"
    child_shadow.mkdir(parents=True)

    # Include a developer_hints field that should NOT appear in child summary
    secret_hint = "PRIVATE_DEVELOPER_HINT_XYZ"
    child_shadow_md = child_shadow / "_dir.md"
    child_shadow_md.write_text(_make_dir_doc_yaml(
        directory="src/models",
        summary="Data models layer",
        developer_hints=[secret_hint],
    ))

    (tmp_path / "src" / "models").mkdir(parents=True, exist_ok=True)

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(parent_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=agent_docs,
    )

    user_content = messages[1]["content"]
    # Child summary should be present
    assert "Data models layer" in user_content
    # Developer hints from child should NOT appear (summary+responsibilities only)
    assert secret_hint not in user_content


# ---------------------------------------------------------------------------
# Test: developer hints
# ---------------------------------------------------------------------------


def test_build_includes_developer_hints_from_existing_dir_doc(tmp_path: Path):
    """Developer hints from existing _dir.md for THIS directory are included."""
    src_dir = tmp_path / "src" / "auth"
    src_dir.mkdir(parents=True)
    (src_dir / "session.py").write_text("class Session: pass\n")

    # Create _dir.md with developer hints for this directory
    agent_docs = tmp_path / ".agent-docs"
    dir_shadow = agent_docs / "src" / "auth"
    dir_shadow.mkdir(parents=True)
    unique_hint = "UNIQUE_DEVELOPER_HINT_ABC"
    (dir_shadow / "_dir.md").write_text(_make_dir_doc_yaml(
        directory="src/auth",
        developer_hints=[unique_hint],
    ))

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=agent_docs,
    )

    user_content = messages[1]["content"]
    assert unique_hint in user_content


# ---------------------------------------------------------------------------
# Test: gap entries
# ---------------------------------------------------------------------------


def test_build_includes_gap_entries(tmp_path: Path):
    """Gap entries for files in this directory appear in the prompt."""
    src_dir = tmp_path / "src" / "api"
    src_dir.mkdir(parents=True)
    (src_dir / "routes.py").write_text("def get_user(): pass\n")

    # Coverage data with a gap involving this directory's files
    coverage_data = {
        "gaps": [
            {
                "source": "src/api/routes.py",
                "target": "src/models/user.py",
                "centrality": 0.85,
                "annotation": "High-centrality untested seam",
            }
        ]
    }

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data=coverage_data,
        agent_docs_root=tmp_path / ".agent-docs",
    )

    user_content = messages[1]["content"]
    assert "src/api/routes.py" in user_content or "routes.py" in user_content


# ---------------------------------------------------------------------------
# Test: edge cases
# ---------------------------------------------------------------------------


def test_build_namespace_package_minimal_prompt(tmp_path: Path):
    """Directory with only __init__.py returns minimal prompt and low token estimate."""
    ns_dir = tmp_path / "src" / "namespace"
    ns_dir.mkdir(parents=True)
    (ns_dir / "__init__.py").write_text("")  # Empty namespace package

    builder = PromptBuilder()
    messages, tokens = builder.build(
        directory=str(ns_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    # Should still return valid messages
    assert isinstance(messages, list)
    assert len(messages) >= 2
    # Token estimate should be very low (namespace package has almost no content)
    assert tokens < 5000


def test_build_directory_not_found_returns_empty(tmp_path: Path):
    """Directory not found on disk returns empty messages with 0 tokens (defensive)."""
    nonexistent = tmp_path / "does" / "not" / "exist"

    builder = PromptBuilder()
    messages, tokens = builder.build(
        directory=str(nonexistent),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    assert messages == []
    assert tokens == 0


# ---------------------------------------------------------------------------
# Test: system prompt content
# ---------------------------------------------------------------------------


def test_build_system_message_mentions_documentation_role(tmp_path: Path):
    """System message defines codebase documentation agent role."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "mod.py").write_text("x = 1\n")

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    system_content = messages[0]["content"].lower()
    assert "documentation" in system_content or "document" in system_content


# ---------------------------------------------------------------------------
# Test: estimate_tokens standalone method
# ---------------------------------------------------------------------------


def test_estimate_tokens_returns_positive_for_nonempty(tmp_path: Path):
    """estimate_tokens() returns > 0 for directory with files."""
    src_dir = tmp_path / "src" / "core"
    src_dir.mkdir(parents=True)
    (src_dir / "engine.py").write_text("class Engine:\n    def run(self): pass\n" * 20)

    builder = PromptBuilder()
    tokens = builder.estimate_tokens(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    assert tokens > 0


def test_estimate_tokens_empty_dir_returns_low(tmp_path: Path):
    """estimate_tokens() for empty dir returns only the system prompt overhead (no file content)."""
    ns_dir = tmp_path / "src" / "empty"
    ns_dir.mkdir(parents=True)
    # No files

    builder = PromptBuilder()
    tokens = builder.estimate_tokens(
        directory=str(ns_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
    )

    # Should return only the prompt overhead (no file content to estimate)
    # and significantly less than a non-empty directory
    assert tokens < 2000


# ---------------------------------------------------------------------------
# Task 1 (08-02): IDK mode and prompt angle tests
# ---------------------------------------------------------------------------


def test_idk_neighbor_expansion(tmp_path: Path):
    """build(idk_mode=True) includes 'Neighboring Directory Context' section in user message."""
    # Target directory
    src_dir = tmp_path / "src" / "auth"
    src_dir.mkdir(parents=True)
    (src_dir / "session.py").write_text("from src.utils import helpers\n")

    # Neighbor directory (2-hop via file graph)
    neighbor_dir = tmp_path / "src" / "utils"
    neighbor_dir.mkdir(parents=True)
    (neighbor_dir / "helpers.py").write_text("def helper(): pass\n")

    # Build a graph: src/auth/session.py -> src/utils/helpers.py
    file_graph = nx.DiGraph()
    file_graph.add_edge("src/auth/session.py", "src/utils/helpers.py")

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=file_graph,
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
        idk_mode=True,
    )

    user_content = messages[1]["content"]
    assert "Neighboring Directory Context" in user_content


def test_prompt_angle_integration(tmp_path: Path):
    """build(prompt_angle='integration') adds integration patterns guidance to system prompt."""
    src_dir = tmp_path / "src" / "api"
    src_dir.mkdir(parents=True)
    (src_dir / "routes.py").write_text("def route(): pass\n")

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
        prompt_angle="integration",
    )

    system_content = messages[0]["content"]
    assert "integration patterns" in system_content.lower()


def test_prompt_angle_data_flow(tmp_path: Path):
    """build(prompt_angle='data_flow') adds data flow guidance to system prompt."""
    src_dir = tmp_path / "src" / "pipeline"
    src_dir.mkdir(parents=True)
    (src_dir / "transform.py").write_text("def transform(data): return data\n")

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
        prompt_angle="data_flow",
    )

    system_content = messages[0]["content"]
    assert "data flow" in system_content.lower()


def test_collect_neighbor_contents_returns_neighbor_files(tmp_path: Path):
    """_collect_neighbor_contents returns files from 2-hop neighbors via BFS."""
    # Create directories and files
    auth_dir = tmp_path / "src" / "auth"
    auth_dir.mkdir(parents=True)
    (auth_dir / "session.py").write_text("from src.utils import helpers\n")

    utils_dir = tmp_path / "src" / "utils"
    utils_dir.mkdir(parents=True)
    (utils_dir / "helpers.py").write_text("UNIQUE_NEIGHBOR_CONTENT_XYZ\n")

    # Build graph: auth/session.py -> utils/helpers.py (1 hop)
    file_graph = nx.DiGraph()
    file_graph.add_edge("src/auth/session.py", "src/utils/helpers.py")

    builder = PromptBuilder()
    neighbors = builder._collect_neighbor_contents(
        rel_dir="src/auth",
        file_graph=file_graph,
        project_root=tmp_path,
        depth=2,
    )

    # Should find src/utils as a neighbor
    neighbor_dirs = [n["directory"] for n in neighbors]
    assert any("utils" in d for d in neighbor_dirs)


def test_build_default_angle_no_extra_guidance(tmp_path: Path):
    """build() with default prompt_angle does not add angle-specific guidance."""
    src_dir = tmp_path / "src" / "core"
    src_dir.mkdir(parents=True)
    (src_dir / "engine.py").write_text("class Engine: pass\n")

    builder = PromptBuilder()
    messages, _ = builder.build(
        directory=str(src_dir),
        project_root=tmp_path,
        file_graph=_empty_graph(),
        coverage_data={},
        agent_docs_root=tmp_path / ".agent-docs",
        prompt_angle="default",
    )

    system_content = messages[0]["content"]
    # Default angle should NOT add angle-specific lines
    assert "Focus on integration patterns" not in system_content
    assert "Focus on data flow" not in system_content
