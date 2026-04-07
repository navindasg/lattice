"""Soul ecosystem: persistent orchestrator identity, procedures, state, and memory.

This subpackage manages four markdown files that define the orchestrator's
persistent context across sessions:

- SOUL.md     — identity, mission, personality (human-owned)
- AGENTS.md   — approval rules, work assignment procedures (human-owned)
- STATE.md    — live orchestrator state: instances, plan, decisions, blockers
- MEMORY.md   — durable cross-session facts and learned patterns

SoulReader reads and assembles these files into structured context.
SoulWriter provides atomic writes to STATE.md and MEMORY.md.

All models are frozen Pydantic models (immutable after construction).
"""
from lattice.orchestrator.soul_ecosystem.compaction import (
    post_compaction_restore,
    pre_compaction_flush,
)
from lattice.orchestrator.soul_ecosystem.models import (
    DecisionRecord,
    InstanceAssignment,
    OrchestratorState,
    SoulContext,
    SoulMemoryEntry,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.soul_ecosystem.writer import SoulWriter

__all__ = [
    "DecisionRecord",
    "InstanceAssignment",
    "OrchestratorState",
    "SoulContext",
    "SoulMemoryEntry",
    "SoulReader",
    "SoulWriter",
    "post_compaction_restore",
    "pre_compaction_flush",
]
