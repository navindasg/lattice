"""Cross-cutting analysis orchestrator.

CrossCuttingAnalyzer coordinates the four detector classes across all source
files, joins per-file event producer/consumer tuples into full EventFlow chains,
resolves SharedState consumer_modules from the _graph.json import edges, and
assembles the results into a complete ProjectDoc.

Helper functions:
    build_cross_cutting_edges   — converts ProjectDoc to _graph.json edge dicts
    compute_cross_cutting_refs  — computes ref strings for a given directory
    enrich_dir_docs_if_present  — conditionally enriches existing _dir.md files
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import structlog

from lattice.cross_cutting.detectors import (
    ApiContractDetector,
    EventFlowDetector,
    PluginPointDetector,
    SharedStateDetector,
)
from lattice.cross_cutting.schema import (
    ApiContract,
    CrossCuttingBlindSpot,
    EventFlow,
    PluginPoint,
    ProjectDoc,
    SharedState,
)

logger = structlog.get_logger()


class CrossCuttingAnalyzer:
    """Orchestrates cross-cutting pattern analysis across a full project.

    Runs all four detectors on every source file, then joins per-file
    results into project-level artifacts:
    - EventFlow: matched by event_name across producers and consumers
    - SharedState: consumer_modules resolved from _graph.json import edges
    - ApiContract: collected directly from each file
    - PluginPoint: collected directly from each file

    Usage::

        analyzer = CrossCuttingAnalyzer(project_root)
        doc = analyzer.analyze(graph_data, source_files)
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._event_detector = EventFlowDetector()
        self._state_detector = SharedStateDetector()
        self._api_detector = ApiContractDetector()
        self._plugin_detector = PluginPointDetector()

    def analyze(self, graph_data: dict, source_files: list[Path]) -> ProjectDoc:
        """Run cross-cutting analysis across all source files.

        Iterates source_files, parses each with ast.parse(), delegates to
        detectors, then joins results into a complete ProjectDoc.

        SyntaxError and OSError on individual files are non-fatal: logged as
        warnings and skipped.

        Args:
            graph_data: Parsed _graph.json dict (metadata, nodes, edges).
            source_files: List of absolute source file paths to analyze.

        Returns:
            Validated ProjectDoc with joined cross-cutting analysis results.
        """
        # Accumulate per-file results
        all_producers: list[tuple[str, int, str]] = []  # (event_name, line, rel_path)
        all_consumers: list[tuple[str, int, str]] = []  # (event_name, line, rel_path)
        all_blind_spots: list[CrossCuttingBlindSpot] = []
        all_shared_state: list[SharedState] = []
        all_api_contracts: list[ApiContract] = []
        all_plugin_points: list[PluginPoint] = []

        for file_path in source_files:
            try:
                source = file_path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except SyntaxError as exc:
                logger.warning(
                    "skipping file with syntax error",
                    path=str(file_path),
                    error=str(exc),
                )
                continue
            except OSError as exc:
                logger.warning(
                    "skipping unreadable file",
                    path=str(file_path),
                    error=str(exc),
                )
                continue

            try:
                rel_path = str(file_path.relative_to(self._project_root))
            except ValueError:
                rel_path = str(file_path)

            # Event flow detection
            event_result = self._event_detector.detect(tree, rel_path)
            for event_name, line in event_result.producers:
                all_producers.append((event_name, line, rel_path))
            for event_name, line in event_result.consumers:
                all_consumers.append((event_name, line, rel_path))
            all_blind_spots.extend(event_result.blind_spots)

            # Shared state detection
            all_shared_state.extend(self._state_detector.detect(tree, rel_path))

            # API contract detection
            all_api_contracts.extend(self._api_detector.detect(tree, rel_path))

            # Plugin point detection
            all_plugin_points.extend(self._plugin_detector.detect(tree, rel_path))

        # Join event flows: match producers to consumers by event_name
        event_flows = self._join_event_flows(all_producers, all_consumers)

        # Resolve SharedState consumer_modules from graph edges
        resolved_shared_state = self._resolve_shared_state_consumers(
            all_shared_state, graph_data
        )

        return ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            event_flows=event_flows,
            shared_state=resolved_shared_state,
            api_contracts=all_api_contracts,
            plugin_points=all_plugin_points,
            blind_spots=all_blind_spots,
        )

    def _join_event_flows(
        self,
        producers: list[tuple[str, int, str]],
        consumers: list[tuple[str, int, str]],
    ) -> list[EventFlow]:
        """Join per-file producer/consumer tuples into EventFlow pairs.

        For each matching (producer, consumer) pair by event_name, creates one
        EventFlow. Unmatched producers or consumers are valid — external
        framework may handle delivery.

        Args:
            producers: List of (event_name, line, relative_path) tuples.
            consumers: List of (event_name, line, relative_path) tuples.

        Returns:
            List of EventFlow instances.
        """
        # Build lookup: event_name -> list of (line, path)
        producer_map: dict[str, list[tuple[int, str]]] = {}
        for event_name, line, path in producers:
            producer_map.setdefault(event_name, []).append((line, path))

        consumer_map: dict[str, list[tuple[int, str]]] = {}
        for event_name, line, path in consumers:
            consumer_map.setdefault(event_name, []).append((line, path))

        flows: list[EventFlow] = []
        # Create flows for every matched producer-consumer pair
        for event_name, prod_entries in producer_map.items():
            cons_entries = consumer_map.get(event_name, [])
            for prod_line, prod_path in prod_entries:
                if cons_entries:
                    for cons_line, cons_path in cons_entries:
                        flows.append(
                            EventFlow(
                                event_name=event_name,
                                producer_module=prod_path,
                                consumer_module=cons_path,
                                pattern_type="event_emitter",
                                producer_line=prod_line,
                                consumer_line=cons_line,
                            )
                        )

        return flows

    def _resolve_shared_state_consumers(
        self,
        shared_states: list[SharedState],
        graph_data: dict,
    ) -> list[SharedState]:
        """Populate SharedState.consumer_modules from _graph.json import edges.

        For each SharedState, finds all graph edges where source == owner_module
        and maps them to consumer modules that exist as graph nodes.

        Per Pitfall 4: uses model_copy(update={...}) on frozen model to set
        consumer_modules without mutation.

        Args:
            shared_states: SharedState instances with empty consumer_modules.
            graph_data: Parsed _graph.json dict with nodes and edges.

        Returns:
            List of SharedState instances with consumer_modules populated.
        """
        # Build node set from graph
        node_ids: set[str] = {n["id"] for n in graph_data.get("nodes", [])}

        # Build edge map: source -> list of targets
        edge_map: dict[str, list[str]] = {}
        for edge in graph_data.get("edges", []):
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            edge_map.setdefault(src, []).append(tgt)

        resolved: list[SharedState] = []
        for state in shared_states:
            # Find all modules that import from the owner_module
            consumers = [
                tgt
                for tgt in edge_map.get(state.owner_module, [])
                if tgt in node_ids
            ]
            if consumers:
                resolved.append(state.model_copy(update={"consumer_modules": consumers}))
            else:
                resolved.append(state)

        return resolved


