"""Tests for lattice.ui.pty_manager.PTYManager.

Tests cover the full PTY lifecycle: spawn, write, read output,
resize, kill, list, shutdown, process exit detection, and
thread safety.

These tests spawn real processes (/bin/echo, /bin/cat, /bin/sh)
so they exercise actual PTY behavior — no mocking.
"""
from __future__ import annotations

import os
import platform
import threading
import time

import pytest

from lattice.ui.pty_manager import (
    PTYManager,
    TerminalInfo,
    TerminalStatus,
    _set_winsize,
)


@pytest.fixture
def output_collector():
    """Collects terminal output from callbacks in a thread-safe way."""
    collected = {}
    lock = threading.Lock()

    def on_output(pane_id: str, data: bytes) -> None:
        with lock:
            if pane_id not in collected:
                collected[pane_id] = bytearray()
            collected[pane_id].extend(data)

    def get(pane_id: str) -> bytes:
        with lock:
            return bytes(collected.get(pane_id, b""))

    return on_output, get


@pytest.fixture
def exit_collector():
    """Collects terminal exit events from callbacks in a thread-safe way."""
    exits = {}
    events = {}
    lock = threading.Lock()

    def on_exit(pane_id: str, exit_code: int | None) -> None:
        with lock:
            exits[pane_id] = exit_code
            if pane_id not in events:
                events[pane_id] = threading.Event()
            events[pane_id].set()

    def wait_for(pane_id: str, timeout: float = 5.0) -> int | None:
        with lock:
            if pane_id not in events:
                events[pane_id] = threading.Event()
            event = events[pane_id]
        event.wait(timeout=timeout)
        with lock:
            return exits.get(pane_id)

    return on_exit, wait_for


@pytest.fixture
def manager(output_collector, exit_collector):
    """Create a PTYManager and shut it down after the test."""
    on_output, _ = output_collector
    on_exit, _ = exit_collector
    mgr = PTYManager(on_output=on_output, on_exit=on_exit)
    yield mgr
    mgr.shutdown()


class TestSpawn:
    """Tests for PTYManager.spawn()."""

    def test_spawn_returns_pane_id(self, manager):
        pane_id = manager.spawn(cmd=["/bin/echo", "hello"])
        assert pane_id.startswith("pty-")
        assert len(pane_id) > 4

    def test_spawn_default_shell(self, manager):
        pane_id = manager.spawn()
        info = manager.get_terminal(pane_id)
        # Should spawn user's shell
        shell = os.environ.get("SHELL", "/bin/sh")
        assert info.cmd == (shell,)
        assert info.status == TerminalStatus.ALIVE

    def test_spawn_with_cwd(self, manager, tmp_path):
        pane_id = manager.spawn(cmd=["/bin/pwd"], cwd=str(tmp_path))
        info = manager.get_terminal(pane_id)
        assert info.cwd == str(tmp_path)

    def test_spawn_with_custom_dimensions(self, manager):
        pane_id = manager.spawn(cmd=["/bin/cat"], cols=120, rows=40)
        info = manager.get_terminal(pane_id)
        assert info.cols == 120
        assert info.rows == 40

    def test_spawn_unique_ids(self, manager):
        id1 = manager.spawn(cmd=["/bin/cat"])
        id2 = manager.spawn(cmd=["/bin/cat"])
        assert id1 != id2

    def test_spawn_after_shutdown_raises(self, manager):
        manager.shutdown()
        with pytest.raises(RuntimeError, match="shut down"):
            manager.spawn(cmd=["/bin/echo", "nope"])


class TestWrite:
    """Tests for PTYManager.write()."""

    def test_write_sends_data(self, manager, output_collector):
        _, get_output = output_collector
        pane_id = manager.spawn(cmd=["/bin/cat"])

        # Give cat time to start
        time.sleep(0.3)

        manager.write(pane_id, b"hello\n")
        time.sleep(0.5)

        output = get_output(pane_id)
        assert b"hello" in output

    def test_write_unknown_pane_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown"):
            manager.write("nonexistent", b"data")

    def test_write_dead_terminal_raises(self, manager, exit_collector):
        _, wait_for = exit_collector
        pane_id = manager.spawn(cmd=["/bin/echo", "done"])
        wait_for(pane_id, timeout=3.0)

        with pytest.raises(OSError, match="dead"):
            manager.write(pane_id, b"data")


class TestResize:
    """Tests for PTYManager.resize()."""

    def test_resize_updates_dimensions(self, manager):
        pane_id = manager.spawn(cmd=["/bin/cat"], cols=80, rows=24)
        manager.resize(pane_id, 120, 40)
        info = manager.get_terminal(pane_id)
        assert info.cols == 120
        assert info.rows == 40

    def test_resize_unknown_pane_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown"):
            manager.resize("nonexistent", 80, 24)

    def test_resize_dead_terminal_is_noop(self, manager, exit_collector):
        _, wait_for = exit_collector
        pane_id = manager.spawn(cmd=["/bin/echo", "done"])
        wait_for(pane_id, timeout=3.0)
        # Should not raise, just return
        manager.resize(pane_id, 120, 40)


