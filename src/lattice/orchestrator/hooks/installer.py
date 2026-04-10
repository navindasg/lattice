"""HookInstaller: manages Lattice hook lifecycle in CC settings.

Reads ~/.claude/settings.json, adds/removes Lattice HTTP hooks, and writes
back. Non-destructive: user's custom hooks are preserved during install and
uninstall. Idempotent: running install twice produces no duplicates.

Hook identification strategy:
    Lattice hooks are identified by URL containing LATTICE_HOOK_MARKER.
    This allows safe uninstall without touching user-created hooks.

Spool fallback:
    Each hook command includes a fallback: if the UDS socket is unreachable,
    the event is appended to ~/.lattice/spool/events.jsonl instead of being
    lost silently. The orchestrator drains the spool on next startup.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import structlog

from lattice.orchestrator.hooks.constants import (
    CC_HOOK_DEFINITIONS,
    HOOK_EVENT_TYPES,
    LATTICE_HOOK_MARKER,
)
from lattice.orchestrator.hooks.models import (
    HookCheckResult,
    HookDefinition,
    HookEventStatus,
    HookInstallResult,
    HookUninstallResult,
)

logger = structlog.get_logger(__name__)

_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_DEFAULT_SOCK_PATH = Path.home() / ".lattice" / "orchestrator.sock"
_DEFAULT_SPOOL_PATH = Path.home() / ".lattice" / "spool" / "events.jsonl"


def _build_hook_url(sock_path: Path, endpoint: str) -> str:
    """Build a curl-compatible URL for a UDS endpoint.

    Uses http+unix:// scheme which is compatible with the hook script.
    Embeds LATTICE_HOOK_MARKER in the URL for identification.

    Args:
        sock_path: Path to the Unix domain socket.
        endpoint: API endpoint path (e.g. "/events").

    Returns:
        URL string like "http+unix://%2Fhome%2F...%2Forchestrator.sock/events"
    """
    encoded_path = str(sock_path).replace("/", "%2F")
    return f"http+unix://{encoded_path}{endpoint}?source={LATTICE_HOOK_MARKER}"


def _build_hook_command(
    hook_def: HookDefinition,
    sock_path: Path,
    spool_path: Path,
) -> str:
    """Build the shell command for a hook entry.

    The command uses curl to POST the event to the orchestrator via UDS.
    Includes a fallback: if curl fails (socket unreachable), the event
    payload is appended to the spool file for later recovery.

    Args:
        hook_def: The hook definition.
        sock_path: Path to the UDS socket.
        spool_path: Path to the spool file for fallback.

    Returns:
        Shell command string for the hook.
    """
    url = _build_hook_url(sock_path, hook_def.endpoint)
    spool_dir = str(spool_path.parent)
    spool_file = str(spool_path)
    timeout = hook_def.timeout_seconds if not hook_def.is_async else 5

    # Use a Python one-liner to safely build JSON from env vars.
    # This avoids shell injection via $TOOL_INPUT or other CC variables.
    # The Python script reads env vars, serializes with json.dumps (safe),
    # then pipes to curl. Falls back to spool file on failure.
    python_payload = (
        "import json,os,sys,datetime;"
        "p=json.dumps({"
        "'session_id':os.environ.get('SESSION_ID',''),"
        f"'event_type':'{hook_def.event_type}',"
        "'tool_name':os.environ.get('TOOL_NAME',''),"
        "'tool_input':os.environ.get('TOOL_INPUT','{}'),"
        "'transcript_path':os.environ.get('TRANSCRIPT_PATH',''),"
        "'cwd':os.environ.get('CWD',''),"
        "'timestamp':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')"
        "});"
        "sys.stdout.write(p)"
    )

    return (
        f'PAYLOAD=$(python3 -c "{python_payload}") && '
        f'(curl -s --max-time {timeout} --unix-socket "{sock_path}" '
        f'-X POST -H "Content-Type: application/json" '
        f'-d "$PAYLOAD" '
        f'"{url}" '
        f'|| (mkdir -p "{spool_dir}" && echo "$PAYLOAD" >> "{spool_file}"))'
    )


def _is_lattice_hook(hook_entry: dict) -> bool:
    """Check if a hook entry was installed by Lattice.

    Identifies Lattice hooks by the presence of LATTICE_HOOK_MARKER
    in any command string. Handles both the new format (matcher + hooks
    array) and legacy flat format.

    Args:
        hook_entry: A hook dict from settings.json.

    Returns:
        True if this is a Lattice-managed hook.
    """
    # New format: { "matcher": "", "hooks": [{ "type": "command", "command": "..." }] }
    hooks_array = hook_entry.get("hooks", [])
    if isinstance(hooks_array, list):
        for h in hooks_array:
            if isinstance(h, dict) and LATTICE_HOOK_MARKER in h.get("command", ""):
                return True

    # Legacy flat format: { "command": "..." }
    command = hook_entry.get("command", "")
    return LATTICE_HOOK_MARKER in command


class HookInstaller:
    """Manages Lattice hook lifecycle in Claude Code settings.

    Install, uninstall, and check hooks in ~/.claude/settings.json.
    Non-destructive: preserves user's custom hooks. Idempotent: safe
    to run multiple times.

    Args:
        settings_path: Path to CC settings.json (default ~/.claude/settings.json).
        sock_path: Path to orchestrator UDS socket (default ~/.lattice/orchestrator.sock).
        spool_path: Path to spool file for fallback (default ~/.lattice/spool/events.jsonl).
    """

    def __init__(
        self,
        settings_path: Path | None = None,
        sock_path: Path | None = None,
        spool_path: Path | None = None,
    ) -> None:
        self._settings_path = settings_path or _DEFAULT_SETTINGS_PATH
        self._sock_path = sock_path or _DEFAULT_SOCK_PATH
        self._spool_path = spool_path or _DEFAULT_SPOOL_PATH

    @property
    def settings_path(self) -> Path:
        """Return the CC settings.json path."""
        return self._settings_path

    def install(self) -> HookInstallResult:
        """Install Lattice hooks into CC settings.json.

        Reads the existing settings, adds Lattice hook entries for all 6
        event types, and writes back. Preserves existing user hooks.
        Idempotent: does not create duplicates.

        Returns:
            HookInstallResult with counts and status.
        """
        settings = self._read_settings()
        if settings is None:
            return HookInstallResult(
                success=False,
                error="Invalid JSON in settings file — not modified",
                settings_path=str(self._settings_path),
            )

        hooks_section = settings.setdefault("hooks", {})
        installed_count = 0
        already_present = 0

        for hook_def in CC_HOOK_DEFINITIONS:
            event_hooks = hooks_section.setdefault(hook_def.event_type, [])

            # Check if Lattice hook already exists for this event
            existing_lattice = [h for h in event_hooks if _is_lattice_hook(h)]
            if existing_lattice:
                # Update existing hook in place
                already_present += 1
                idx = event_hooks.index(existing_lattice[0])
                event_hooks[idx] = self._build_hook_entry(hook_def)
                logger.debug(
                    "hook_installer.update_existing",
                    event_type=hook_def.event_type,
                )
                continue

            # Append new hook (preserves user hooks)
            event_hooks.append(self._build_hook_entry(hook_def))
            installed_count += 1
            logger.info(
                "hook_installer.installed",
                event_type=hook_def.event_type,
            )

        self._write_settings(settings)

        return HookInstallResult(
            success=True,
            installed_count=installed_count,
            already_present=already_present,
            settings_path=str(self._settings_path),
        )

    def uninstall(self) -> HookUninstallResult:
        """Remove only Lattice-managed hooks from CC settings.json.

        Identifies Lattice hooks by LATTICE_HOOK_MARKER in the command.
        User's custom hooks are left untouched.

        Returns:
            HookUninstallResult with removed count.
        """
        settings = self._read_settings()
        if settings is None:
            return HookUninstallResult(
                success=False,
                error="Invalid JSON in settings file — not modified",
                settings_path=str(self._settings_path),
            )

        hooks_section = settings.get("hooks", {})
        removed_count = 0

        for event_type in list(hooks_section.keys()):
            event_hooks = hooks_section[event_type]
            if not isinstance(event_hooks, list):
                continue

            original_len = len(event_hooks)
            filtered = [h for h in event_hooks if not _is_lattice_hook(h)]
            removed_count += original_len - len(filtered)

            if filtered:
                hooks_section[event_type] = filtered
            else:
                del hooks_section[event_type]

        # Remove empty hooks section
        if not hooks_section and "hooks" in settings:
            del settings["hooks"]

        self._write_settings(settings)

        logger.info("hook_installer.uninstalled", removed_count=removed_count)

        return HookUninstallResult(
            success=True,
            removed_count=removed_count,
            settings_path=str(self._settings_path),
        )

    def check(self) -> HookCheckResult:
        """Verify hook installation status and orchestrator reachability.

        Checks each of the 6 event types for a Lattice hook entry, and
        tests whether the orchestrator UDS socket is reachable.

        Returns:
            HookCheckResult with per-event status.
        """
        settings = self._read_settings()
        if settings is None:
            return HookCheckResult(
                all_installed=False,
                events=[],
                error="Invalid JSON in settings file",
                settings_path=str(self._settings_path),
            )

        hooks_section = settings.get("hooks", {})
        socket_reachable = self._check_socket_reachable()

        events: list[HookEventStatus] = []
        all_installed = True

        for hook_def in CC_HOOK_DEFINITIONS:
            event_hooks = hooks_section.get(hook_def.event_type, [])
            has_lattice = any(_is_lattice_hook(h) for h in event_hooks)

            if not has_lattice:
                all_installed = False

            events.append(
                HookEventStatus(
                    event_type=hook_def.event_type,
                    installed=has_lattice,
                    reachable=socket_reachable if has_lattice else None,
                )
            )

        return HookCheckResult(
            all_installed=all_installed,
            events=events,
            settings_path=str(self._settings_path),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_hook_entry(self, hook_def: HookDefinition) -> dict:
        """Build a single hook entry dict for settings.json.

        Claude Code hook format requires a matcher + hooks array::

            {
                "matcher": "",
                "hooks": [
                    { "type": "command", "command": "...", "timeout": 5000 }
                ]
            }

        The ``matcher`` is empty string to match all tools. The ``hooks``
        array contains the actual command definitions.

        Args:
            hook_def: The hook definition to build from.

        Returns:
            Dict suitable for inclusion in the event type's hooks list.
        """
        command = _build_hook_command(hook_def, self._sock_path, self._spool_path)

        hook_cmd: dict = {
            "type": "command",
            "command": command,
        }

        if hook_def.is_async:
            hook_cmd["async"] = True

        if not hook_def.is_async:
            hook_cmd["timeout"] = hook_def.timeout_seconds * 1000  # CC uses milliseconds

        return {
            "matcher": "",
            "hooks": [hook_cmd],
        }

    def _read_settings(self) -> dict | None:
        """Read and parse CC settings.json.

        Returns:
            Parsed settings dict, empty dict if file doesn't exist,
            or None if JSON is invalid.
        """
        if not self._settings_path.exists():
            logger.debug(
                "hook_installer.settings_not_found",
                path=str(self._settings_path),
            )
            return {}

        try:
            content = self._settings_path.read_text(encoding="utf-8")
            if not content.strip():
                return {}
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error(
                "hook_installer.invalid_json",
                path=str(self._settings_path),
                error=str(exc),
            )
            return None

    def _write_settings(self, settings: dict) -> None:
        """Write settings dict back to settings.json.

        Creates parent directory if needed. Pretty-prints JSON with
        2-space indent for readability.

        Args:
            settings: The full settings dict to write.
        """
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        self._settings_path.write_text(content, encoding="utf-8")
        logger.debug(
            "hook_installer.settings_written",
            path=str(self._settings_path),
        )

    def _check_socket_reachable(self) -> bool:
        """Test if the orchestrator UDS socket is accepting connections.

        Returns:
            True if a connection can be established, False otherwise.
        """
        if not self._sock_path.exists():
            return False

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(2.0)
                sock.connect(str(self._sock_path))
            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return False
