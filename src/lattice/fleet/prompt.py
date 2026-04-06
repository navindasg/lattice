"""PromptBuilder for the agent fleet dispatcher.

Assembles investigation prompts for LLM documentation agents. Each prompt
includes 6 context sections as per user decisions:
    1. File contents     — full content of every file in the directory (no truncation)
    2. Graph context     — inbound/outbound edges, entry point flags
    3. Gap entries       — untested seams involving this directory's files
    4. Child summaries   — summary + responsibilities from child _dir.md files
    5. Developer hints   — developer_hints from existing _dir.md for this directory
    6. Output format     — system message with DirDoc JSON schema instructions

Token estimation formula (from RESEARCH.md):
    (total_file_bytes / 3.5) + (graph_edges * 30) + (gap_entries * 50)
    + (child_summaries * 200) + PROMPT_OVERHEAD

Public API:
    PromptBuilder   — stateless builder class
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import structlog

from lattice.shadow.reader import parse_dir_doc

logger = structlog.get_logger()

# Approximate overhead tokens for system prompt + formatting
_PROMPT_OVERHEAD = 1500

# Tokens per graph edge reference (source + target path strings)
_TOKENS_PER_EDGE = 30

# Tokens per gap entry (source, target, centrality, annotation)
_TOKENS_PER_GAP = 50

# Tokens per child summary (summary text + responsibilities list)
_TOKENS_PER_CHILD_SUMMARY = 200

# Files to skip when reading directory contents
_SKIP_HIDDEN = frozenset({"__pycache__", ".git", ".mypy_cache", ".pytest_cache"})

_SYSTEM_PROMPT = """\
You are a codebase documentation agent. Your task is to analyze a directory in a \
software project and produce a structured documentation summary.

You will receive:
1. The full contents of all source files in the directory
2. The dependency graph context (inbound/outbound imports, entry point flags)
3. Test coverage gap entries (untested integration seams)
4. Child directory summaries (already-documented subdirectories)
5. Developer hints (optional notes from the project author)

Produce a JSON object with the following fields (matching DirDoc schema):
{
  "directory": "<relative path>",
  "confidence": <float 0.0-1.0>,
  "source": "agent",
  "confidence_factors": ["<reason 1>", "<reason 2>"],
  "summary": "<one paragraph description of purpose and role>",
  "responsibilities": ["<responsibility 1>", "<responsibility 2>"],
  "developer_hints": [],
  "child_refs": ["<child dir path>"],
  "static_analysis_limits": {"dynamic_imports": 0, "unresolved_paths": 0},
  "gap_summary": {"untested_edges": 0, "top_gaps": []}
}

Also produce a "test_stubs" list — syntactically valid pytest stub functions for \
the top untested seams (from gap entries). Each stub:
{
  "stub_file": "<suggested file path under .agent-docs/>",
  "content": "<full Python file content with imports, stub functions, TODO comments>"
}