class TestKill:
    """Tests for PTYManager.kill()."""

    def test_kill_removes_terminal(self, manager):
        pane_id = manager.spawn(cmd=["/bin/cat"])
        assert len(manager.list_terminals()) == 1
        manager.kill(pane_id)
        assert len(manager.list_terminals()) == 0

    def test_kill_unknown_pane_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown"):
            manager.kill("nonexistent")

    def test_kill_terminates_process(self, manager):
        pane_id = manager.spawn(cmd=["/bin/sleep", "300"])
        info = manager.get_terminal(pane_id)
        pid = info.pid

        manager.kill(pane_id)

        # Process should no longer be running
        time.sleep(0.2)
        try:
            os.kill(pid, 0)
            pytest.fail("Process should have been killed")
        except ProcessLookupError:
            pass  # Expected — process is gone


class TestListTerminals:
    """Tests for PTYManager.list_terminals()."""

    def test_list_empty(self, manager):
        assert manager.list_terminals() == []

    def test_list_returns_info(self, manager):
        pane_id = manager.spawn(cmd=["/bin/cat"])
        terminals = manager.list_terminals()
        assert len(terminals) == 1
        info = terminals[0]
        assert isinstance(info, TerminalInfo)
        assert info.pane_id == pane_id
        assert info.cmd == ("/bin/cat",)
        assert info.status == TerminalStatus.ALIVE
        assert info.pid > 0

    def test_list_multiple_terminals(self, manager):
        manager.spawn(cmd=["/bin/cat"])
        manager.spawn(cmd=["/bin/cat"])
        manager.spawn(cmd=["/bin/cat"])
        assert len(manager.list_terminals()) == 3

    def test_list_shows_dead_terminals(self, manager, exit_collector):
        _, wait_for = exit_collector
        pane_id = manager.spawn(cmd=["/bin/echo", "bye"])
        wait_for(pane_id, timeout=3.0)

        terminals = manager.list_terminals()
        assert len(terminals) == 1
        assert terminals[0].status == TerminalStatus.DEAD


class TestGetTerminal:
    """Tests for PTYManager.get_terminal()."""

    def test_get_returns_info(self, manager):
        pane_id = manager.spawn(cmd=["/bin/cat"], cols=100, rows=30)
        info = manager.get_terminal(pane_id)
        assert info.pane_id == pane_id
        assert info.cols == 100
        assert info.rows == 30

    def test_get_unknown_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown"):
            manager.get_terminal("nonexistent")


class TestOutputCallback:
    """Tests for output delivery via callback."""

    def test_output_received(self, manager, output_collector):
        _, get_output = output_collector
        pane_id = manager.spawn(cmd=["/bin/echo", "test output"])

        # Wait for output
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            output = get_output(pane_id)
            if b"test output" in output:
                break
            time.sleep(0.1)

        assert b"test output" in get_output(pane_id)


class TestExitDetection:
    """Tests for process exit detection and callback."""

    def test_exit_detected(self, manager, exit_collector):
        _, wait_for = exit_collector
        pane_id = manager.spawn(cmd=["/usr/bin/true"])
        exit_code = wait_for(pane_id, timeout=3.0)
        assert exit_code == 0

    def test_exit_nonzero_code(self, manager, exit_collector):
        _, wait_for = exit_collector
        pane_id = manager.spawn(cmd=["/usr/bin/false"])
        exit_code = wait_for(pane_id, timeout=3.0)
        assert exit_code != 0

    def test_exit_marks_terminal_dead(self, manager, exit_collector):
        _, wait_for = exit_collector
        pane_id = manager.spawn(cmd=["/usr/bin/true"])
        wait_for(pane_id, timeout=3.0)

        info = manager.get_terminal(pane_id)
        assert info.status == TerminalStatus.DEAD


class TestShutdown:
    """Tests for PTYManager.shutdown()."""

    def test_shutdown_kills_all(self, output_collector, exit_collector):
        on_output, _ = output_collector
        on_exit, _ = exit_collector
        mgr = PTYManager(on_output=on_output, on_exit=on_exit)

        mgr.spawn(cmd=["/bin/sleep", "300"])
        mgr.spawn(cmd=["/bin/sleep", "300"])
        mgr.spawn(cmd=["/bin/sleep", "300"])
        assert len(mgr.list_terminals()) == 3

        mgr.shutdown()
        assert len(mgr.list_terminals()) == 0

    def test_shutdown_idempotent(self, output_collector, exit_collector):
        on_output, _ = output_collector
        on_exit, _ = exit_collector
        mgr = PTYManager(on_output=on_output, on_exit=on_exit)
        mgr.spawn(cmd=["/bin/cat"])
        mgr.shutdown()
        mgr.shutdown()  # Should not raise


class TestThreadSafety:
    """Tests for thread safety of PTYManager operations."""

    def test_concurrent_spawn_and_list(self, manager):
        errors = []

        def spawn_many():
            try:
                for _ in range(5):
                    manager.spawn(cmd=["/bin/cat"])
            except Exception as exc:
                errors.append(exc)

        def list_many():
            try:
                for _ in range(10):
                    manager.list_terminals()
                    time.sleep(0.01)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=spawn_many)
        t2 = threading.Thread(target=list_many)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert errors == [], f"Thread safety errors: {errors}"
        assert len(manager.list_terminals()) == 5


class TestSetWinsize:
    """Tests for the _set_winsize helper."""

    def test_set_winsize_on_pty(self):
        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        try:
            _set_winsize(master, 50, 120)
            result = struct.unpack(
                "HHHH",
                fcntl.ioctl(master, termios.TIOCGWINSZ, b"\x00" * 8),
            )
            assert result[0] == 50   # rows
            assert result[1] == 120  # cols
        finally:
            os.close(master)
            os.close(slave)
