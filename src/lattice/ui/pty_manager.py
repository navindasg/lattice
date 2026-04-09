"""PTY-backed terminal process manager for the Lattice dashboard.

Replaces tmux-based terminal management with direct PTY control.
Each managed terminal gets its own pseudo-terminal pair (master/slave),
a subprocess, and a background reader thread that delivers output
via callbacks.

Thread safety: all public methods are protected by a lock over the
terminal registry.  Reader threads only call the output/exit callbacks,
which must themselves be thread-safe (e.g. posting to an event loop).
"""
from __future__ import annotations

import errno
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import structlog

log = structlog.get_logger(__name__)

# Callback type aliases
OutputCallback = Callable[[str, bytes], None]
ExitCallback = Callable[[str, int | None], None]

# Default terminal dimensions
_DEFAULT_COLS = 80
_DEFAULT_ROWS = 24

# Reader thread buffer size (matches typical PTY reads)
_READ_BUF_SIZE = 4096

# Grace period before SIGKILL after SIGTERM (seconds)
_KILL_GRACE_SECONDS = 3.0


class TerminalStatus(str, Enum):
    """Lifecycle status of a managed terminal."""

    ALIVE = "alive"
    DEAD = "dead"


@dataclass(frozen=True)
class TerminalInfo:
    """Snapshot of a managed terminal's state.

    Frozen dataclass — safe to pass across threads and serialize.
    """

    pane_id: str
    cmd: tuple[str, ...]
    cwd: str
    pid: int
    status: TerminalStatus
    exit_code: int | None
    cols: int
    rows: int
    created_at: float


class _Terminal:
    """Internal mutable state for a single managed terminal.

    Not exposed outside this module — callers see TerminalInfo snapshots.
    """

    __slots__ = (
        "pane_id",
        "cmd",
        "cwd",
        "master_fd",
        "slave_fd",
        "process",
        "reader_thread",
        "status",
        "exit_code",
        "cols",
        "rows",
        "created_at",
        "_stop_event",
    )

    def __init__(
        self,
        pane_id: str,
        cmd: tuple[str, ...],
        cwd: str,
        master_fd: int,
        slave_fd: int,
        process: subprocess.Popen,
        cols: int,
        rows: int,
    ) -> None:
        self.pane_id = pane_id
        self.cmd = cmd
        self.cwd = cwd
        self.master_fd = master_fd
        self.slave_fd = slave_fd
        self.process = process
        self.reader_thread: threading.Thread | None = None
        self.status = TerminalStatus.ALIVE
        self.exit_code: int | None = None
        self.cols = cols
        self.rows = rows
        self.created_at = time.monotonic()
        self._stop_event = threading.Event()

    def to_info(self) -> TerminalInfo:
        return TerminalInfo(
            pane_id=self.pane_id,
            cmd=self.cmd,
            cwd=self.cwd,
            pid=self.process.pid,
            status=self.status,
            exit_code=self.exit_code,
            cols=self.cols,
            rows=self.rows,
            created_at=self.created_at,
        )


