"""Shadow tree module — _dir.md schema, staleness detection, reader, writer.

Re-exports all public symbols so callers can use:
    from lattice.shadow import DirDoc, write_dir_doc, traverse, is_stale
"""
from lattice.shadow.reader import parse_dir_doc, traverse
from lattice.shadow.schema import (
    ConfidenceSource,
    DirDoc,
    GapSummary,
    StaticAnalysisLimits,
)
from lattice.shadow.staleness import is_stale, last_git_commit_time
from lattice.shadow.writer import write_dir_doc

__all__ = [
    "ConfidenceSource",
    "DirDoc",
    "GapSummary",
    "StaticAnalysisLimits",
    "is_stale",
    "last_git_commit_time",
    "parse_dir_doc",
    "traverse",
    "write_dir_doc",
]
