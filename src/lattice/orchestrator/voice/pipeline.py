"""Voice pipeline: composes full capture -> STT -> intent -> route flow.

VoicePipeline wires all voice modules together:
    AudioCapture -> STTProvider -> IntentClassifier -> IntentRouter

Also provides:
    beep_start()          — play record-start beep (macOS: osascript, other: bell)
    beep_stop()           — play record-stop beep (macOS: osascript 'beep 2', other: bell)
    format_voice_display  — format route result for CLI display

The push-to-talk loop is exposed via run_listener() (async).
Text fallback (no audio/STT) is via process_text().
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from typing import Any

import click
import numpy as np
import structlog

from typing import TYPE_CHECKING

from lattice.orchestrator.protocol import read_message, write_message
from lattice.orchestrator.voice.intent import IntentClassifier
from lattice.orchestrator.voice.models import VoiceConfig
from lattice.orchestrator.voice.router import IntentRouter, RouteResult

if TYPE_CHECKING:
    from lattice.orchestrator.voice.capture import AudioCapture
    from lattice.orchestrator.voice.hotkey import HotkeyListener

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Audio feedback
# ---------------------------------------------------------------------------


def beep_start() -> None:
    """Play record-start audio feedback.

    On macOS: runs osascript to play the system beep (single tone).
    Other platforms: writes ASCII bell character to stdout.
    """
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", "beep"], check=False, capture_output=True)
    else:
        print("\a", end="", flush=True)


def beep_stop() -> None:
    """Play record-stop audio feedback.

    On macOS: runs osascript with 'beep 2' (two-tone stop signal).
    Other platforms: writes ASCII bell character to stdout.
    """
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", "beep 2"], check=False, capture_output=True)
    else:
        print("\a", end="", flush=True)


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------


def format_voice_display(transcript: str, result: RouteResult) -> str:
    """Format a RouteResult for CLI display after processing a voice command.

    Args:
        transcript: The original transcribed (or typed) text.
        result: The RouteResult from IntentRouter.dispatch.

    Returns:
        String in the format: '> "transcript" -> action: detail'
    """
    return f'> "{transcript}" -> {result.action}: {result.detail}'


# ---------------------------------------------------------------------------
# VoicePipeline
# ---------------------------------------------------------------------------


class VoicePipeline:
    """Composes the full voice command pipeline.

    Combines AudioCapture, STTProvider, IntentClassifier, and IntentRouter
    into a single orchestrator. STT is lazy-loaded on first audio call.

    Args:
        config: VoiceConfig with hotkey, model_size, thresholds.
        router: IntentRouter instance for dispatching intents.
    """

    def __init__(
        self,
        config: VoiceConfig,
        router: IntentRouter,
        mapper_processes: dict[str, Any] | None = None,
    ) -> None:
        self._config = config
        self._stt = None  # lazy-loaded on first process_audio call
        self._classifier = IntentClassifier()
        self._router = router
        self._capture: AudioCapture | None = None  # lazy
        # mapper_processes: keyed by project_root, value is asyncio.subprocess.Process
        self._mapper_processes: dict[str, Any] = mapper_processes or {}
        self._log = structlog.get_logger(__name__)

    def _ensure_stt(self):  # type: ignore[return]
        """Lazy-load STTProvider on first audio processing call."""
        if self._stt is None:
            from lattice.orchestrator.voice.stt import STTProvider
            self._stt = STTProvider(
                self._config.model_size,
                self._config.deepgram_api_key,
            )
        return self._stt

    def process_text(self, text: str) -> RouteResult:
        """Process typed text through intent classifier and router (text fallback).

        Skips audio capture and STT. Routes text directly through the same
        IntentClassifier -> IntentRouter pipeline as voice input.

        Args:
            text: The typed command text to classify and route.

        Returns:
            RouteResult from IntentRouter.dispatch.
        """
        intent = self._classifier.classify(text)
        return self._router.dispatch(intent)

    def process_audio(self, audio_np: np.ndarray) -> RouteResult:
        """Process captured audio through full STT -> intent -> route pipeline.

        Args:
            audio_np: Numpy array of 16kHz mono int16 audio samples.

        Returns:
            RouteResult from IntentRouter.dispatch, or empty_transcript result
            if STT produces no speech.
        """
        from lattice.orchestrator.voice.capture import AudioCapture as _AudioCapture

        stt = self._ensure_stt()
        capture = _AudioCapture()
        wav_bytes = capture.to_wav_bytes(audio_np)
        transcript = stt.transcribe_with_fallback(
            audio_np, wav_bytes, self._config.confidence_threshold
        )

        if not transcript.strip():
            return RouteResult(
                success=False,
                action="empty_transcript",
                detail="No speech detected",
            )

        intent = self._classifier.classify(transcript)
        result = self._router.dispatch(intent)
        self._log.info(
            "voice_command_processed",
            transcript=transcript,
            action=result.action,
            voice_request_id=str(uuid.uuid4()),
        )
        return result

    async def complete_mapper_dispatch(self, result: RouteResult) -> RouteResult:
        """Complete NDJSON I/O for a mapper_dispatch_pending RouteResult.

        Writes the mapper command to the subprocess stdin via write_message,
        reads the response via read_message, and returns a completed RouteResult.

        Args:
            result: A RouteResult with action="mapper_dispatch_pending" containing
                    data keys: command, target, project.

        Returns:
            RouteResult with action="mapper_dispatched" and subprocess response data,
            or action="mapper_dispatch_failed" on I/O error or missing process.
        """
        project = result.data.get("project")
        command = result.data.get("command", "")
        target = result.data.get("target", ".")

        proc = self._mapper_processes.get(project) if project else None
        if proc is None or proc.returncode is not None:
            self._log.error("mapper_dispatch_no_process", project=project)
            return RouteResult(
                success=False,
                action="mapper_dispatch_failed",
                detail=f"No live mapper process for project {project!r}",
                data=dict(result.data),
            )

        try:
            payload = {"command": command, "payload": {"target": target}}
            await write_message(proc.stdin, payload)
            response = await read_message(proc.stdout)
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            self._log.error("mapper_dispatch_io_error", project=project, error=str(exc))
            return RouteResult(
                success=False,
                action="mapper_dispatch_failed",
                detail=f"I/O error dispatching to mapper: {exc}",
                data=dict(result.data),
            )

        if response is None:
            self._log.error("mapper_dispatch_eof", project=project)
            return RouteResult(
                success=False,
                action="mapper_dispatch_failed",
                detail=f"Mapper subprocess returned EOF for {command}",
                data=dict(result.data),
            )

        self._log.info(
            "mapper_dispatch_completed",
            command=command,
            target=target,
            project=project,
            success=response.get("success"),
        )
        return RouteResult(
            success=response.get("success", False),
            action="mapper_dispatched",
            detail=f"{command} {target} -> {project}",
            data={
                "command": command,
                "target": target,
                "project": project,
                "response": response,
            },
        )

    async def process_text_async(self, text: str) -> RouteResult:
        """Process typed text with async NDJSON completion for mapper dispatch.

        Like process_text but awaits NDJSON I/O when the router returns
        mapper_dispatch_pending. Use this from async callers (run_listener,
        CLI async context) to get the full round-trip result.

        Args:
            text: The typed command text to classify and route.

        Returns:
            RouteResult — completed mapper_dispatched or pass-through for other actions.
        """
        result = self.process_text(text)
        if result.action == "mapper_dispatch_pending":
            return await self.complete_mapper_dispatch(result)
        return result

    async def run_listener(self) -> None:
        """Main push-to-talk loop: listen for hotkey events, capture audio, process.

        Runs until cancelled or a KeyboardInterrupt is received.
        Calls beep_start on hotkey press (recording begins) and beep_stop on
        release (recording ends, processing starts).
        """
        from lattice.orchestrator.voice.capture import AudioCapture as _AudioCapture, check_microphone
        from lattice.orchestrator.voice.hotkey import HotkeyListener as _HotkeyListener

        if not check_microphone():
            raise RuntimeError(
                "No microphone detected. Push-to-talk requires an audio input device. "
                "Use --text for text-mode input instead."
            )

        loop = asyncio.get_running_loop()
        hotkey = _HotkeyListener(self._config.hotkey, loop)
        hotkey.start()
        self._log.info("voice_listener_started", hotkey=self._config.hotkey)
        capture = _AudioCapture()

        try:
            async for event in hotkey.events():
                if event == "press":
                    beep_start()
                    try:
                        capture.start()
                    except RuntimeError as exc:
                        self._log.error("audio_capture_failed", error=str(exc))
                        continue
                elif event == "release":
                    audio = capture.stop()
                    beep_stop()
                    if audio is None:
                        self._log.debug("voice_recording_too_short")
                        continue
                    result = self.process_audio(audio)
                    # Complete async NDJSON I/O if router returned pending dispatch
                    if result.action == "mapper_dispatch_pending":
                        result = await self.complete_mapper_dispatch(result)
                    # Use any transcript stored in data, or empty string
                    transcript = result.data.get("transcript", "")
                    display = format_voice_display(transcript, result)
                    click.echo(display)
        finally:
            hotkey.stop()