def build_cross_cutting_edges(doc: ProjectDoc) -> list[dict]:
    """Convert a ProjectDoc into _graph.json cross_cutting_edges format.

    Each EventFlow produces one edge with edge_type="event_flow".
    Each SharedState produces one edge per consumer with edge_type="shared_state".
    Each PluginPoint produces one edge with edge_type="plugin_registration".

    Args:
        doc: Completed ProjectDoc from CrossCuttingAnalyzer.analyze().

    Returns:
        List of edge dicts compatible with _graph.json cross_cutting_edges section.
    """
    edges: list[dict] = []

    for flow in doc.event_flows:
        edges.append({
            "source": flow.producer_module,
            "target": flow.consumer_module,
            "type": "event_flow",
            "label": flow.event_name,
        })

    for state in doc.shared_state:
        for consumer in state.consumer_modules:
            edges.append({
                "source": state.owner_module,
                "target": consumer,
                "type": "shared_state",
                "label": state.object_name,
            })

    for plugin in doc.plugin_points:
        edges.append({
            "source": plugin.target_module,
            "target": plugin.group,
            "type": "plugin_registration",
            "label": plugin.name,
        })

    return edges


def compute_cross_cutting_refs(doc: ProjectDoc, directory: str) -> list[str]:
    """Compute cross-cutting ref strings for a directory.

    Returns lightweight ref strings indicating which cross-cutting patterns
    the directory participates in. Format:
    - "event:<name>:producer"
    - "event:<name>:consumer"
    - "state:<name>:owner"
    - "state:<name>:consumer"
    - "api:<method>:<path>"
    - "plugin:<group>:<name>"

    Args:
        doc: Completed ProjectDoc from CrossCuttingAnalyzer.analyze().
        directory: Directory path to compute refs for (relative to project root).

    Returns:
        List of ref strings for the directory.
    """
    refs: list[str] = []

    for flow in doc.event_flows:
        if flow.producer_module.startswith(directory + "/") or flow.producer_module == directory:
            refs.append(f"event:{flow.event_name}:producer")
        if flow.consumer_module.startswith(directory + "/") or flow.consumer_module == directory:
            refs.append(f"event:{flow.event_name}:consumer")

    for state in doc.shared_state:
        if state.owner_module.startswith(directory + "/") or state.owner_module == directory:
            refs.append(f"state:{state.object_name}:owner")
        for consumer in state.consumer_modules:
            if consumer.startswith(directory + "/") or consumer == directory:
                refs.append(f"state:{state.object_name}:consumer")

    for contract in doc.api_contracts:
        if (
            contract.handler_module.startswith(directory + "/")
            or contract.handler_module == directory
        ):
            refs.append(f"api:{contract.method}:{contract.path}")

    for plugin in doc.plugin_points:
        if (
            plugin.target_module.startswith(directory + "/")
            or plugin.target_module == directory
        ):
            refs.append(f"plugin:{plugin.group}:{plugin.name}")

    return refs


