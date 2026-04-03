"""Tests for VoicePipeline, beep functions, format_voice_display, and CLI command.

Tests cover:
    - VoicePipeline.process_text routes through classifier -> router
    - VoicePipeline.process_audio calls STT -> classify -> route
    - format_voice_display output format
    - beep_start / beep_stop subprocess calls on darwin
    - orchestrator:voice CLI command with --text flag
    - CLI --json flag output format
"""
from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from click.testing import CliRunner

from lattice.orchestrator.voice.models import IntentResult, VoiceConfig
from lattice.orchestrator.voice.pipeline import (
    VoicePipeline,
    beep_start,
    beep_stop,
    format_voice_display,
)
from lattice.orchestrator.voice.router import IntentRouter, RouteResult


# ---------------------------------------------------------------------------
# format_voice_display tests
# ---------------------------------------------------------------------------


def test_format_voice_display_basic() -> None:
    """format_voice_display returns '> "transcript" -> action: detail' string."""
    result = RouteResult(
        success=True,
        action="mapper_dispatched",
        detail="map:init auth",
    )
    display = format_voice_display("map the auth directory", result)
    assert display == '> "map the auth directory" -> mapper_dispatched: map:init auth'


def test_format_voice_display_empty_detail() -> None:
    """format_voice_display works with empty detail."""
    result = RouteResult(success=True, action="task_enqueued")
    display = format_voice_display("fix the bug", result)
    assert display == '> "fix the bug" -> task_enqueued: '


# ---------------------------------------------------------------------------
# VoicePipeline.process_text tests
# ---------------------------------------------------------------------------


def _make_pipeline() -> VoicePipeline:
    """Create a VoicePipeline with a real IntentRouter (no DB, no queue)."""
    config = VoiceConfig()
    router = IntentRouter()
    return VoicePipeline(config=config, router=router)


def test_process_text_mapper_dispatched() -> None:
    """process_text('map the auth directory') routes to mapper_dispatched."""
    pipeline = _make_pipeline()
    result = pipeline.process_text("map the auth directory")
    assert result.action == "mapper_dispatched"


def test_process_text_status_returned() -> None:
    """process_text("what's the status") routes to status_returned."""
    pipeline = _make_pipeline()
    result = pipeline.process_text("what's the status")
    assert result.action == "status_returned"


def test_process_text_unrecognized() -> None:
    """process_text("asdf gibberish") routes to unrecognized."""
    pipeline = _make_pipeline()
    result = pipeline.process_text("asdf gibberish")
    assert result.action == "unrecognized"


def test_process_text_task_dispatch() -> None:
    """process_text("start working on auth") routes to task_enqueued."""
    pipeline = _make_pipeline()
    result = pipeline.process_text("start working on auth")
    assert result.action == "task_enqueued"


def test_process_text_context_injection() -> None:
    """process_text("tell instance 1 about auth") routes to context_injected."""
    pipeline = _make_pipeline()
    result = pipeline.process_text("tell instance 1 about auth")
    assert result.action == "context_injected"


def test_process_text_routes_through_classifier_and_router() -> None:
    """process_text calls IntentClassifier.classify then IntentRouter.dispatch."""
    mock_classifier = MagicMock()
    mock_intent = IntentResult(
        category="task_dispatch",
        transcript="fix auth",
        confidence=0.9,
    )
    mock_classifier.classify.return_value = mock_intent

    mock_router = MagicMock()
    mock_route = RouteResult(success=True, action="task_enqueued")
    mock_router.dispatch.return_value = mock_route

    config = VoiceConfig()
    pipeline = VoicePipeline(config=config, router=mock_router)
    pipeline._classifier = mock_classifier

    result = pipeline.process_text("fix auth")

    mock_classifier.classify.assert_called_once_with("fix auth")
    mock_router.dispatch.assert_called_once_with(mock_intent)
    assert result.action == "task_enqueued"


# ---------------------------------------------------------------------------
# VoicePipeline.process_audio tests
# ---------------------------------------------------------------------------


