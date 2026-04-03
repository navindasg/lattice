"""Cross-cutting integration analysis subdomain.

Detects project-level patterns that span directory boundaries:
event flows, shared state, API contracts, and plugin points.

Public API (re-exported from submodules):
  schema:    ProjectDoc, EventFlow, SharedState, ApiContract,
             PluginPoint, CrossCuttingBlindSpot
  detectors: EventFlowDetector, SharedStateDetector,
             ApiContractDetector, PluginPointDetector
  writer:    write_project_doc, parse_project_doc
  analyzer:  CrossCuttingAnalyzer, build_cross_cutting_edges,
             compute_cross_cutting_refs, enrich_dir_docs_if_present
"""
from lattice.cross_cutting.analyzer import (
    CrossCuttingAnalyzer,
    build_cross_cutting_edges,
    compute_cross_cutting_refs,
    enrich_dir_docs_if_present,
)
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
from lattice.cross_cutting.writer import parse_project_doc, write_project_doc

__all__ = [
    "ApiContract",
    "ApiContractDetector",
    "CrossCuttingAnalyzer",
    "CrossCuttingBlindSpot",
    "EventFlow",
    "EventFlowDetector",
    "PluginPoint",
    "PluginPointDetector",
    "ProjectDoc",
    "SharedState",
    "SharedStateDetector",
    "build_cross_cutting_edges",
    "compute_cross_cutting_refs",
    "enrich_dir_docs_if_present",
    "parse_project_doc",
    "write_project_doc",
]