Respond with valid JSON only. No markdown, no commentary.\
"""


class PromptBuilder:
    """Stateless builder that assembles LLM investigation prompts for a directory.

    No constructor arguments required. Each call to build() or estimate_tokens()
    is independent.
    """

    def build(
        self,
        directory: str,
        project_root: Path,
        file_graph,  # nx.DiGraph
        coverage_data: dict,
        agent_docs_root: Path,
        idk_mode: bool = False,
        prompt_angle: str = "default",
    ) -> tuple[list[dict], int]:
        """Assemble a full investigation prompt for a directory.

        Args:
            directory: Absolute or relative path to the directory to document.
            project_root: Project root directory (for relative path computation).
            file_graph: File-level NetworkX DiGraph from load_graph_from_json().
            coverage_data: Coverage dict from _test_coverage.json (may be empty).
            agent_docs_root: Root of the shadow tree (.agent-docs/).
            idk_mode: If True, include 2-hop neighbor file contents in the prompt.
            prompt_angle: Angle-specific prompt guidance ('default', 'integration',
                          or 'data_flow').

        Returns:
            Tuple of (messages, estimated_tokens):
                messages: List of dicts with 'role' and 'content' keys.
                          [system_message, user_message]
                estimated_tokens: Integer approximation of prompt token count.
                                  Returns 0 if directory not found.
        """
        dir_path = Path(directory)
        if not dir_path.is_absolute() and project_root:
            dir_path = Path(project_root) / dir_path

        if not dir_path.exists() or not dir_path.is_dir():
            return [], 0

        # 1. Collect files (skip hidden dirs and __pycache__)
        file_entries = self._collect_files(dir_path)

        # 2. Read file contents (full, no truncation — user locked decision)
        file_contents = self._read_file_contents(file_entries)

        # 3. Compute relative directory path for graph matching
        try:
            rel_dir = str(dir_path.relative_to(project_root))
        except ValueError:
            rel_dir = str(dir_path)

        # Normalize to forward slashes for graph lookup
        rel_dir_posix = rel_dir.replace("\\", "/")

        # 4. Extract graph edges for this directory
        inbound_edges, outbound_edges, is_entry_point = self._extract_graph_edges(
            file_graph, rel_dir_posix, dir_path, project_root
        )

        # 5. Extract gap entries involving this directory
        gap_entries = self._extract_gap_entries(coverage_data, rel_dir_posix, dir_path, project_root)

        # 6. Read child _dir.md summaries
        child_summaries = self._collect_child_summaries(dir_path, project_root, agent_docs_root)

        # 7. Read developer hints from existing _dir.md for this directory
        developer_hints = self._read_developer_hints(rel_dir_posix, agent_docs_root)

        # 8. Check if namespace package (only __init__.py)
        is_namespace = self._is_namespace_package(file_entries)

        # 9. IDK mode: collect 2-hop neighbor contents
        neighbor_contents: list[dict] = []
        if idk_mode:
            neighbor_contents = self._collect_neighbor_contents(
                rel_dir=rel_dir_posix,
                file_graph=file_graph,
                project_root=project_root,
                depth=2,
            )

        # 10. Assemble user message
        user_content = self._assemble_user_message(
            rel_dir=rel_dir_posix,
            file_contents=file_contents,
            inbound_edges=inbound_edges,
            outbound_edges=outbound_edges,
            is_entry_point=is_entry_point,
            gap_entries=gap_entries,
            child_summaries=child_summaries,
            developer_hints=developer_hints,
            is_namespace=is_namespace,
            neighbor_contents=neighbor_contents,
        )

        # 11. Build system prompt (with optional angle guidance)
        system_content = _SYSTEM_PROMPT
        if prompt_angle == "integration":
            system_content = (
                _SYSTEM_PROMPT
                + "\n\nFocus on integration patterns: how this directory communicates "
                "with other parts of the system, API boundaries, event flows, shared state."
            )
        elif prompt_angle == "data_flow":
            system_content = (
                _SYSTEM_PROMPT
                + "\n\nFocus on data flow: how data enters, transforms, and exits this "
                "directory. Trace input sources, processing steps, and output destinations."
            )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        # 12. Estimate tokens
        total_file_bytes = sum(len(fc["content"].encode("utf-8")) for fc in file_contents)
        estimated_tokens = self._estimate(
            file_bytes=total_file_bytes,
            edge_count=len(inbound_edges) + len(outbound_edges),
            gap_count=len(gap_entries),
            child_count=len(child_summaries),
        )

        return messages, estimated_tokens

    def estimate_tokens(
        self,
        directory: str,
        project_root: Path,
        file_graph,  # nx.DiGraph
        coverage_data: dict,
        agent_docs_root: Path,
    ) -> int:
        """Estimate token count for a directory without reading file contents.

        Uses file sizes from disk (faster than reading full contents) for
        pre-dispatch cost display.

        Args:
            directory: Absolute or relative path to the directory.
            project_root: Project root for relative path computation.
            file_graph: File-level NetworkX DiGraph.
            coverage_data: Coverage dict from _test_coverage.json.
            agent_docs_root: Root of the shadow tree.

        Returns:
            Integer token estimate. Returns 0 for missing/empty directories.
        """
        dir_path = Path(directory)

        if not dir_path.exists() or not dir_path.is_dir():
            return 0

        file_entries = self._collect_files(dir_path)

        # Sum file sizes in bytes (no reading needed)
        total_bytes = sum(p.stat().st_size for p in file_entries if p.is_file())

        try:
            rel_dir = str(dir_path.relative_to(project_root)).replace("\\", "/")
        except ValueError:
            rel_dir = str(dir_path)

        inbound_edges, outbound_edges, _ = self._extract_graph_edges(
            file_graph, rel_dir, dir_path, project_root
        )
        gap_entries = self._extract_gap_entries(coverage_data, rel_dir, dir_path, project_root)
        child_summaries = self._collect_child_summaries(dir_path, project_root, agent_docs_root)

        return self._estimate(
            file_bytes=total_bytes,
            edge_count=len(inbound_edges) + len(outbound_edges),
            gap_count=len(gap_entries),
            child_count=len(child_summaries),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _collect_files(self, dir_path: Path) -> list[Path]:
        """List source files in directory, skipping hidden files and __pycache__."""
        files: list[Path] = []
        try:
            for item in dir_path.iterdir():
                if item.name.startswith("."):
                    continue
                if item.name in _SKIP_HIDDEN:
                    continue
                if item.is_file():
                    files.append(item)
        except PermissionError as exc:
            logger.warning("permission denied reading directory", path=str(dir_path), error=str(exc))
        return sorted(files)

    def _read_file_contents(self, file_paths: list[Path]) -> list[dict]:
        """Read full content of each file. Skip binary or unreadable files."""
        contents: list[dict] = []
        for path in file_paths:
            try:
                text = path.read_text(encoding="utf-8")
                contents.append({"path": str(path), "name": path.name, "content": text})
            except UnicodeDecodeError:
                logger.warning("skipping binary file", path=str(path))
            except OSError as exc:
                logger.warning("could not read file", path=str(path), error=str(exc))
        return contents

    def _extract_graph_edges(
        self,
        file_graph,
        rel_dir: str,
        dir_path: Path,
        project_root: Path,
    ) -> tuple[list[str], list[str], bool]:
        """Extract inbound and outbound edges for files in this directory.

        Returns:
            Tuple of (inbound_edges, outbound_edges, is_entry_point):
                inbound_edges: File paths that import files in this directory.
                outbound_edges: File paths that this directory's files import.
                is_entry_point: True if any file in this dir is a graph entry point.
        """
        inbound: list[str] = []
        outbound: list[str] = []
        is_entry_point = False

        if file_graph is None or len(file_graph.nodes) == 0:
            return inbound, outbound, is_entry_point

        # Get file paths in this directory relative to project root
        dir_files: set[str] = set()
        try:
            for f in dir_path.iterdir():
                if f.is_file():
                    try:
                        rel = str(f.relative_to(project_root)).replace("\\", "/")
                        dir_files.add(rel)
                    except ValueError:
                        pass
        except (PermissionError, OSError):
            pass

        for node in file_graph.nodes():
            # Check if this node belongs to our directory
            node_posix = str(node).replace("\\", "/")
            node_dir = str(PurePosixPath(node_posix).parent)

            if node_dir == rel_dir or node_posix in dir_files:
                # Check entry point flag
                node_data = file_graph.nodes[node]
                if node_data.get("is_entry_point", False):
                    is_entry_point = True

                # Outbound: what this file imports
                for _, target in file_graph.out_edges(node):
                    target_str = str(target).replace("\\", "/")
                    if target_str not in outbound:
                        outbound.append(target_str)

                # Inbound: what imports this file
                for source, _ in file_graph.in_edges(node):
                    source_str = str(source).replace("\\", "/")
                    if source_str not in inbound:
                        inbound.append(source_str)

        return inbound, outbound, is_entry_point

    def _extract_gap_entries(
        self,
        coverage_data: dict,
        rel_dir: str,
        dir_path: Path,
        project_root: Path,
    ) -> list[dict]:
        """Extract gap entries involving files in this directory."""
        if not coverage_data:
            return []

        gaps = coverage_data.get("gaps", [])
        if not gaps:
            return []

        # Get file paths in this directory
        dir_files: set[str] = set()
        try:
            for f in dir_path.iterdir():
                if f.is_file():
                    try:
                        rel = str(f.relative_to(project_root)).replace("\\", "/")
                        dir_files.add(rel)
                    except ValueError:
                        pass
        except (PermissionError, OSError):
            pass

        relevant: list[dict] = []
        for gap in gaps:
            source = str(gap.get("source", "")).replace("\\", "/")
            target = str(gap.get("target", "")).replace("\\", "/")
            src_dir = str(PurePosixPath(source).parent)
            tgt_dir = str(PurePosixPath(target).parent)

            # Include gap if it involves files in this directory
            if src_dir == rel_dir or tgt_dir == rel_dir or source in dir_files or target in dir_files:
                relevant.append(gap)

        return relevant

    def _collect_child_summaries(
        self,
        dir_path: Path,
        project_root: Path,
        agent_docs_root: Path,
    ) -> list[dict]:
        """Collect summary + responsibilities from child _dir.md files.

        Only includes immediate child directories that have a _dir.md in the
        shadow tree. Returns summary and responsibilities only (user decision).
        """
        summaries: list[dict] = []

        if not agent_docs_root.exists():
            return summaries

        # Find child directories of this directory on disk
        try:
            child_dirs = [d for d in dir_path.iterdir() if d.is_dir() and not d.name.startswith(".")]
        except (PermissionError, OSError):
            return summaries

        for child_dir in sorted(child_dirs):
            # Compute shadow path for child directory
            try:
                child_rel = str(child_dir.relative_to(project_root)).replace("\\", "/")
            except ValueError:
                continue

            child_shadow = agent_docs_root / child_rel / "_dir.md"

            if not child_shadow.exists():
                continue

            try:
                doc = parse_dir_doc(child_shadow)
                summaries.append({
                    "directory": child_rel,
                    "summary": doc.summary,
                    "responsibilities": list(doc.responsibilities),
                })
            except Exception as exc:
                logger.warning(
                    "could not read child _dir.md",
                    path=str(child_shadow),
                    error=str(exc),
                )

        return summaries

    def _read_developer_hints(
        self,
        rel_dir: str,
        agent_docs_root: Path,
    ) -> list[str]:
        """Read developer_hints from existing _dir.md for this directory."""
        if not agent_docs_root.exists():
            return []

        dir_md = agent_docs_root / rel_dir / "_dir.md"
        if not dir_md.exists():
            return []

        try:
            doc = parse_dir_doc(dir_md)
            return list(doc.developer_hints)
        except Exception as exc:
            logger.warning(
                "could not read _dir.md for developer hints",
                path=str(dir_md),
                error=str(exc),
            )
            return []

    def _collect_neighbor_contents(
        self,
        rel_dir: str,
        file_graph,
        project_root: Path,
        depth: int = 2,
    ) -> list[dict]:
        """Collect file contents from neighbor directories via BFS up to `depth` hops.

        Follows edges in both directions (predecessors + successors) from all nodes
        in the target directory. Collects unique directories (excluding the target
        directory itself) reachable within `depth` hops.

        Args:
            rel_dir: Relative directory path (forward-slash) of the target directory.
            file_graph: File-level NetworkX DiGraph.
            project_root: Project root for resolving absolute paths.
            depth: Number of hops to traverse (default 2).

        Returns:
            List of dicts: [{"directory": rel_dir, "files": [{"name": ..., "content": ...}]}]
        """
        if file_graph is None or len(file_graph.nodes) == 0:
            return []

        # Collect all nodes belonging to the target directory
        start_nodes: list[str] = []
        for node in file_graph.nodes():
            node_posix = str(node).replace("\\", "/")
            node_dir = str(PurePosixPath(node_posix).parent)
            if node_dir == rel_dir:
                start_nodes.append(node_posix)

        if not start_nodes:
            return []

        # BFS to collect neighbor nodes
        visited_nodes: set[str] = set(start_nodes)
        frontier: list[str] = list(start_nodes)

        for _ in range(depth):
            next_frontier: list[str] = []
            for node in frontier:
                # Traverse in both directions
                for _, successor in file_graph.out_edges(node):
                    s = str(successor).replace("\\", "/")
                    if s not in visited_nodes:
                        visited_nodes.add(s)
                        next_frontier.append(s)
                for predecessor, _ in file_graph.in_edges(node):
                    p = str(predecessor).replace("\\", "/")
                    if p not in visited_nodes:
                        visited_nodes.add(p)
                        next_frontier.append(p)
            frontier = next_frontier

        # Collect unique neighbor directories (exclude target dir)
        neighbor_dirs: dict[str, list[Path]] = {}
        for node in visited_nodes:
            node_posix = str(node).replace("\\", "/")
            node_dir = str(PurePosixPath(node_posix).parent)
            if node_dir == rel_dir:
                continue
            # Resolve to absolute path
            abs_dir = project_root / node_dir
            if abs_dir.exists() and abs_dir.is_dir():
                if node_dir not in neighbor_dirs:
                    neighbor_dirs[node_dir] = []
                # Collect files for this neighbor directory
                file_path = project_root / node_posix
                if file_path.exists() and file_path.is_file():
                    neighbor_dirs[node_dir].append(file_path)

        result: list[dict] = []
        for neighbor_rel, file_paths in sorted(neighbor_dirs.items()):
            files = self._read_file_contents(sorted(set(file_paths)))
            if files:
                result.append({
                    "directory": neighbor_rel,
                    "files": [{"name": fc["name"], "content": fc["content"]} for fc in files],
                })

        return result

    def _is_namespace_package(self, file_entries: list[Path]) -> bool:
        """Return True if directory contains only an empty __init__.py."""
        names = [f.name for f in file_entries]
        if names == ["__init__.py"] or names == []:
            # Check if __init__.py is empty
            if names == ["__init__.py"]:
                try:
                    content = file_entries[0].read_text(encoding="utf-8").strip()
                    return len(content) == 0
                except OSError:
                    return False
            return True
        return False

    def _assemble_user_message(
        self,
        rel_dir: str,
        file_contents: list[dict],
        inbound_edges: list[str],
        outbound_edges: list[str],
        is_entry_point: bool,
        gap_entries: list[dict],
        child_summaries: list[dict],
        developer_hints: list[str],
        is_namespace: bool,
        neighbor_contents: list[dict] | None = None,
    ) -> str:
        """Assemble all context sections into a single user message string."""
        sections: list[str] = []

        # Header
        sections.append(f"# Directory: {rel_dir}")
        if is_entry_point:
            sections.append("**Note:** This directory contains one or more entry points.")
        if is_namespace:
            sections.append(
                "**Note:** This is a namespace package (empty __init__.py only). "
                "Document its role as a package namespace."
            )
        sections.append("")

        # Section 1: File Contents
        sections.append("## File Contents")
        if file_contents:
            for fc in file_contents:
                sections.append(f"\n### {fc['name']}")
                sections.append("```")
                sections.append(fc["content"])
                sections.append("```")
        else:
            sections.append("(No source files found in this directory)")
        sections.append("")

        # Section 2: Graph Context
        sections.append("## Dependency Graph Context")
        if inbound_edges:
            sections.append("\n**Inbound edges** (files that import from this directory):")
            for edge in inbound_edges:
                sections.append(f"- {edge}")
        else:
            sections.append("\n**Inbound edges:** None (no other files import from this directory)")

        if outbound_edges:
            sections.append("\n**Outbound edges** (files this directory imports):")
            for edge in outbound_edges:
                sections.append(f"- {edge}")
        else:
            sections.append("\n**Outbound edges:** None (this directory imports nothing)")
        sections.append("")

        # Section 3: Coverage Gaps
        sections.append("## Test Coverage Gaps")
        if gap_entries:
            sections.append(f"Found {len(gap_entries)} untested seam(s) involving this directory:")
            for gap in gap_entries:
                source = gap.get("source", "")
                target = gap.get("target", "")
                centrality = gap.get("centrality", 0.0)
                annotation = gap.get("annotation", "")
                sections.append(f"- {source} -> {target} (centrality: {centrality:.3f})")
                if annotation:
                    sections.append(f"  {annotation}")
        else:
            sections.append("No coverage gaps found for this directory.")
        sections.append("")

        # Section 4: Child Directory Summaries
        sections.append("## Child Directory Summaries")
        if child_summaries:
            for child in child_summaries:
                sections.append(f"\n### {child['directory']}")
                sections.append(child["summary"])
                if child["responsibilities"]:
                    sections.append("\nKey responsibilities:")
                    for resp in child["responsibilities"]:
                        sections.append(f"- {resp}")
        else:
            sections.append("No child directories with existing documentation.")
        sections.append("")

        # Section 5: Developer Hints
        sections.append("## Developer Hints")
        if developer_hints:
            for hint in developer_hints:
                sections.append(f"- {hint}")
        else:
            sections.append("No developer hints provided.")
        sections.append("")

        # Section 6: Neighboring Directory Context (IDK mode only)
        if neighbor_contents:
            sections.append("## Neighboring Directory Context")
            for neighbor in neighbor_contents:
                sections.append(f"\n### {neighbor['directory']}")
                for fc in neighbor.get("files", []):
                    sections.append(f"\n#### {fc['name']}")
                    sections.append("```")
                    sections.append(fc["content"])
                    sections.append("```")
            sections.append("")

        return "\n".join(sections)

    def _estimate(
        self,
        file_bytes: int,
        edge_count: int,
        gap_count: int,
        child_count: int,
    ) -> int:
        """Compute token estimate from RESEARCH.md formula.

        formula:
            (total_file_bytes / 3.5) + (graph_edges * 30) + (gap_entries * 50)
            + (child_summaries * 200) + PROMPT_OVERHEAD
        """
        return int(
            (file_bytes / 3.5)
            + (edge_count * _TOKENS_PER_EDGE)
            + (gap_count * _TOKENS_PER_GAP)
            + (child_count * _TOKENS_PER_CHILD_SUMMARY)
            + _PROMPT_OVERHEAD
        )
