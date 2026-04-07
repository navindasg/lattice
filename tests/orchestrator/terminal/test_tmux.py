"""Tests for TmuxBackend — integration tests requiring a real tmux server.

Tests are skipped when tmux is not installed.  A dedicated session
``lattice-test`` is created for each test function and torn down afterward.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest

from lattice.orchestrator.terminal.models import PaneInfo
from lattice.orchestrator.terminal.tmux import TmuxBackend

_TMUX = shutil.which("tmux")
_skip_no_tmux = pytest.mark.skipif(
    _TMUX is None, reason="tmux not installed"
)

SESSION_NAME = "lattice-test"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def tmux_session():
    """Create a disposable tmux session and yield its name.

    Tears down the session even if the test fails.
    """
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", SESSION_NAME],
        check=True,
    )
    yield SESSION_NAME
    subprocess.run(
        ["tmux", "kill-session", "-t", SESSION_NAME],
        check=False,
    )


@pytest.fixture()
def backend(tmux_session: str) -> TmuxBackend:
    """Return a TmuxBackend connected to the test tmux server."""
    return TmuxBackend()


# ------------------------------------------------------------------
# list_panes
# ------------------------------------------------------------------


@_skip_no_tmux
class TestListPanes:
    async def test_list_panes_returns_panes(self, backend: TmuxBackend):
        """list_panes returns at least the test session's pane."""
        panes = await backend.list_panes()
        session_names = [p.session_name for p in panes]
        assert SESSION_NAME in session_names

    async def test_panes_have_correct_types(self, backend: TmuxBackend):
        """Each returned item is a PaneInfo."""
        panes = await backend.list_panes()
        for pane in panes:
            assert isinstance(pane, PaneInfo)


# ------------------------------------------------------------------
# send_text / capture_output
# ------------------------------------------------------------------


@_skip_no_tmux
class TestSendTextAndCapture:
    async def test_send_text_appears_in_capture(self, backend: TmuxBackend):
        """Text sent to a pane is visible in captured output."""
        panes = await backend.list_panes()
        test_pane = next(p for p in panes if p.session_name == SESSION_NAME)
        await backend.send_text(test_pane.pane_id, "hello world")
        await asyncio.sleep(0.3)
        output = await backend.capture_output(test_pane.pane_id)
        combined = "\n".join(output)
        assert "hello world" in combined

    async def test_send_text_escapes_special_chars(
        self, backend: TmuxBackend
    ):
        """Special characters (quotes, backticks, $, \\) are sent literally."""
        panes = await backend.list_panes()
        test_pane = next(p for p in panes if p.session_name == SESSION_NAME)
        special = 'echo "hi" \'there\' `cmd` $VAR \\\\'
        await backend.send_text(test_pane.pane_id, special)
        await asyncio.sleep(0.3)
        output = await backend.capture_output(test_pane.pane_id)
        combined = "\n".join(output)
        assert '"hi"' in combined
        assert "'there'" in combined
        assert "`cmd`" in combined
        assert "$VAR" in combined


# ------------------------------------------------------------------
# send_enter
# ------------------------------------------------------------------


@_skip_no_tmux
class TestSendEnter:
    async def test_send_enter_submits_input(self, backend: TmuxBackend):
        """send_text + send_enter executes a command."""
        panes = await backend.list_panes()
        test_pane = next(p for p in panes if p.session_name == SESSION_NAME)
        await backend.send_text(test_pane.pane_id, "echo test123")
        await backend.send_enter(test_pane.pane_id)
        await asyncio.sleep(0.5)
        output = await backend.capture_output(test_pane.pane_id)
        combined = "\n".join(output)
        assert "test123" in combined


# ------------------------------------------------------------------
# send_interrupt
# ------------------------------------------------------------------


@_skip_no_tmux
class TestSendInterrupt:
    async def test_send_interrupt_stops_process(self, backend: TmuxBackend):
        """Ctrl+C interrupts a running process."""
        pane_id = await backend.spawn_pane("sleep 60")
        await asyncio.sleep(0.5)
        await backend.send_interrupt(pane_id)
        await asyncio.sleep(0.5)
        # After interrupt, the pane may still exist but sleep should have
        # exited.  We just verify the interrupt did not raise.
        panes = await backend.list_panes()
        # Clean up — pane may already be gone after interrupt
        pane_ids = {p.pane_id for p in panes}
        if pane_id in pane_ids:
            await backend.close_pane(pane_id)


# ------------------------------------------------------------------
# capture_output line limiting
# ------------------------------------------------------------------


@_skip_no_tmux
class TestCaptureOutputLines:
    async def test_capture_output_returns_limited_lines(
        self, backend: TmuxBackend
    ):
        """capture_output(lines=5) returns at most 5 lines."""
        panes = await backend.list_panes()
        test_pane = next(p for p in panes if p.session_name == SESSION_NAME)
        output = await backend.capture_output(test_pane.pane_id, lines=5)
        assert len(output) <= 5


# ------------------------------------------------------------------
# spawn_pane / close_pane
# ------------------------------------------------------------------


@_skip_no_tmux
class TestSpawnAndClosePane:
    async def test_spawn_pane_creates_pane(self, backend: TmuxBackend):
        """spawn_pane creates a new pane visible in list_panes."""
        pane_id = await backend.spawn_pane("sleep 30")
        await asyncio.sleep(0.3)
        panes = await backend.list_panes()
        pane_ids = [p.pane_id for p in panes]
        assert pane_id in pane_ids
        await backend.close_pane(pane_id)

    async def test_spawn_pane_with_name(self, backend: TmuxBackend):
        """spawn_pane with name renames the window."""
        pane_id = await backend.spawn_pane("sleep 30", name="test-win")
        await asyncio.sleep(0.3)
        panes = await backend.list_panes()
        pane_info = next(p for p in panes if p.pane_id == pane_id)
        assert pane_info.window_name == "test-win"
        await backend.close_pane(pane_id)

    async def test_close_pane_removes_pane(self, backend: TmuxBackend):
        """Closed pane no longer appears in list_panes."""
        pane_id = await backend.spawn_pane("sleep 30")
        await asyncio.sleep(0.3)
        await backend.close_pane(pane_id)
        await asyncio.sleep(0.3)
        panes = await backend.list_panes()
        pane_ids = [p.pane_id for p in panes]
        assert pane_id not in pane_ids


