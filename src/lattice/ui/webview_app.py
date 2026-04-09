"""Native desktop dashboard using pywebview.

Replaces the Textual TUI with a proper native window that renders
HTML/CSS/JS via the OS webview (WebKit on macOS, WebView2 on Windows).

The Python DashboardAPI class is exposed to JavaScript via
``window.pywebview.api``.  A background thread polls
DashboardService.poll_full_snapshot() every second and pushes
serialized JSON to the frontend via ``window.evaluate_js()``.

Terminal panes are managed by PTYManager (direct PTY control) and
rendered in the frontend via xterm.js.
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

import structlog
import webview

from lattice.ui.pty_manager import PTYManager, TerminalStatus
from lattice.ui.services import DashboardService, DashboardSnapshot

log = structlog.get_logger(__name__)

_POLL_INTERVAL: float = 1.0
_WEB_DIR = Path(__file__).parent / "web"


def _snapshot_to_dict(snap: DashboardSnapshot) -> dict[str, Any]:
    """Serialize a DashboardSnapshot to a JSON-safe dictionary.

    Pydantic models use .model_dump(), dataclasses use dataclasses.asdict(),
    and immutable collections are converted to plain lists/dicts.
    """
    instances = [inst.model_dump() for inst in snap.instances]

    soul_state = snap.soul_state.model_dump()

    memory_entries = [entry.model_dump() for entry in snap.memory_entries]

    recent_events = [
        ev.model_dump() if hasattr(ev, "model_dump") else dict(ev)
        for ev in snap.recent_events
    ]

    health = asdict(snap.health)

    captured_output = {
        pane_id: list(lines)
        for pane_id, lines in snap.captured_output.items()
    }

    return {
        "instances": instances,
        "soul_state": soul_state,
        "memory_entries": memory_entries,
        "recent_events": recent_events,
        "health": health,
        "captured_output": captured_output,
    }


class DashboardAPI:
    """Python API exposed to the frontend via window.pywebview.api.

    Methods on this class are callable from JavaScript as:
        window.pywebview.api.method_name(args)

    Terminal management is handled by PTYManager.  Output from terminals
    is pushed to the frontend via evaluate_js() callbacks.
    """

    def __init__(
        self,
        service: DashboardService,
        columns: int,
        interactive: bool,
    ) -> None:
        self._service = service
        self._columns = columns
        self._interactive = interactive
        self._loop: asyncio.AbstractEventLoop | None = None
        self._capture: Any | None = None
        self._pipeline: Any | None = None
        self._window: webview.Window | None = None
        self._pty_manager: PTYManager | None = None

    def _init_pty_manager(self) -> None:
        """Initialize the PTYManager with output/exit callbacks wired
        to push data to the frontend via evaluate_js.
        """
        self._pty_manager = PTYManager(
            on_output=self._on_terminal_output,
            on_exit=self._on_terminal_exit,
        )

    def _on_terminal_output(self, pane_id: str, data: bytes) -> None:
        """Callback invoked by PTYManager reader threads on new output.

        Encodes binary data as base64 and pushes it to the frontend
        via evaluate_js.  Thread-safe.
        """
        if self._window is None:
            return

        b64 = base64.b64encode(data).decode("ascii")
        safe_pane_id = json.dumps(pane_id)
        safe_data = json.dumps(b64)

        try:
            self._window.evaluate_js(
                f"window.__terminalOutput && window.__terminalOutput({safe_pane_id}, {safe_data});"
            )
        except Exception as exc:
            log.debug("api.terminal_output_push_failed", pane_id=pane_id, error=str(exc))

    def _on_terminal_exit(self, pane_id: str, exit_code: int | None) -> None:
        """Callback invoked by PTYManager when a terminal process exits.

        Pushes exit notification to the frontend via evaluate_js.
        """
        if self._window is None:
            return

        safe_pane_id = json.dumps(pane_id)
        safe_code = json.dumps(exit_code)

        try:
            self._window.evaluate_js(
                f"window.__terminalExited && window.__terminalExited({safe_pane_id}, {safe_code});"
            )
        except Exception:
            pass

    @property
    def config(self) -> dict[str, Any]:
        """Return dashboard configuration for the frontend."""
        return {
            "columns": self._columns,
            "interactive": self._interactive,
            "poll_interval": _POLL_INTERVAL,
        }

    def get_config(self) -> str:
        """Return dashboard config as JSON string."""
        return json.dumps(self.config)

    def poll_snapshot(self) -> str:
        """Poll a fresh snapshot and return it as a JSON string.

        Called by the frontend on demand or as a fallback.
        The primary update path is the push-based background poller.
        """
        if self._loop is None:
            return json.dumps(None)
        future = asyncio.run_coroutine_threadsafe(
            self._service.poll_full_snapshot(), self._loop
        )
        try:
            snapshot = future.result(timeout=10.0)
            return json.dumps(_snapshot_to_dict(snapshot))
        except Exception as exc:
            log.error("api.poll_snapshot_failed", error=str(exc))
            return json.dumps({"error": str(exc)})

    # ------------------------------------------------------------------
    # Terminal management (PTY-backed)
    # ------------------------------------------------------------------

    def spawn_terminal(self, cmd_json: str | None = None) -> str:
        """Spawn a new PTY-backed terminal process.

        Args:
            cmd_json: JSON string of {"cmd": [...], "cwd": "...", "cols": N, "rows": N}.
                      All fields optional.  Defaults to user's shell in home dir.

        Returns:
            JSON string with {pane_id, success} or {error, success: false}.
        """
        if self._pty_manager is None:
            return json.dumps({"success": False, "error": "PTYManager not initialized"})

        try:
            params: dict[str, Any] = {}
            if cmd_json:
                parsed = json.loads(cmd_json) if isinstance(cmd_json, str) else cmd_json
                if isinstance(parsed, dict):
                    params = parsed

            # Validate cmd is a list of strings if provided
            cmd = params.get("cmd")
            if cmd is not None:
                if not isinstance(cmd, list) or not all(isinstance(c, str) for c in cmd):
                    return json.dumps({"success": False, "error": "cmd must be a list of strings"})

            # Validate cwd exists if provided
            cwd = params.get("cwd")
            if cwd is not None:
                import os
                cwd = os.path.realpath(str(cwd))
                if not os.path.isdir(cwd):
                    return json.dumps({"success": False, "error": "Invalid working directory"})

            # Validate dimensions
            cols = int(params.get("cols", 80))
            rows = int(params.get("rows", 24))
            cols = max(1, min(500, cols))
            rows = max(1, min(200, rows))

            pane_id = self._pty_manager.spawn(
                cmd=cmd,
                cwd=cwd,
                cols=cols,
                rows=rows,
            )
            return json.dumps({"success": True, "pane_id": pane_id})
        except Exception as exc:
            log.error("api.spawn_terminal_failed", error=str(exc))
            return json.dumps({"success": False, "error": str(exc)})

    def write_terminal(self, pane_id: str, data: str) -> str:
        """Write input data to a terminal's PTY.

        Args:
            pane_id: Terminal identifier from spawn_terminal().
            data: Base64-encoded input bytes.

        Returns:
            JSON string with {success} or {error, success: false}.
        """
        if self._pty_manager is None:
            return json.dumps({"success": False, "error": "PTYManager not initialized"})

        try:
            raw = base64.b64decode(data)
            self._pty_manager.write(pane_id, raw)
            return json.dumps({"success": True})
        except Exception as exc:
            log.error("api.write_terminal_failed", pane_id=pane_id, error=str(exc))
            return json.dumps({"success": False, "error": str(exc)})

    def resize_terminal(self, pane_id: str, cols: int, rows: int) -> str:
        """Resize a terminal's PTY.

        Args:
            pane_id: Terminal identifier from spawn_terminal().
            cols: New column count.
            rows: New row count.

        Returns:
            JSON string with {success} or {error, success: false}.
        """
        if self._pty_manager is None:
            return json.dumps({"success": False, "error": "PTYManager not initialized"})

        try:
            cols = max(1, min(500, int(cols)))
            rows = max(1, min(200, int(rows)))
            self._pty_manager.resize(pane_id, cols, rows)
            return json.dumps({"success": True})
        except Exception as exc:
            log.error("api.resize_terminal_failed", pane_id=pane_id, error=str(exc))
            return json.dumps({"success": False, "error": str(exc)})

    def kill_terminal(self, pane_id: str) -> str:
        """Kill a terminal process and clean up its PTY.

        Args:
            pane_id: Terminal identifier from spawn_terminal().

        Returns:
            JSON string with {success} or {error, success: false}.
        """
        if self._pty_manager is None:
            return json.dumps({"success": False, "error": "PTYManager not initialized"})

        try:
            self._pty_manager.kill(pane_id)
            return json.dumps({"success": True})
        except Exception as exc:
            log.error("api.kill_terminal_failed", pane_id=pane_id, error=str(exc))
            return json.dumps({"success": False, "error": str(exc)})

    def list_terminals(self) -> str:
        """List all managed terminal processes.

        Returns:
            JSON string with {terminals: [...], success: true}.
        """
        if self._pty_manager is None:
            return json.dumps({"success": True, "terminals": []})

        try:
            terminals = self._pty_manager.list_terminals()
            return json.dumps({
                "success": True,
                "terminals": [
                    {
                        "pane_id": t.pane_id,
                        "cmd": list(t.cmd),
                        "cwd": t.cwd,
                        "pid": t.pid,
                        "status": t.status.value,
                        "exit_code": t.exit_code,
                        "cols": t.cols,
                        "rows": t.rows,
                    }
                    for t in terminals
                ],
            })
        except Exception as exc:
            log.error("api.list_terminals_failed", error=str(exc))
            return json.dumps({"success": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # Voice / text commands
    # ------------------------------------------------------------------

    def send_text_command(self, text: str) -> str:
        """Process a text command through the voice pipeline.

        Returns JSON with action, detail, and success keys.
        """
        if self._loop is None:
            return json.dumps({"error": "Event loop not ready"})
        future = asyncio.run_coroutine_threadsafe(
            self._service.process_text_command(text), self._loop
        )
        try:
            result = future.result(timeout=30.0)
            return json.dumps(result)
        except Exception as exc:
            log.error("api.send_text_command_failed", error=str(exc))
            return json.dumps({
                "action": "error",
                "detail": str(exc),
                "success": False,
            })

    def start_recording(self) -> str:
        """Start capturing audio from the microphone.

        Returns JSON with success status and any error message.
        """
        try:
            from lattice.orchestrator.voice.capture import AudioCapture, check_microphone

            if not check_microphone():
                return json.dumps({
                    "success": False,
                    "error": "No microphone detected",
                })

            if self._capture is None:
                self._capture = AudioCapture()

            self._capture.start()
            return json.dumps({"success": True})
        except Exception as exc:
            log.error("api.start_recording_failed", error=str(exc))
            return json.dumps({
                "success": False,
                "error": str(exc),
            })

    def stop_recording(self) -> str:
        """Stop recording and process audio through the voice pipeline.

        Returns JSON with transcript, action, detail, and success keys.
        """
        if self._capture is None:
            return json.dumps({
                "success": False,
                "error": "No active recording",
            })

        try:
            audio = self._capture.stop()
            if audio is None:
                return json.dumps({
                    "success": False,
                    "action": "empty_transcript",
                    "detail": "Recording too short — hold the button longer",
                    "transcript": "",
                })

            from lattice.orchestrator.voice.models import VoiceConfig
            from lattice.orchestrator.voice.pipeline import VoicePipeline
            from lattice.orchestrator.voice.router import IntentRouter

            if self._pipeline is None:
                router = IntentRouter()
                self._pipeline = VoicePipeline(
                    config=VoiceConfig(), router=router
                )
                # Eagerly load the STT model so the first transcription
                # doesn't hang for 2-3s with no feedback.
                stt = self._pipeline._ensure_stt()
                stt._ensure_loaded()

            result = self._pipeline.process_audio(audio)

            # Complete async mapper dispatch if needed
            if result.action == "mapper_dispatch_pending" and self._loop is not None:
                future = asyncio.run_coroutine_threadsafe(
                    self._pipeline.complete_mapper_dispatch(result),
                    self._loop,
                )
                result = future.result(timeout=30.0)

            transcript = result.data.get("transcript", "")
            return json.dumps({
                "success": result.success,
                "action": result.action,
                "detail": result.detail,
                "transcript": transcript,
            })
        except Exception as exc:
            log.error("api.stop_recording_failed", error=str(exc))
            return json.dumps({
                "success": False,
                "action": "error",
                "detail": str(exc),
                "transcript": "",
            })

    def quit_app(self) -> None:
        """Close the dashboard window."""
        for win in webview.windows:
            win.destroy()


class _Poller:
    """Background poller that pushes snapshots to the frontend."""

    def __init__(
        self,
        service: DashboardService,
        window: webview.Window,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._service = service
        self._window = window
        self._loop = loop
        self._running = False

    async def _poll_once(self) -> DashboardSnapshot | None:
        try:
            return await self._service.poll_full_snapshot()
        except Exception as exc:
            log.error("poller.poll_failed", error=str(exc))
            return None

    def _push_to_frontend(self, snapshot: DashboardSnapshot) -> None:
        """Serialize snapshot and push to JS via evaluate_js."""
        try:
            data = _snapshot_to_dict(snapshot)
            json_str = json.dumps(data)
            safe_str = json.dumps(json_str)
            self._window.evaluate_js(
                f"window.__latticeUpdate && window.__latticeUpdate(JSON.parse({safe_str}));"
            )
        except Exception as exc:
            log.error("poller.push_failed", error=str(exc))

    async def _run_loop(self) -> None:
        self._running = True
        while self._running:
            snapshot = await self._poll_once()
            if snapshot is not None:
                self._push_to_frontend(snapshot)
            await asyncio.sleep(_POLL_INTERVAL)

    def start(self) -> None:
        """Start the polling loop on the async event loop."""
        asyncio.run_coroutine_threadsafe(self._run_loop(), self._loop)

    def stop(self) -> None:
        self._running = False


def _run_async_loop(
    loop: asyncio.AbstractEventLoop,
    service: DashboardService,
) -> None:
    """Run the async event loop in a background thread.

    Initializes the DashboardService before signaling readiness.
    """
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(service.initialize())
    except Exception as exc:
        log.error("async_loop.init_failed", error=str(exc))
        return
    loop.run_forever()


def launch_dashboard(
    soul_dir: Path,
    sock_path: Path | None = None,
    columns: int = 3,
    interactive: bool = False,
) -> None:
    """Create and launch the pywebview desktop dashboard.

    This is the main entry point called by the CLI command.
    It creates an async event loop in a background thread,
    initializes the DashboardService and PTYManager, and opens
    the native window.

    Args:
        soul_dir: Path to the soul ecosystem directory.
        sock_path: Override for the orchestrator UDS socket path.
        columns: Number of terminal grid columns.
        interactive: Whether to allow sending input to panes.
    """
    service = DashboardService(soul_dir=soul_dir, sock_path=sock_path)
    api = DashboardAPI(
        service=service,
        columns=columns,
        interactive=interactive,
    )

    loop = asyncio.new_event_loop()
    api._loop = loop

    # Initialize PTY manager with callbacks wired to the frontend
    api._init_pty_manager()

    async_thread = threading.Thread(
        target=_run_async_loop,
        args=(loop, service),
        daemon=True,
        name="lattice-async",
    )
    async_thread.start()

    index_path = _WEB_DIR / "index.html"
    window = webview.create_window(
        title="Lattice Dashboard",
        url=str(index_path),
        js_api=api,
        width=1400,
        height=900,
        min_size=(900, 600),
        background_color="#0f1117",
        text_select=True,
    )

    # Store window reference for PTY output callbacks
    api._window = window

    poller: _Poller | None = None

    def _on_loaded() -> None:
        nonlocal poller
        try:
            config_json = json.dumps(api.config)
            safe_str = json.dumps(config_json)
            window.evaluate_js(
                f"window.__latticeInit && window.__latticeInit(JSON.parse({safe_str}));"
            )
        except Exception as exc:
            log.error("webview.init_failed", error=str(exc))

        if poller is not None:
            poller.stop()
        poller = _Poller(service=service, window=window, loop=loop)
        poller.start()

    def _on_closed() -> None:
        # Shut down PTY manager first
        if api._pty_manager is not None:
            api._pty_manager.shutdown()

        if poller is not None:
            poller.stop()
        future = asyncio.run_coroutine_threadsafe(service.close(), loop)
        try:
            future.result(timeout=5.0)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)

    window.events.loaded += _on_loaded
    window.events.closed += _on_closed

    webview.start(debug=False)
