"""Tests for hook constants and definitions."""
from lattice.orchestrator.hooks.constants import (
    CC_HOOK_DEFINITIONS,
    HOOK_EVENT_TYPES,
    LATTICE_HOOK_MARKER,
)


class TestHookConstants:
    def test_six_event_types_defined(self) -> None:
        """All 6 target event types are defined."""
        assert len(HOOK_EVENT_TYPES) == 6

    def test_required_event_types_present(self) -> None:
        """SessionStart, PreToolUse, PostToolUse, Stop, PreCompact, TaskCompleted."""
        expected = {
            "SessionStart",
            "PreToolUse",
            "PostToolUse",
            "Stop",
            "PreCompact",
            "TaskCompleted",
        }
        assert HOOK_EVENT_TYPES == expected

    def test_six_hook_definitions(self) -> None:
        """One HookDefinition per event type."""
        assert len(CC_HOOK_DEFINITIONS) == 6

    def test_each_event_type_has_definition(self) -> None:
        """Every event type in HOOK_EVENT_TYPES has a matching HookDefinition."""
        defined_types = {d.event_type for d in CC_HOOK_DEFINITIONS}
        assert defined_types == HOOK_EVENT_TYPES

    def test_pre_tool_use_is_synchronous(self) -> None:
        """PreToolUse hook must be synchronous (blocks CC for approval)."""
        pre_tool = next(d for d in CC_HOOK_DEFINITIONS if d.event_type == "PreToolUse")
        assert pre_tool.is_async is False
        assert pre_tool.timeout_seconds == 30

    def test_pre_tool_use_uses_approval_endpoint(self) -> None:
        """PreToolUse uses /events/approval (not /events)."""
        pre_tool = next(d for d in CC_HOOK_DEFINITIONS if d.event_type == "PreToolUse")
        assert pre_tool.endpoint == "/events/approval"

    def test_other_hooks_are_async(self) -> None:
        """All hooks except PreToolUse are async."""
        for hook_def in CC_HOOK_DEFINITIONS:
            if hook_def.event_type == "PreToolUse":
                continue
            assert hook_def.is_async is True, (
                f"{hook_def.event_type} should be async"
            )

    def test_marker_is_non_empty(self) -> None:
        """LATTICE_HOOK_MARKER is a non-empty string."""
        assert LATTICE_HOOK_MARKER
        assert isinstance(LATTICE_HOOK_MARKER, str)

    def test_all_definitions_have_descriptions(self) -> None:
        """All hook definitions have non-empty descriptions."""
        for hook_def in CC_HOOK_DEFINITIONS:
            assert hook_def.description, (
                f"{hook_def.event_type} missing description"
            )
