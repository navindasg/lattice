"""DependencyGraphBuilder — constructs a NetworkX DiGraph from FileAnalysis results.

Each analyzed file becomes a graph node keyed by its relative path. Directed edges
represent internal, non-dynamic import relationships between modules.

Edge filtering rules:
- External imports (is_external=True) are NOT edges
- Dynamic imports (import_type="dynamic") are NOT edges
- Imports without a resolved_path are NOT edges
- Imports whose resolved_path does not match a known node are NOT edges

Node attributes:
    language          str   — file language (python / typescript / javascript)
    is_entry_point    bool  — False by default; annotated by EntryPointDetector
    entry_point_type  str|None — None by default; annotated by EntryPointDetector
    entry_details     dict|None — None by default; annotated by EntryPointDetector
    exports           list[str] — top-level exported names from FileAnalysis
    has_main_guard    bool  — True when file has `if __name__ == "__main__"` guard

Edge attributes:
    import_type  str — ImportType value from ImportInfo
"""
from pathlib import Path

import networkx as nx

from lattice.models.analysis import FileAnalysis


class DependencyGraphBuilder:
    """Builds a NetworkX DiGraph from a list of FileAnalysis results.

    Usage::

        builder = DependencyGraphBuilder()
        graph = builder.build(analyses, project_root)
    """

    def build(self, analyses: list[FileAnalysis], project_root: Path) -> nx.DiGraph:
        """Construct a directed dependency graph from FileAnalysis results.

        Args:
            analyses: List of FileAnalysis objects, one per source file.
            project_root: Absolute path to the project root directory. Used to
                compute relative node keys when FileAnalysis.path is absolute.

        Returns:
            A NetworkX DiGraph with files as nodes and import relationships as
            directed edges (source -> dependency).
        """
        graph: nx.DiGraph = nx.DiGraph()

        # --- Phase 1: add all files as nodes ---
        known_nodes: set[str] = set()
        for analysis in analyses:
            node_key = self._node_key(analysis.path, project_root)
            graph.add_node(
                node_key,
                language=analysis.language,
                is_entry_point=False,
                entry_point_type=None,
                entry_details=None,
                exports=list(analysis.exports),
                has_main_guard=analysis.has_main_guard,
            )
            known_nodes.add(node_key)

        # --- Phase 2: add edges for internal, non-dynamic imports ---
        for analysis in analyses:
            source_key = self._node_key(analysis.path, project_root)
            for imp in analysis.imports:
                # Skip external imports — they have no graph node
                if imp.is_external:
                    continue
                # Skip dynamic imports — they become blind_spots, not edges
                if imp.import_type == "dynamic":
                    continue
                # Skip imports without a resolved path
                if imp.resolved_path is None:
                    continue
                # Skip imports to unknown nodes (not in the analysis set)
                target_key = self._normalise_path(imp.resolved_path, project_root)
                if target_key not in known_nodes:
                    continue

                graph.add_edge(source_key, target_key, import_type=imp.import_type)

        return graph

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_key(path: str, project_root: Path) -> str:
        """Return a project-relative path string to use as node key.

        If path is already relative (no leading /), return as-is.
        If path is absolute, attempt to relativise against project_root.
        """
        p = Path(path)
        if p.is_absolute():
            try:
                return str(p.relative_to(project_root))
            except ValueError:
                return path
        return path

    @staticmethod
    def _normalise_path(path: str, project_root: Path) -> str:
        """Normalise an import resolved_path to a node key."""
        p = Path(path)
        if p.is_absolute():
            try:
                return str(p.relative_to(project_root))
            except ValueError:
                return path
        return path
