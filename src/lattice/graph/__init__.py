"""Lattice graph package — dependency graph construction, annotation, and serialization.

Public exports:
    DependencyGraphBuilder — builds NetworkX DiGraph from FileAnalysis results
    EntryPointDetector     — annotates graph nodes with entry point metadata
    ConfigWiringDetector   — adds config file nodes and config_ref edges
    serialize_graph        — serializes graph to _graph.json-compatible dict
"""
from lattice.graph.builder import DependencyGraphBuilder
from lattice.graph.config_wiring import ConfigWiringDetector
from lattice.graph.entry_points import EntryPointDetector
from lattice.graph.serializer import serialize_graph

__all__ = [
    "DependencyGraphBuilder",
    "ConfigWiringDetector",
    "EntryPointDetector",
    "serialize_graph",
]
