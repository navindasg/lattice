"""Tests for hook installer Pydantic models."""
import pytest

from lattice.orchestrator.hooks.models import (
    HookCheckResult,
    HookDefinition,
    HookEventStatus,
    HookInstallResult,
    HookUninstallResult,
)


class TestHookDefinition:
    def test_construction(self) -> None:
        hook = HookDefinition(
            event_type="PreToolUse",
            endpoint="/events/approval",
            is_async=False,
            timeout_seconds=30,
            description="Block CC for approval",
        )
        assert hook.event_type == "PreToolUse"
        assert hook.is_async is False
        assert hook.timeout_seconds == 30

    def test_frozen(self) -> None:
        hook = HookDefinition(
            event_type="PostToolUse",
            endpoint="/events",
            is_async=True,
        )
        with pytest.raises(Exception):
            hook.event_type = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        hook = HookDefinition(
            event_type="Stop",
            endpoint="/events",
            is_async=True,
        )
        assert hook.timeout_seconds == 30
        assert hook.description == ""


class TestHookEventStatus:
    def test_construction(self) -> None:
        status = HookEventStatus(
            event_type="PreToolUse",
            installed=True,
            reachable=True,
        )
        assert status.installed is True
        assert status.reachable is True

    def test_reachable_none_default(self) -> None:
        status = HookEventStatus(
            event_type="Stop",
            installed=False,
        )
        assert status.reachable is None


class TestHookInstallResult:
    def test_success(self) -> None:
        result = HookInstallResult(
            success=True,
            installed_count=6,
            settings_path="/home/.claude/settings.json",
        )
        assert result.success is True
        assert result.installed_count == 6
        assert result.error is None

    def test_failure(self) -> None:
        result = HookInstallResult(
            success=False,
            error="Invalid JSON",
        )
        assert result.success is False
        assert result.error == "Invalid JSON"


class TestHookUninstallResult:
    def test_success(self) -> None:
        result = HookUninstallResult(
            success=True,
            removed_count=6,
            settings_path="/home/.claude/settings.json",
        )
        assert result.removed_count == 6

    def test_frozen(self) -> None:
        result = HookUninstallResult(success=True, removed_count=3)
        with pytest.raises(Exception):
            result.removed_count = 0  # type: ignore[misc]


class TestHookCheckResult:
    def test_all_installed(self) -> None:
        events = [
            HookEventStatus(event_type="PreToolUse", installed=True, reachable=True),
            HookEventStatus(event_type="PostToolUse", installed=True, reachable=True),
        ]
        result = HookCheckResult(all_installed=True, events=events)
        assert result.all_installed is True
        assert len(result.events) == 2

    def test_not_all_installed(self) -> None:
        events = [
            HookEventStatus(event_type="PreToolUse", installed=True),
            HookEventStatus(event_type="PostToolUse", installed=False),
        ]
        result = HookCheckResult(all_installed=False, events=events)
        assert result.all_installed is False