def test_process_audio_calls_stt_then_classify_then_route() -> None:
    """process_audio calls STT -> classify -> router.dispatch in sequence."""
    config = VoiceConfig()

    mock_stt = MagicMock()
    mock_stt.transcribe_with_fallback.return_value = "fix the login bug"

    mock_classifier = MagicMock()
    mock_intent = IntentResult(
        category="task_dispatch",
        transcript="fix the login bug",
        confidence=0.9,
    )
    mock_classifier.classify.return_value = mock_intent

    mock_router = MagicMock()
    mock_route = RouteResult(success=True, action="task_enqueued", data={"task_id": "t1"})
    mock_router.dispatch.return_value = mock_route

    pipeline = VoicePipeline(config=config, router=mock_router)
    pipeline._stt = mock_stt
    pipeline._classifier = mock_classifier

    audio_np = np.zeros(16000, dtype=np.int16)
    result = pipeline.process_audio(audio_np)

    mock_stt.transcribe_with_fallback.assert_called_once()
    mock_classifier.classify.assert_called_once_with("fix the login bug")
    mock_router.dispatch.assert_called_once_with(mock_intent)
    assert result.action == "task_enqueued"


def test_process_audio_returns_route_result() -> None:
    """process_audio returns a RouteResult instance."""
    config = VoiceConfig()

    mock_stt = MagicMock()
    mock_stt.transcribe_with_fallback.return_value = "map the auth directory"

    mock_router = MagicMock()
    mock_route = RouteResult(success=True, action="mapper_dispatched")
    mock_router.dispatch.return_value = mock_route

    pipeline = VoicePipeline(config=config, router=mock_router)
    pipeline._stt = mock_stt

    audio_np = np.zeros(16000, dtype=np.int16)
    result = pipeline.process_audio(audio_np)

    assert isinstance(result, RouteResult)


def test_process_audio_empty_transcript_returns_empty_transcript_result() -> None:
    """process_audio with empty STT output returns empty_transcript action."""
    config = VoiceConfig()

    mock_stt = MagicMock()
    mock_stt.transcribe_with_fallback.return_value = "   "  # only whitespace

    pipeline = VoicePipeline(config=config, router=IntentRouter())
    pipeline._stt = mock_stt

    audio_np = np.zeros(16000, dtype=np.int16)
    result = pipeline.process_audio(audio_np)

    assert result.success is False
    assert result.action == "empty_transcript"


# ---------------------------------------------------------------------------
# beep_start / beep_stop tests
# ---------------------------------------------------------------------------


def test_beep_start_calls_osascript_on_darwin() -> None:
    """beep_start calls subprocess.run with osascript 'beep' on darwin."""
    with patch("lattice.orchestrator.voice.pipeline.sys") as mock_sys, \
         patch("lattice.orchestrator.voice.pipeline.subprocess.run") as mock_run:
        mock_sys.platform = "darwin"
        beep_start()
        mock_run.assert_called_once_with(
            ["osascript", "-e", "beep"],
            check=False,
            capture_output=True,
        )


def test_beep_stop_calls_osascript_beep2_on_darwin() -> None:
    """beep_stop calls subprocess.run with osascript 'beep 2' on darwin."""
    with patch("lattice.orchestrator.voice.pipeline.sys") as mock_sys, \
         patch("lattice.orchestrator.voice.pipeline.subprocess.run") as mock_run:
        mock_sys.platform = "darwin"
        beep_stop()
        mock_run.assert_called_once_with(
            ["osascript", "-e", "beep 2"],
            check=False,
            capture_output=True,
        )


def test_beep_start_prints_bell_on_non_darwin() -> None:
    """beep_start prints bell character on non-darwin platforms."""
    with patch("lattice.orchestrator.voice.pipeline.sys") as mock_sys, \
         patch("builtins.print") as mock_print:
        mock_sys.platform = "linux"
        beep_start()
        mock_print.assert_called_once_with("\a", end="", flush=True)


def test_beep_stop_prints_bell_on_non_darwin() -> None:
    """beep_stop prints bell character on non-darwin platforms."""
    with patch("lattice.orchestrator.voice.pipeline.sys") as mock_sys, \
         patch("builtins.print") as mock_print:
        mock_sys.platform = "linux"
        beep_stop()
        mock_print.assert_called_once_with("\a", end="", flush=True)


# ---------------------------------------------------------------------------
# CLI orchestrator:voice --text tests
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_cli_voice_text_mapper_dispatched(cli_runner: CliRunner) -> None:
    """orchestrator:voice --text 'map the auth directory' outputs mapper_dispatched."""
    from lattice.cli.commands import cli

    result = cli_runner.invoke(cli, ["orchestrator:voice", "--text", "map the auth directory"])

    assert result.exit_code == 0, result.output
    assert "mapper_dispatched" in result.output


