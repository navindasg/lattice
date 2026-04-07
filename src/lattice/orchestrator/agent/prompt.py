"""System prompt assembly for the orchestrator agent.

Builds the system prompt from soul files (SOUL.md, AGENTS.md, STATE.md)
plus tool descriptions. The prompt establishes the agent's identity,
capabilities, and constraints.
"""
from __future__ import annotations

from lattice.orchestrator.soul_ecosystem.reader import SoulReader


_CORE_DIRECTIVE = """\
You are the Lattice orchestrator — an AI project manager that coordinates \
multiple Claude Code (CC) worker instances. You do NOT write code yourself. \
You reason about what needs to be done, assign work to CC instances, handle \
tool-use approvals, and manage project state.

## Your Capabilities
- cc_send: Send prompts to CC instances
- cc_approve / cc_deny: Handle tool-use approval requests
- cc_status: Check what instances are working on
- cc_spawn: Start new CC instances for parallel work
- cc_interrupt: Stop a runaway instance
- github_read: Fetch issue/PR details for context
- soul_read / soul_update: Read and update persistent state
- map_query: Query codebase intelligence
- write_todos: Plan and track work items

## Core Rules
1. NEVER generate code — always delegate to a CC instance
2. When a PreToolUse approval event arrives, reason about it:
   - Check STATE.md for the instance's assignment
   - Consider whether the tool and file path are expected
   - Approve if consistent, deny with redirect if not
3. Log all approval decisions to STATE.md ## Decisions with timestamp and rationale
4. Keep STATE.md ## Instances section current — update on spawn, completion, errors
5. When given freeform instructions, create a plan via write_todos before assigning work
6. Handle concurrent instances without confusion — events from instance 3 never trigger actions on instance 4
"""


def build_system_prompt(soul_reader: SoulReader) -> str:
    """Assemble the full system prompt for the orchestrator agent.

    Combines:
    1. Core directive (hardcoded rules and capabilities)
    2. SOUL.md (identity and mission)
    3. AGENTS.md (approval rules and procedures)
    4. STATE.md (current live state)

    MEMORY.md is excluded (too large for context window).

    Args:
        soul_reader: SoulReader instance for accessing soul files.

    Returns:
        Complete system prompt string.
    """
    soul_context = soul_reader.build_system_prompt()

    return f"""{_CORE_DIRECTIVE}

{soul_context}
"""
