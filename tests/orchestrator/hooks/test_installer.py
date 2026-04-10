"""Tests for HookInstaller: install, uninstall, check, and edge cases.

Tests cover:
    - Fresh install on missing settings.json
    - Install over existing settings with user hooks
    - Idempotent re-install (no duplicates)
    - Uninstall removes only Lattice hooks
    - Uninstall preserves user hooks
    - Check reports per-event status
    - Invalid JSON handling (error, no modification)
    - Empty settings file handling
    - PreToolUse is synchronous with 30s timeout
    - Other hooks are async
    - Spool fallback in generated command
    - Hook URL contains LATTICE_HOOK_MARKER
    - Socket reachability check
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lattice.orchestrator.hooks.constants import (
    CC_HOOK_DEFINITIONS,
    HOOK_EVENT_TYPES,
    LATTICE_HOOK_MARKER,
)
from lattice.orchestrator.hooks.installer import (
    HookInstaller,
    _build_hook_command,
    _build_hook_url,
    _is_lattice_hook,
)
from lattice.orchestrator.hooks.models import HookDefinition


def _get_hook_command(hook_entry: dict) -> str:
    """Extract the command string from a hook entry.

    Handles the CC hook format: { "matcher": "", "hooks": [{ "type": "command", "command": "..." }] }
    """
    hooks_array = hook_entry.get("hooks", [])
    if hooks_array and isinstance(hooks_array, list):
        return hooks_array[0].get("command", "")
    return hook_entry.get("command", "")


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Path:
    """Return path to a temporary settings.json (does not exist yet)."""
    return tmp_path / ".claude" / "settings.json"


@pytest.fixture
def tmp_sock(tmp_path: Path) -> Path:
    """Return path to a temporary socket file."""
    return tmp_path / ".lattice" / "orchestrator.sock"


@pytest.fixture
def tmp_spool(tmp_path: Path) -> Path:
    """Return path to a temporary spool file."""
    return tmp_path / ".lattice" / "spool" / "events.jsonl"


@pytest.fixture
def installer(tmp_settings: Path, tmp_sock: Path, tmp_spool: Path) -> HookInstaller:
    """Create a HookInstaller with temporary paths."""
    return HookInstaller(
        settings_path=tmp_settings,
        sock_path=tmp_sock,
        spool_path=tmp_spool,
    )


# ---------------------------------------------------------------------------
# URL and command building
# ---------------------------------------------------------------------------


class TestBuildHookUrl:
    def test_url_contains_marker(self) -> None:
        url = _build_hook_url(Path("/tmp/test.sock"), "/events")
        assert LATTICE_HOOK_MARKER in url

    def test_url_contains_endpoint(self) -> None:
        url = _build_hook_url(Path("/tmp/test.sock"), "/events/approval")
        assert "/events/approval" in url

    def test_url_encodes_socket_path(self) -> None:
        url = _build_hook_url(Path("/home/user/.lattice/orchestrator.sock"), "/events")
        assert "%2F" in url


class TestBuildHookCommand:
    def test_command_contains_curl(self) -> None:
        hook_def = CC_HOOK_DEFINITIONS[0]
        cmd = _build_hook_command(hook_def, Path("/tmp/test.sock"), Path("/tmp/spool.jsonl"))
        assert "curl" in cmd

    def test_command_contains_spool_fallback(self) -> None:
        hook_def = CC_HOOK_DEFINITIONS[0]
        cmd = _build_hook_command(hook_def, Path("/tmp/test.sock"), Path("/tmp/spool.jsonl"))
        assert "spool.jsonl" in cmd
        assert "||" in cmd  # fallback operator

    def test_sync_hook_has_longer_timeout(self) -> None:
        pre_tool = next(d for d in CC_HOOK_DEFINITIONS if d.event_type == "PreToolUse")
        cmd = _build_hook_command(pre_tool, Path("/tmp/test.sock"), Path("/tmp/spool.jsonl"))
        assert "--max-time 30" in cmd

    def test_async_hook_has_short_timeout(self) -> None:
        post_tool = next(d for d in CC_HOOK_DEFINITIONS if d.event_type == "PostToolUse")
        cmd = _build_hook_command(post_tool, Path("/tmp/test.sock"), Path("/tmp/spool.jsonl"))
        assert "--max-time 5" in cmd

    def test_command_contains_event_type(self) -> None:
        for hook_def in CC_HOOK_DEFINITIONS:
            cmd = _build_hook_command(hook_def, Path("/tmp/test.sock"), Path("/tmp/spool.jsonl"))
            assert hook_def.event_type in cmd


class TestIsLatticeHook:
    def test_lattice_hook_detected(self) -> None:
        hook = {"command": f"curl ... {LATTICE_HOOK_MARKER} ..."}
        assert _is_lattice_hook(hook) is True

    def test_user_hook_not_detected(self) -> None:
        hook = {"command": "echo 'custom hook'"}
        assert _is_lattice_hook(hook) is False

    def test_empty_command_not_detected(self) -> None:
        hook = {"command": ""}
        assert _is_lattice_hook(hook) is False

    def test_missing_command_not_detected(self) -> None:
        hook = {"other_key": "value"}
        assert _is_lattice_hook(hook) is False


# ---------------------------------------------------------------------------
# Fresh install
# ---------------------------------------------------------------------------


class TestFreshInstall:
    def test_creates_settings_file(self, installer: HookInstaller, tmp_settings: Path) -> None:
        """Install creates settings.json if it doesn't exist."""
        assert not tmp_settings.exists()
        result = installer.install()
        assert result.success is True
        assert tmp_settings.exists()

    def test_installs_six_hooks(self, installer: HookInstaller) -> None:
        """Fresh install creates 6 hook entries."""
        result = installer.install()
        assert result.installed_count == 6
        assert result.already_present == 0

    def test_settings_has_all_event_types(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """After install, settings.json contains all 6 event types."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        hooks = settings["hooks"]
        for event_type in HOOK_EVENT_TYPES:
            assert event_type in hooks, f"Missing {event_type}"

    def test_pre_tool_use_is_synchronous(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """PreToolUse hook command has no 'async' key and has 'timeout'."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        pre_tool_hooks = settings["hooks"]["PreToolUse"]
        lattice_hook = next(h for h in pre_tool_hooks if _is_lattice_hook(h))
        hook_cmd = lattice_hook["hooks"][0]
        assert "async" not in hook_cmd
        assert hook_cmd["timeout"] == 30000  # 30s in ms

    def test_async_hooks_have_async_flag(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """All non-PreToolUse hooks have 'async': true."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        for event_type in HOOK_EVENT_TYPES:
            if event_type == "PreToolUse":
                continue
            hooks = settings["hooks"][event_type]
            lattice_hook = next(h for h in hooks if _is_lattice_hook(h))
            hook_cmd = lattice_hook["hooks"][0]
            assert hook_cmd.get("async") is True, (
                f"{event_type} should have async=True"
            )

    def test_hook_commands_contain_marker(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """All installed hook commands contain LATTICE_HOOK_MARKER."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        for event_type in HOOK_EVENT_TYPES:
            hooks = settings["hooks"][event_type]
            lattice_hook = next(h for h in hooks if _is_lattice_hook(h))
            assert LATTICE_HOOK_MARKER in _get_hook_command(lattice_hook)

    def test_hook_commands_contain_spool_fallback(
        self, installer: HookInstaller, tmp_settings: Path, tmp_spool: Path
    ) -> None:
        """All hook commands include spool file fallback."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        for event_type in HOOK_EVENT_TYPES:
            hooks = settings["hooks"][event_type]
            lattice_hook = next(h for h in hooks if _is_lattice_hook(h))
            assert str(tmp_spool) in _get_hook_command(lattice_hook)

    def test_result_settings_path(self, installer: HookInstaller, tmp_settings: Path) -> None:
        """Install result includes the settings path."""
        result = installer.install()
        assert result.settings_path == str(tmp_settings)


# ---------------------------------------------------------------------------
# Install over existing settings with user hooks
# ---------------------------------------------------------------------------


class TestInstallPreservesUserHooks:
    def test_user_hooks_preserved(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """User's custom hooks are preserved during install."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            "hooks": {
                "PreToolUse": [
                    {"command": "echo 'my custom pre-tool hook'"}
                ],
                "MyCustomEvent": [
                    {"command": "echo 'totally custom'"}
                ],
            },
            "other_setting": True,
        }
        tmp_settings.write_text(json.dumps(existing))

        installer.install()

        settings = json.loads(tmp_settings.read_text())

        # User's PreToolUse hook still present
        pre_tool_hooks = settings["hooks"]["PreToolUse"]
        user_hooks = [h for h in pre_tool_hooks if not _is_lattice_hook(h)]
        assert len(user_hooks) == 1
        assert user_hooks[0]["command"] == "echo 'my custom pre-tool hook'"

        # User's custom event still present
        assert "MyCustomEvent" in settings["hooks"]

        # Other settings preserved
        assert settings["other_setting"] is True

    def test_non_hook_settings_untouched(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Non-hook settings are preserved."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        existing = {"theme": "dark", "editor": "vim"}
        tmp_settings.write_text(json.dumps(existing))

        installer.install()

        settings = json.loads(tmp_settings.read_text())
        assert settings["theme"] == "dark"
        assert settings["editor"] == "vim"


# ---------------------------------------------------------------------------
# Idempotent re-install
# ---------------------------------------------------------------------------


class TestIdempotentInstall:
    def test_no_duplicates_on_reinstall(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Running install twice does not create duplicate hooks."""
        installer.install()
        result2 = installer.install()

        assert result2.already_present == 6
        assert result2.installed_count == 0

        settings = json.loads(tmp_settings.read_text())
        for event_type in HOOK_EVENT_TYPES:
            hooks = settings["hooks"][event_type]
            lattice_hooks = [h for h in hooks if _is_lattice_hook(h)]
            assert len(lattice_hooks) == 1, (
                f"Expected 1 Lattice hook for {event_type}, got {len(lattice_hooks)}"
            )

    def test_triple_install_still_idempotent(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Three installs still produce exactly 1 Lattice hook per event."""
        installer.install()
        installer.install()
        installer.install()

        settings = json.loads(tmp_settings.read_text())
        for event_type in HOOK_EVENT_TYPES:
            hooks = settings["hooks"][event_type]
            lattice_hooks = [h for h in hooks if _is_lattice_hook(h)]
            assert len(lattice_hooks) == 1


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_removes_all_lattice_hooks(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Uninstall removes all Lattice hooks."""
        installer.install()
        result = installer.uninstall()

        assert result.success is True
        assert result.removed_count == 6

        settings = json.loads(tmp_settings.read_text())
        # No hooks section (all events were Lattice-only)
        assert "hooks" not in settings or not settings.get("hooks")

    def test_preserves_user_hooks(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Uninstall preserves user's custom hooks."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            "hooks": {
                "PreToolUse": [
                    {"command": "echo 'my hook'"},
                ],
            },
        }
        tmp_settings.write_text(json.dumps(existing))

        installer.install()
        installer.uninstall()

        settings = json.loads(tmp_settings.read_text())
        pre_tool = settings["hooks"]["PreToolUse"]
        assert len(pre_tool) == 1
        assert pre_tool[0]["command"] == "echo 'my hook'"

    def test_uninstall_on_clean_settings(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Uninstall on settings with no Lattice hooks removes nothing."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        tmp_settings.write_text(json.dumps({"hooks": {}}))

        result = installer.uninstall()

        assert result.success is True
        assert result.removed_count == 0

    def test_uninstall_result_settings_path(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Uninstall result includes settings path."""
        installer.install()
        result = installer.uninstall()
        assert result.settings_path == str(tmp_settings)


# ---------------------------------------------------------------------------
# Check hooks
# ---------------------------------------------------------------------------


class TestCheckHooks:
    def test_all_installed_after_install(self, installer: HookInstaller) -> None:
        """check() reports all_installed=True after install()."""
        installer.install()
        result = installer.check()

        assert result.all_installed is True
        assert len(result.events) == 6

    def test_not_installed_before_install(self, installer: HookInstaller) -> None:
        """check() reports all_installed=False before install()."""
        result = installer.check()

        assert result.all_installed is False

    def test_per_event_status(self, installer: HookInstaller) -> None:
        """check() reports installed=True for each event after install."""
        installer.install()
        result = installer.check()

        for event_status in result.events:
            assert event_status.installed is True, (
                f"{event_status.event_type} not installed"
            )

    def test_missing_events_after_partial_uninstall(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """check() detects missing events if some are removed."""
        installer.install()

        # Manually remove one event
        settings = json.loads(tmp_settings.read_text())
        del settings["hooks"]["PreToolUse"]
        tmp_settings.write_text(json.dumps(settings))

        result = installer.check()

        assert result.all_installed is False
        pre_tool_status = next(
            e for e in result.events if e.event_type == "PreToolUse"
        )
        assert pre_tool_status.installed is False

    def test_socket_not_reachable_when_no_server(self, installer: HookInstaller) -> None:
        """check() reports reachable=False when no socket exists."""
        installer.install()
        result = installer.check()

        for event_status in result.events:
            assert event_status.reachable is False


# ---------------------------------------------------------------------------
# Invalid JSON handling
# ---------------------------------------------------------------------------


class TestInvalidJsonHandling:
    def test_install_fails_on_invalid_json(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Install errors with clear message on invalid JSON."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        tmp_settings.write_text("{invalid json here}")

        result = installer.install()

        assert result.success is False
        assert "Invalid JSON" in (result.error or "")

    def test_invalid_json_not_modified(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Install does not modify the file when JSON is invalid."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        original = "{invalid json here}"
        tmp_settings.write_text(original)

        installer.install()

        assert tmp_settings.read_text() == original

    def test_uninstall_fails_on_invalid_json(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Uninstall errors with clear message on invalid JSON."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        tmp_settings.write_text("not json!")

        result = installer.uninstall()

        assert result.success is False
        assert "Invalid JSON" in (result.error or "")

    def test_check_fails_on_invalid_json(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Check errors with clear message on invalid JSON."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        tmp_settings.write_text("broken{}")

        result = installer.check()

        assert result.all_installed is False
        assert "Invalid JSON" in (result.error or "")


# ---------------------------------------------------------------------------
# Empty settings file
# ---------------------------------------------------------------------------


class TestEmptySettingsFile:
    def test_install_on_empty_file(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Install works on empty settings file."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        tmp_settings.write_text("")

        result = installer.install()

        assert result.success is True
        assert result.installed_count == 6

    def test_install_on_whitespace_only_file(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Install works on whitespace-only settings file."""
        tmp_settings.parent.mkdir(parents=True, exist_ok=True)
        tmp_settings.write_text("   \n  ")

        result = installer.install()

        assert result.success is True
        assert result.installed_count == 6


# ---------------------------------------------------------------------------
# Hook payload content
# ---------------------------------------------------------------------------


class TestHookPayloadContent:
    def test_payload_includes_session_id(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Hook commands reference $SESSION_ID."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        for event_type in HOOK_EVENT_TYPES:
            hooks = settings["hooks"][event_type]
            lattice_hook = next(h for h in hooks if _is_lattice_hook(h))
            assert "SESSION_ID" in _get_hook_command(lattice_hook)

    def test_payload_includes_tool_name(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Hook commands reference $TOOL_NAME."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        pre_tool = settings["hooks"]["PreToolUse"]
        lattice_hook = next(h for h in pre_tool if _is_lattice_hook(h))
        assert "TOOL_NAME" in _get_hook_command(lattice_hook)

    def test_payload_includes_timestamp(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Hook commands generate a timestamp."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        for event_type in HOOK_EVENT_TYPES:
            hooks = settings["hooks"][event_type]
            lattice_hook = next(h for h in hooks if _is_lattice_hook(h))
            assert "timestamp" in _get_hook_command(lattice_hook)

    def test_payload_includes_cwd(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Hook commands reference $CWD."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        for event_type in HOOK_EVENT_TYPES:
            hooks = settings["hooks"][event_type]
            lattice_hook = next(h for h in hooks if _is_lattice_hook(h))
            assert "CWD" in _get_hook_command(lattice_hook)

    def test_payload_includes_transcript_path(
        self, installer: HookInstaller, tmp_settings: Path
    ) -> None:
        """Hook commands reference $TRANSCRIPT_PATH."""
        installer.install()
        settings = json.loads(tmp_settings.read_text())
        for event_type in HOOK_EVENT_TYPES:
            hooks = settings["hooks"][event_type]
            lattice_hook = next(h for h in hooks if _is_lattice_hook(h))
            assert "TRANSCRIPT_PATH" in _get_hook_command(lattice_hook)