def test_cli_voice_text_status_returned(cli_runner: CliRunner) -> None:
    """orchestrator:voice --text 'status' outputs status_returned."""
    from lattice.cli.commands import cli

    result = cli_runner.invoke(cli, ["orchestrator:voice", "--text", "what's the status"])

    assert result.exit_code == 0, result.output
    assert "status_returned" in result.output


def test_cli_voice_text_unrecognized(cli_runner: CliRunner) -> None:
    """orchestrator:voice --text 'asdfghjkl' outputs unrecognized."""
    from lattice.cli.commands import cli

    result = cli_runner.invoke(cli, ["orchestrator:voice", "--text", "asdfghjkl"])

    assert result.exit_code == 0, result.output
    assert "unrecognized" in result.output


def test_cli_voice_text_task_enqueued(cli_runner: CliRunner) -> None:
    """orchestrator:voice --text 'start working on auth' outputs task_enqueued."""
    from lattice.cli.commands import cli

    result = cli_runner.invoke(cli, ["orchestrator:voice", "--text", "start working on auth"])

    assert result.exit_code == 0, result.output
    assert "task_enqueued" in result.output


def test_cli_voice_text_json_flag(cli_runner: CliRunner) -> None:
    """orchestrator:voice --text 'map auth' --json returns valid JSON with success_response envelope.

    Note: structlog may emit JSON log lines before the final response JSON.
    We parse the last non-empty line as the response envelope.
    """
    from lattice.cli.commands import cli

    result = cli_runner.invoke(
        cli, ["orchestrator:voice", "--text", "map the auth directory", "--json"]
    )

    assert result.exit_code == 0, result.output
    # Last non-empty line is the success_response JSON (structlog may emit earlier lines)
    last_line = [line for line in result.output.strip().splitlines() if line.strip()][-1]
    data = json.loads(last_line)
    assert "success" in data
    assert "data" in data
    assert data["data"]["action"] == "mapper_dispatched"


def test_cli_voice_text_json_flag_status(cli_runner: CliRunner) -> None:
    """orchestrator:voice --text 'status' --json contains status_returned action."""
    from lattice.cli.commands import cli

    result = cli_runner.invoke(
        cli, ["orchestrator:voice", "--text", "what's the status", "--json"]
    )

    assert result.exit_code == 0, result.output
    last_line = [line for line in result.output.strip().splitlines() if line.strip()][-1]
    data = json.loads(last_line)
    assert data["data"]["action"] == "status_returned"


# ---------------------------------------------------------------------------
# VoicePipeline.complete_mapper_dispatch tests (Phase 14)
# ---------------------------------------------------------------------------


def _make_mock_proc_alive():
    """Create a mock process that appears alive (returncode=None)."""
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    return proc


def _make_pending_result(project="myapp", command="map:status", target="auth"):
    """Create a mapper_dispatch_pending RouteResult."""
    return RouteResult(
        success=True,
        action="mapper_dispatch_pending",
        detail=f"{command} {target} -> project {project}",
        data={"command": command, "target": target, "project": project},
    )