class PTYManager:
    """Manages PTY-backed terminal processes for the dashboard.

    Spawns processes with real pseudo-terminals, delivers output via
    callbacks, and handles resize/kill/shutdown lifecycle.

    All public methods are thread-safe.
    """

    def __init__(
        self,
        on_output: OutputCallback | None = None,
        on_exit: ExitCallback | None = None,
    ) -> None:
        self._terminals: dict[str, _Terminal] = {}
        self._lock = threading.Lock()
        self._on_output = on_output
        self._on_exit = on_exit
        self._shutdown_event = threading.Event()
        log.info("pty_manager.init")

    def _validate_pane_id(self, pane_id: str) -> _Terminal:
        """Look up a terminal by pane_id. Caller must hold self._lock."""
        term = self._terminals.get(pane_id)
        if term is None:
            raise ValueError(f"Unknown terminal pane_id: {pane_id!r}")
        return term

    def spawn(
        self,
        cmd: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = _DEFAULT_COLS,
        rows: int = _DEFAULT_ROWS,
    ) -> str:
        """Spawn a new terminal process with a PTY.

        Args:
            cmd: Command to run. Defaults to user's login shell.
            cwd: Working directory. Defaults to home directory.
            env: Extra environment variables (merged with os.environ).
            cols: Initial terminal width in columns.
            rows: Initial terminal height in rows.

        Returns:
            A unique pane_id string identifying this terminal.

        Raises:
            OSError: If PTY creation or process spawn fails.
        """
        if self._shutdown_event.is_set():
            raise RuntimeError("PTYManager is shut down")

        if cmd is None:
            shell = os.environ.get("SHELL", "/bin/sh")
            cmd = [shell]

        if cwd is None:
            cwd = os.path.expanduser("~")

        pane_id = f"pty-{uuid.uuid4().hex[:12]}"

        # Create PTY pair
        master_fd, slave_fd = pty.openpty()

        try:
            # Set initial terminal size
            _set_winsize(master_fd, rows, cols)

            # Make master fd non-blocking for reads
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # Build environment
            proc_env = {**os.environ}
            proc_env["TERM"] = "xterm-256color"
            proc_env["COLORTERM"] = "truecolor"
            if env:
                proc_env.update(env)

            # Spawn the process with slave PTY as stdin/stdout/stderr
            process = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=proc_env,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise

        # Close slave fd in parent — the child has its own copy
        os.close(slave_fd)

        term = _Terminal(
            pane_id=pane_id,
            cmd=tuple(cmd),
            cwd=cwd,
            master_fd=master_fd,
            slave_fd=-1,  # closed in parent
            process=process,
            cols=cols,
            rows=rows,
        )

        # Start background reader thread
        reader = threading.Thread(
            target=self._reader_loop,
            args=(term,),
            daemon=True,
            name=f"pty-reader-{pane_id}",
        )
        term.reader_thread = reader

        with self._lock:
            self._terminals[pane_id] = term

        reader.start()

        log.info(
            "pty_manager.spawn",
            pane_id=pane_id,
            cmd=cmd,
            cwd=cwd,
            pid=process.pid,
            cols=cols,
            rows=rows,
        )
        return pane_id

    def write(self, pane_id: str, data: bytes) -> None:
        """Write input data to a terminal's PTY.

        Args:
            pane_id: Terminal identifier from spawn().
            data: Raw bytes to send (keyboard input, escape sequences, etc.).

        Raises:
            ValueError: If pane_id is unknown.
            OSError: If the PTY write fails or terminal is dead.
        """
        with self._lock:
            term = self._validate_pane_id(pane_id)
            if term.status != TerminalStatus.ALIVE:
                raise OSError(f"Terminal {pane_id} is dead")
            if term.master_fd < 0:
                raise OSError(f"Terminal {pane_id} fd is closed")
            os.write(term.master_fd, data)

    def resize(self, pane_id: str, cols: int, rows: int) -> None:
        """Resize a terminal's PTY via TIOCSWINSZ ioctl.

        Args:
            pane_id: Terminal identifier from spawn().
            cols: New terminal width.
            rows: New terminal height.

        Raises:
            ValueError: If pane_id is unknown.
        """
        with self._lock:
            term = self._validate_pane_id(pane_id)
            if term.status != TerminalStatus.ALIVE:
                return
            if term.master_fd < 0:
                return
            term.cols = cols
            term.rows = rows
            _set_winsize(term.master_fd, rows, cols)
        log.debug("pty_manager.resize", pane_id=pane_id, cols=cols, rows=rows)

    def kill(self, pane_id: str) -> None:
        """Kill a terminal process and clean up its PTY.

        Sends SIGTERM first, waits briefly, then SIGKILL if needed.
        Closes the master fd and joins the reader thread.

        Args:
            pane_id: Terminal identifier from spawn().

        Raises:
            ValueError: If pane_id is unknown.
        """
        with self._lock:
            term = self._validate_pane_id(pane_id)

        self._terminate(term)

        with self._lock:
            self._terminals.pop(pane_id, None)

        log.info("pty_manager.kill", pane_id=pane_id)

    def list_terminals(self) -> list[TerminalInfo]:
        """List all managed terminals (alive and dead).

        Returns:
            List of immutable TerminalInfo snapshots.
        """
        with self._lock:
            return [term.to_info() for term in self._terminals.values()]

    def get_terminal(self, pane_id: str) -> TerminalInfo:
        """Get info for a single terminal.

        Args:
            pane_id: Terminal identifier from spawn().

        Returns:
            Immutable TerminalInfo snapshot.

        Raises:
            ValueError: If pane_id is unknown.
        """
        with self._lock:
            term = self._validate_pane_id(pane_id)
            return term.to_info()

    def shutdown(self) -> None:
        """Kill all terminals and clean up all resources.

        Safe to call multiple times.  After shutdown, spawn() will raise.
        """
        self._shutdown_event.set()

        with self._lock:
            terminals = list(self._terminals.values())
            self._terminals.clear()

        for term in terminals:
            self._terminate(term)

        log.info("pty_manager.shutdown", terminated=len(terminals))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _terminate(self, term: _Terminal) -> None:
        """Terminate a single terminal: stop process, close fd, join thread."""
        term._stop_event.set()

        # Terminate the process
        proc = term.process
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

            try:
                proc.wait(timeout=_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

        term.exit_code = proc.returncode
        term.status = TerminalStatus.DEAD

        # Close master fd
        if term.master_fd >= 0:
            try:
                os.close(term.master_fd)
            except OSError:
                pass
            term.master_fd = -1

        # Join reader thread
        if term.reader_thread is not None and term.reader_thread.is_alive():
            term.reader_thread.join(timeout=2.0)

    def _reader_loop(self, term: _Terminal) -> None:
        """Background reader: reads master fd, delivers output via callback.

        Runs until the process exits or stop_event is set.
        Detects process death when read returns empty or raises EIO.
        """
        fd = term.master_fd
        pane_id = term.pane_id
        stop = term._stop_event

        log.debug("pty_reader.start", pane_id=pane_id)

        try:
            while not stop.is_set():
                try:
                    data = os.read(fd, _READ_BUF_SIZE)
                except BlockingIOError:
                    # Non-blocking fd, no data available — brief sleep
                    stop.wait(0.01)
                    continue
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        # EIO means the slave side closed — process exited
                        break
                    if exc.errno == errno.EBADF:
                        break
                    raise

                if not data:
                    break

                if self._on_output is not None:
                    try:
                        self._on_output(pane_id, data)
                    except Exception:
                        log.exception(
                            "pty_reader.callback_error", pane_id=pane_id
                        )
        except Exception:
            log.exception("pty_reader.unexpected_error", pane_id=pane_id)
        finally:
            # Mark terminal as dead and notify.
            # Only set exit_code if not already set by _terminate() to
            # avoid overwriting a correct value with None.
            exit_code = term.process.poll()
            if not stop.is_set():
                term.exit_code = exit_code
                term.status = TerminalStatus.DEAD

            log.info(
                "pty_reader.exit",
                pane_id=pane_id,
                exit_code=exit_code,
            )

            if self._on_exit is not None and not stop.is_set():
                try:
                    self._on_exit(pane_id, exit_code)
                except Exception:
                    log.exception(
                        "pty_reader.exit_callback_error", pane_id=pane_id
                    )


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    """Set the terminal window size on a PTY file descriptor.

    Uses the TIOCSWINSZ ioctl to inform the kernel (and thus the
    child process) of the new terminal dimensions.
    """
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
