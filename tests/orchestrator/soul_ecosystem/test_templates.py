"""Unit tests for soul ecosystem templates.

Tests cover:
- All templates are non-empty strings
- Templates contain expected markdown headers
- STATE_TEMPLATE contains all 4 sections
"""
from __future__ import annotations

from lattice.orchestrator.soul_ecosystem.templates import (
    AGENTS_TEMPLATE,
    MEMORY_TEMPLATE,
    SOUL_TEMPLATE,
    STATE_TEMPLATE,
)


class TestTemplatesNonEmpty:
    """All templates are non-empty strings."""

    def test_soul_template_non_empty(self):
        assert isinstance(SOUL_TEMPLATE, str)
        assert len(SOUL_TEMPLATE) > 0

    def test_agents_template_non_empty(self):
        assert isinstance(AGENTS_TEMPLATE, str)
        assert len(AGENTS_TEMPLATE) > 0

    def test_state_template_non_empty(self):
        assert isinstance(STATE_TEMPLATE, str)
        assert len(STATE_TEMPLATE) > 0

    def test_memory_template_non_empty(self):
        assert isinstance(MEMORY_TEMPLATE, str)
        assert len(MEMORY_TEMPLATE) > 0


class TestTemplatesValidMarkdown:
    """Templates contain expected markdown headers."""

    def test_soul_template_has_mission(self):
        assert "## Mission" in SOUL_TEMPLATE

    def test_soul_template_has_personality(self):
        assert "## Personality" in SOUL_TEMPLATE

    def test_soul_template_has_boundaries(self):
        assert "## Boundaries" in SOUL_TEMPLATE

    def test_agents_template_has_approval_rules(self):
        assert "## Approval Rules" in AGENTS_TEMPLATE

    def test_agents_template_has_work_assignment(self):
        assert "## Work Assignment" in AGENTS_TEMPLATE

    def test_agents_template_has_escalation(self):
        assert "## Escalation" in AGENTS_TEMPLATE

    def test_memory_template_has_header(self):
        assert "# Orchestrator Memory" in MEMORY_TEMPLATE


class TestStateTemplateSections:
    """STATE_TEMPLATE contains all 4 required sections."""

    def test_has_instances_section(self):
        assert "## Instances" in STATE_TEMPLATE

    def test_has_plan_section(self):
        assert "## Plan" in STATE_TEMPLATE

    def test_has_decisions_section(self):
        assert "## Decisions" in STATE_TEMPLATE

    def test_has_blockers_section(self):
        assert "## Blockers" in STATE_TEMPLATE
