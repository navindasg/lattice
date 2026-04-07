"""Context compaction lifecycle for the soul ecosystem.

Provides flush/restore functions to persist in-context state before
compaction and re-read it afterward.
"""
from __future__ import annotations

import structlog

from lattice.orchestrator.soul_ecosystem.models import (
    DecisionRecord,
    InstanceAssignment,
    OrchestratorState,
    SoulContext,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.soul_ecosystem.writer import SoulWriter

logger = structlog.get_logger(__name__)


def pre_compaction_flush(
    writer: SoulWriter,
    context: dict,
) -> None:
    """Flush all in-context state to STATE.md before compaction.

    Called before context compaction to persist:
    - Instance assignments
    - Pending decisions
    - Current plan
    - Active blockers

    Args:
        writer: SoulWriter instance for atomic writes.
        context: Dict with keys matching OrchestratorState fields.
                 Expected keys: "instances", "plan", "decisions", "blockers".
                 Missing keys default to empty lists.
    """
    instances_raw = context.get("instances", [])
    instances = [
        item if isinstance(item, InstanceAssignment)
        else InstanceAssignment(**item)
        for item in instances_raw
    ]

    decisions_raw = context.get("decisions", [])
    decisions = [
        item if isinstance(item, DecisionRecord)
        else DecisionRecord(**item)
        for item in decisions_raw
    ]

    state = OrchestratorState(
        instances=instances,
        plan=context.get("plan", []),
        decisions=decisions,
        blockers=context.get("blockers", []),
    )

    logger.info(
        "soul_ecosystem.pre_compaction_flush",
        instances=len(state.instances),
        plan_items=len(state.plan),
        decisions=len(state.decisions),
        blockers=len(state.blockers),
    )
    writer.update_full_state(state)


def post_compaction_restore(reader: SoulReader) -> SoulContext:
    """Re-read all soul files after compaction to reconstruct context.

    Returns the full SoulContext for re-injection into the LLM.

    Args:
        reader: SoulReader instance for reading soul files.

    Returns:
        SoulContext with all four soul file contents.
    """
    logger.info("soul_ecosystem.post_compaction_restore")
    return reader.read_all()