def enrich_dir_docs_if_present(doc: ProjectDoc, agent_docs_root: Path) -> int:
    """Enrich existing _dir.md files with cross_cutting_refs.

    Reads existing _dir.md files under agent_docs_root, computes cross-cutting
    refs for each directory, and writes back an enriched DirDoc via write_dir_doc().

    Checks file existence before parsing (per Pitfall 6 — non-existent files
    are skipped gracefully with a debug log).

    Args:
        doc: Completed ProjectDoc from CrossCuttingAnalyzer.analyze().
        agent_docs_root: Root of the .agent-docs shadow tree.

    Returns:
        Count of _dir.md files successfully enriched.
    """
    from lattice.shadow.reader import parse_dir_doc
    from lattice.shadow.writer import write_dir_doc

    enriched_count = 0

    for dir_md_path in agent_docs_root.rglob("_dir.md"):
        if not dir_md_path.exists():
            logger.debug(
                "skipping non-existent _dir.md",
                path=str(dir_md_path),
            )
            continue

        try:
            dir_doc = parse_dir_doc(dir_md_path)
        except Exception as exc:
            logger.warning(
                "skipping corrupt _dir.md during enrichment",
                path=str(dir_md_path),
                error=str(exc),
            )
            continue

        refs = compute_cross_cutting_refs(doc, dir_doc.directory)
        if refs:
            enriched_doc = dir_doc.model_copy(update={"cross_cutting_refs": refs})
            try:
                write_dir_doc(enriched_doc, agent_docs_root)
                enriched_count += 1
            except Exception as exc:
                logger.warning(
                    "failed to write enriched _dir.md",
                    path=str(dir_md_path),
                    error=str(exc),
                )

    return enriched_count
