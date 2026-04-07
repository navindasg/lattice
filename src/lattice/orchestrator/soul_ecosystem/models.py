"""Structured models for soul ecosystem file sections.

All models are frozen Pydantic models (immutable after construction).
OrchestratorState supports round-trip serialization to/from STATE.md markdown.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field


class InstanceAssignment(BaseModel):
    """Which CC instance is working on what."""

    instance_id: str
    task_description: str
    status: str  # "active", "idle", "blocked"
    assigned_at: str

    model_config = {"frozen": True}


class DecisionRecord(BaseModel):
    """A recorded approval/denial decision."""

    timestamp: str
    event_type: str  # "approve", "deny"
    target: str  # what was approved/denied
    reason: str | None = None

    model_config = {"frozen": True}


class SoulMemoryEntry(BaseModel):
    """A durable memory entry with ISO timestamp and category."""

    timestamp: str  # ISO 8601
    category: str  # e.g., "preference", "convention", "pattern", "decision"
    content: str

    model_config = {"frozen": True}


class OrchestratorState(BaseModel):
    """Structured STATE.md content with named sections.

    Supports round-trip serialization: to_markdown() renders the state as
    markdown with ## sections, and from_markdown() parses it back.
    """

    instances: list[InstanceAssignment] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}

    def to_markdown(self) -> str:
        """Render state as markdown with ## sections."""
        sections: list[str] = []

        # Instances section
        lines: list[str] = ["## Instances"]
        if self.instances:
            for inst in self.instances:
                lines.append(
                    f"- **{inst.instance_id}**: {inst.task_description} "
                    f"({inst.status}, assigned {inst.assigned_at})"
                )
        else:
            lines.append("_No active instances_")
        sections.append("\n".join(lines))

        # Plan section
        lines = ["## Plan"]
        if self.plan:
            for item in self.plan:
                lines.append(f"- {item}")
        else:
            lines.append("_No current plan_")
        sections.append("\n".join(lines))

        # Decisions section
        lines = ["## Decisions"]
        if self.decisions:
            for dec in self.decisions:
                reason = f" — {dec.reason}" if dec.reason else ""
                lines.append(
                    f"- [{dec.timestamp}] {dec.event_type}: {dec.target}{reason}"
                )
        else:
            lines.append("_No recent decisions_")
        sections.append("\n".join(lines))

        # Blockers section
        lines = ["## Blockers"]
        if self.blockers:
            for blocker in self.blockers:
                lines.append(f"- {blocker}")
        else:
            lines.append("_No blockers_")
        sections.append("\n".join(lines))

        return "\n\n".join(sections)

    @classmethod
    def from_markdown(cls, text: str) -> OrchestratorState:
        """Parse STATE.md markdown back into structured state.

        Handles both populated and empty/placeholder sections.

        Args:
            text: Markdown string with ## Instances, ## Plan, ## Decisions, ## Blockers.

        Returns:
            Parsed OrchestratorState instance.
        """
        section_pattern = re.compile(
            r"## (.+?)\n(.*?)(?=\n## |\Z)", re.DOTALL
        )
        section_map: dict[str, str] = {}
        for match in section_pattern.finditer(text):
            section_map[match.group(1).strip()] = match.group(2).strip()

        instances = _parse_instances(section_map.get("Instances", ""))
        plan = _parse_plan(section_map.get("Plan", ""))
        decisions = _parse_decisions(section_map.get("Decisions", ""))
        blockers = _parse_blockers(section_map.get("Blockers", ""))

        return cls(
            instances=instances,
            plan=plan,
            decisions=decisions,
            blockers=blockers,
        )


def _parse_instances(text: str) -> list[InstanceAssignment]:
    """Parse instance assignment lines from markdown."""
    if not text or text.startswith("_"):
        return []

    results: list[InstanceAssignment] = []
    pattern = re.compile(
        r"- \*\*(.+?)\*\*: (.+?) \((\w+), assigned (.+?)\)"
    )
    for match in pattern.finditer(text):
        results.append(
            InstanceAssignment(
                instance_id=match.group(1),
                task_description=match.group(2),
                status=match.group(3),
                assigned_at=match.group(4),
            )
        )
    return results


def _parse_plan(text: str) -> list[str]:
    """Parse plan items from markdown bullet list."""
    if not text or text.startswith("_"):
        return []
    return [
        line[2:] for line in text.splitlines() if line.startswith("- ")
    ]


def _parse_decisions(text: str) -> list[DecisionRecord]:
    """Parse decision records from markdown."""
    if not text or text.startswith("_"):
        return []

    results: list[DecisionRecord] = []
    pattern = re.compile(
        r"- \[(.+?)\] (\w+): (.+?)(?:\s—\s(.+))?$", re.MULTILINE
    )
    for match in pattern.finditer(text):
        results.append(
            DecisionRecord(
                timestamp=match.group(1),
                event_type=match.group(2),
                target=match.group(3).strip(),
                reason=match.group(4),
            )
        )
    return results


def _parse_blockers(text: str) -> list[str]:
    """Parse blocker items from markdown bullet list."""
    if not text or text.startswith("_"):
        return []
    return [
        line[2:] for line in text.splitlines() if line.startswith("- ")
    ]


class SoulContext(BaseModel):
    """Complete soul context returned by SoulReader.read_all()."""

    soul: str  # SOUL.md content
    agents: str  # AGENTS.md content
    state: str  # STATE.md content
    memory: str  # MEMORY.md content

    model_config = {"frozen": True}