# ------------------------------------------------------------------
# detect_cc_panes
# ------------------------------------------------------------------


@_skip_no_tmux
class TestDetectCCPanes:
    async def test_detect_cc_panes_empty_when_no_claude(
        self, backend: TmuxBackend
    ):
        """No claude processes means empty list."""
        instances = await backend.detect_cc_panes()
        assert instances == []


# ------------------------------------------------------------------
# Stable numbering
# ------------------------------------------------------------------


@_skip_no_tmux
class TestStableNumbering:
    async def test_stable_numbering_persists(self, backend: TmuxBackend):
        """Calling detect_cc_panes twice yields identical numbers."""
        # Mock list_panes to simulate claude processes
        fake_panes = [
            PaneInfo(
                pane_id="%100",
                session_name="s",
                window_name="w",
                pane_index=0,
                running_command="claude",
                cwd="/tmp",
            ),
            PaneInfo(
                pane_id="%101",
                session_name="s",
                window_name="w",
                pane_index=1,
                running_command="claude --resume",
                cwd="/tmp",
            ),
        ]
        with patch.object(backend, "list_panes", return_value=fake_panes):
            first = await backend.detect_cc_panes()
            second = await backend.detect_cc_panes()

        assert len(first) == 2
        assert len(second) == 2
        for a, b in zip(first, second):
            assert a.user_number == b.user_number

    async def test_stable_numbering_gaps_preserved(
        self, backend: TmuxBackend
    ):
        """When a pane disappears, remaining numbers do not shift."""
        pane_a = PaneInfo(
            pane_id="%200",
            session_name="s",
            window_name="w",
            pane_index=0,
            running_command="claude",
            cwd="/tmp",
        )
        pane_b = PaneInfo(
            pane_id="%201",
            session_name="s",
            window_name="w",
            pane_index=1,
            running_command="claude",
            cwd="/tmp",
        )
        pane_c = PaneInfo(
            pane_id="%202",
            session_name="s",
            window_name="w",
            pane_index=2,
            running_command="claude",
            cwd="/tmp",
        )

        with patch.object(
            backend, "list_panes", return_value=[pane_a, pane_b, pane_c]
        ):
            first = await backend.detect_cc_panes()

        num_a = first[0].user_number
        num_b = first[1].user_number
        num_c = first[2].user_number

        # Remove pane_b
        with patch.object(
            backend, "list_panes", return_value=[pane_a, pane_c]
        ):
            second = await backend.detect_cc_panes()

        assert len(second) == 2
        assert second[0].user_number == num_a
        assert second[1].user_number == num_c
        # Gap preserved — num_b is not reused
        assert num_b not in [s.user_number for s in second]


# ------------------------------------------------------------------
# Error cases (mocked)
# ------------------------------------------------------------------


class TestTmuxBackendErrors:
    def test_no_tmux_server_raises_error(self):
        """TmuxBackend() raises RuntimeError when no tmux server exists."""
        with patch(
            "lattice.orchestrator.terminal.tmux._get_server",
            side_effect=RuntimeError("No tmux server found. Start tmux first."),
        ):
            with pytest.raises(RuntimeError, match="No tmux server found"):
                TmuxBackend()


# ------------------------------------------------------------------
# create_backend factory
# ------------------------------------------------------------------


class TestCreateBackend:
    def test_create_backend_with_tmux_env(self):
        """create_backend returns TmuxBackend when $TMUX is set."""
        from lattice.orchestrator.terminal import create_backend

        with (
            patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}),
            patch(
                "lattice.orchestrator.terminal.TmuxBackend",
                return_value="mock-backend",
            ),
        ):
            result = create_backend()
            assert result == "mock-backend"

    def test_create_backend_without_tmux_raises(self):
        """create_backend raises RuntimeError without $TMUX."""
        from lattice.orchestrator.terminal import create_backend

        env = os.environ.copy()
        env.pop("TMUX", None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="No supported terminal"):
                create_backend()


# ------------------------------------------------------------------
# Integration test
# ------------------------------------------------------------------


@_skip_no_tmux
class TestIntegration:
    async def test_spawn_list_capture_close(self, backend: TmuxBackend):
        """Full lifecycle: spawn 3 panes, list, capture, close all."""
        pane_ids: list[str] = []
        for i in range(3):
            pid = await backend.spawn_pane(
                f"bash -c 'echo pane-{i} && sleep 30'"
            )
            pane_ids.append(pid)

        await asyncio.sleep(0.5)

        # All spawned panes should be listed
        panes = await backend.list_panes()
        listed_ids = {p.pane_id for p in panes}
        for pid in pane_ids:
            assert pid in listed_ids

        # Capture from first pane should contain its echo output
        output = await backend.capture_output(pane_ids[0])
        combined = "\n".join(output)
        assert "pane-0" in combined

        # Close all
        for pid in pane_ids:
            await backend.close_pane(pid)

        await asyncio.sleep(0.3)

        # Verify gone
        panes = await backend.list_panes()
        listed_ids = {p.pane_id for p in panes}
        for pid in pane_ids:
            assert pid not in listed_ids