@pytest.mark.asyncio
async def test_complete_mapper_dispatch_writes_and_reads_ndjson() -> None:
    """complete_mapper_dispatch writes NDJSON command and reads response, returns mapper_dispatched."""
    from lattice.orchestrator.voice.pipeline import VoicePipeline
    from lattice.orchestrator.voice.models import VoiceConfig

    mock_proc = _make_mock_proc_alive()
    config = VoiceConfig()
    router = IntentRouter()
    pipeline = VoicePipeline(config=config, router=router, mapper_processes={"myapp": mock_proc})

    pending = _make_pending_result()

    mock_response = {"success": True, "command": "map:status", "data": {"phases": 3}, "error": None}

    with patch(
        "lattice.orchestrator.voice.pipeline.write_message",
        new_callable=AsyncMock,
    ) as mock_write, patch(
        "lattice.orchestrator.voice.pipeline.read_message",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_read:
        result = await pipeline.complete_mapper_dispatch(pending)

    assert result.action == "mapper_dispatched"
    assert result.success is True
    assert result.data["response"] == mock_response
    assert result.data["command"] == "map:status"
    assert result.data["project"] == "myapp"

    # Verify write_message was called with correct payload
    mock_write.assert_called_once()
    write_args = mock_write.call_args[0]
    assert write_args[0] is mock_proc.stdin
    payload = write_args[1]
    assert payload["command"] == "map:status"
    assert payload["payload"]["target"] == "auth"

    # Verify read_message was called on proc.stdout
    mock_read.assert_called_once_with(mock_proc.stdout)


@pytest.mark.asyncio
async def test_complete_mapper_dispatch_process_crashed() -> None:
    """complete_mapper_dispatch returns mapper_dispatch_failed when subprocess returncode is set."""
    from lattice.orchestrator.voice.pipeline import VoicePipeline
    from lattice.orchestrator.voice.models import VoiceConfig

    dead_proc = MagicMock()
    dead_proc.returncode = 1  # dead

    config = VoiceConfig()
    router = IntentRouter()
    pipeline = VoicePipeline(config=config, router=router, mapper_processes={"myapp": dead_proc})

    pending = _make_pending_result()
    result = await pipeline.complete_mapper_dispatch(pending)

    assert result.success is False
    assert result.action == "mapper_dispatch_failed"


@pytest.mark.asyncio
async def test_complete_mapper_dispatch_read_returns_none() -> None:
    """complete_mapper_dispatch returns mapper_dispatch_failed when read_message returns None (EOF)."""
    from lattice.orchestrator.voice.pipeline import VoicePipeline
    from lattice.orchestrator.voice.models import VoiceConfig

    mock_proc = _make_mock_proc_alive()
    config = VoiceConfig()
    router = IntentRouter()
    pipeline = VoicePipeline(config=config, router=router, mapper_processes={"myapp": mock_proc})

    pending = _make_pending_result()

    with patch(
        "lattice.orchestrator.voice.pipeline.write_message",
        new_callable=AsyncMock,
    ), patch(
        "lattice.orchestrator.voice.pipeline.read_message",
        new_callable=AsyncMock,
        return_value=None,  # EOF
    ):
        result = await pipeline.complete_mapper_dispatch(pending)

    assert result.success is False
    assert result.action == "mapper_dispatch_failed"
    assert "EOF" in result.detail


@pytest.mark.asyncio
async def test_process_text_async_completes_pending() -> None:
    """process_text_async calls complete_mapper_dispatch when router returns mapper_dispatch_pending."""
    from lattice.orchestrator.voice.pipeline import VoicePipeline
    from lattice.orchestrator.voice.models import VoiceConfig

    mock_proc = _make_mock_proc_alive()
    config = VoiceConfig()

    # Set up router to return mapper_dispatch_pending
    mock_router = MagicMock()
    pending_result = _make_pending_result()
    mock_router.dispatch.return_value = pending_result

    pipeline = VoicePipeline(config=config, router=mock_router, mapper_processes={"myapp": mock_proc})

    completed_result = RouteResult(
        success=True,
        action="mapper_dispatched",
        detail="done",
        data={"command": "map:status", "target": "auth", "project": "myapp", "response": {}},
    )

    with patch.object(
        pipeline, "complete_mapper_dispatch", new_callable=AsyncMock, return_value=completed_result
    ) as mock_complete:
        result = await pipeline.process_text_async("map status on auth")

    mock_complete.assert_called_once_with(pending_result)
    assert result.action == "mapper_dispatched"


@pytest.mark.asyncio
async def test_process_text_async_passthrough_non_pending() -> None:
    """process_text_async passes through non-pending results without calling complete_mapper_dispatch."""
    from lattice.orchestrator.voice.pipeline import VoicePipeline
    from lattice.orchestrator.voice.models import VoiceConfig

    config = VoiceConfig()
    mock_router = MagicMock()
    status_result = RouteResult(success=True, action="status_returned", data={"instances": []})
    mock_router.dispatch.return_value = status_result

    pipeline = VoicePipeline(config=config, router=mock_router)

    with patch.object(
        pipeline, "complete_mapper_dispatch", new_callable=AsyncMock
    ) as mock_complete:
        result = await pipeline.process_text_async("what's the status")

    # complete_mapper_dispatch should NOT have been called
    mock_complete.assert_not_called()
    assert result.action == "status_returned"
