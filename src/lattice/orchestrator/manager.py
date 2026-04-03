"""ProcessManager: CC instance lifecycle management with DuckDB-backed registry.

Provides:
- build_child_env: Build subprocess env with CLAUDECODE stripped/reset to empty string
- is_process_alive: Check process existence via os.kill(pid, 0)
- terminate_instance: Graceful SIGTERM -> SIGKILL shutdown with zombie reaping
- ProcessRegistry: DuckDB-backed process instance registry (same pattern as FleetCheckpoint)
- ProcessManager: Orchestrates spawn, health check, terminate, and orphan recovery

Design decisions (from RESEARCH.md):
- CLAUDECODE="" (empty string, not absent) — nested session rejection requires env var present
- os.kill(pid, 0) for health polling — cross-platform, no /proc dependency
- SIGTERM -> 10s timeout -> SIGKILL — graceful shutdown with forced cleanup
- INSERT OR REPLACE for idempotent upserts — same pattern as FleetCheckpoint
- Orphan detection on startup — prevents ghost processes from prior crashes
"""
from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

import duckdb
import structlog

from lattice.models.orchestrator import ManagedInstance
from lattice.orchestrator.models import OrchestratorConfig

log = structlog.get_logger(__name__)

# CC session vars that must be stripped/reset in child processes
_CC_SESSION_VARS = frozenset({"CLAUDECODE"})


def build_child_env(
    override: dict[str, str] | None = None,
    project_root: str | None = None,
    agent_docs: str | None = None,
) -> dict[str, str]:
    """Build subprocess env with CC session vars stripped.

    Sets CLAUDECODE="" (empty string, not absent) per locked decision:
    nested session rejection requires the env var to be present and empty,
    not absent.

    If project_root is provided, injects LATTICE_PROJECT_ROOT into the env.
    If agent_docs is provided, injects LATTICE_AGENT_DOCS into the env.
    These are applied before the override dict so override can still overwrite them.

    Args:
        override: Optional additional env vars to inject into the result.
        project_root: Optional per-project root path — injected as LATTICE_PROJECT_ROOT.
        agent_docs: Optional per-project agent docs path — injected as LATTICE_AGENT_DOCS.

    Returns:
        Dict of environment variables safe for CC child process.
    """
    filtered = {k: v for k, v in os.environ.items() if k not in _CC_SESSION_VARS}
    filtered["CLAUDECODE"] = ""
    if project_root is not None:
        filtered["LATTICE_PROJECT_ROOT"] = project_root
    if agent_docs is not None:
        filtered["LATTICE_AGENT_DOCS"] = agent_docs
    if override:
        filtered.update(override)
    return filtered


def is_process_alive(pid: int) -> bool:
    """Check if a process exists via os.kill(pid, 0).

    Returns True if alive (including PermissionError, which means the process
    exists but is owned by another user). Returns False only on ProcessLookupError
    (no such process).

    Args:
        pid: Process ID to check.

    Returns:
        True if process exists, False if it does not.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it
        return True


async def terminate_instance(
    proc: asyncio.subprocess.Process, timeout: float = 10.0
) -> str:
    """Graceful SIGTERM -> wait timeout -> SIGKILL with zombie reaping.

    Sends SIGTERM first, waits up to `timeout` seconds for the process to exit.
    If the timeout expires, sends SIGKILL and reaps the zombie via await proc.wait().

    Args:
        proc: The asyncio subprocess to terminate.
        timeout: Seconds to wait after SIGTERM before escalating to SIGKILL.

    Returns:
        "already_dead" if process had already exited.
        "graceful" if process exited within timeout after SIGTERM.
        "killed" if SIGKILL was required.
    """
    if proc.returncode is not None:
        return "already_dead"

    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return "graceful"
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()  # MUST reap zombie
        return "killed"


class ProcessRegistry:
    """DuckDB-backed process instance registry.

    Same pattern as FleetCheckpoint: constructor creates tables,
    uses INSERT OR REPLACE for idempotent upserts.

    Args:
        conn: An open duckdb.DuckDBPyConnection instance.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self._create_tables()

    def _create_tables(self) -> None:
        """Create orchestrator_instances table idempotently."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS orchestrator_instances (
                instance_id TEXT PRIMARY KEY,
                pid INTEGER,
                status TEXT NOT NULL,
                task_id TEXT,
                created_at TEXT NOT NULL,
                last_heartbeat TEXT,
                error_reason TEXT,
                project_id TEXT
            )
        """)

    def upsert(self, instance: ManagedInstance) -> None:
        """Insert or replace an instance record in the registry.

        Args:
            instance: ManagedInstance to persist.
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO orchestrator_instances "
            "(instance_id, pid, status, task_id, created_at, last_heartbeat, error_reason, project_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                instance.id,
                instance.pid,
                instance.status,
                instance.task_id,
                instance.created_at,
                instance.last_heartbeat,
                instance.error_reason,
                getattr(instance, "project_id", None),
            ],
        )

    def get_all_by_status(self, status: str) -> list[dict[str, Any]]:
        """Return all instances with the given status.

        Args:
            status: Status string to filter by (e.g., "running", "idle").

        Returns:
            List of dicts with instance fields.
        """
        rows = self._conn.execute(
            "SELECT instance_id, pid, status, task_id, created_at, last_heartbeat, error_reason, project_id "
            "FROM orchestrator_instances WHERE status = ?",
            [status],
        ).fetchall()
        return [
            {
                "instance_id": r[0],
                "pid": r[1],
                "status": r[2],
                "task_id": r[3],
                "created_at": r[4],
                "last_heartbeat": r[5],
                "error_reason": r[6],
                "project_id": r[7],
            }
            for r in rows
        ]

    def mark_crashed(self, instance_id: str, reason: str) -> None:
        """Mark an instance as crashed with an error reason.

        Args:
            instance_id: The instance to update.
            reason: Human-readable reason for the crash.
        """
        self._conn.execute(
            "UPDATE orchestrator_instances SET status = 'crashed', error_reason = ? "
            "WHERE instance_id = ?",
            [reason, instance_id],
        )


class ProcessManager:
    """Manages CC instance lifecycle: spawn, monitor, terminate.

    Uses DuckDB registry for persistence across orchestrator restarts.
    Supports orphan recovery on startup to handle prior crash scenarios.

    Args:
        conn: An open duckdb.DuckDBPyConnection for the registry.
        config: OrchestratorConfig with fleet settings.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, config: OrchestratorConfig) -> None:
        self._conn = conn
        self._config = config
        self._registry = ProcessRegistry(conn)
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._instances: dict[str, ManagedInstance] = {}
        # Mapper subprocess registry: keyed by project_root string.
        # Separate from CC worker processes — not counted against max_instances.
        self._mapper_processes: dict[str, asyncio.subprocess.Process] = {}
        self._log = structlog.get_logger(__name__)

    async def spawn_instance(
        self,
        cmd: list[str] | None = None,
        cwd: str | None = None,
        project_id: str | None = None,
    ) -> ManagedInstance:
        """Spawn a new CC instance subprocess.

        Checks max_instances limit before spawning. Builds child env with
        CLAUDECODE="" to prevent nested session conflicts.

        If project_id is provided and cwd is provided, injects LATTICE_PROJECT_ROOT
        set to cwd into the subprocess environment for per-project isolation.

        Args:
            cmd: Command to run. Defaults to ["claude", "--print"] for real CC.
                 Override for testing.
            cwd: Working directory for the subprocess.
            project_id: Optional project identifier. Stored on the ManagedInstance
                        and used to inject per-project env vars.

        Returns:
            ManagedInstance with pid set and status="idle".

        Raises:
            ValueError: If max_instances limit has been reached.
        """
        active = [
            i for i in self._instances.values()
            if i.status in ("idle", "running")
        ]
        if len(active) >= self._config.max_instances:
            raise ValueError(
                f"Max instances ({self._config.max_instances}) reached"
            )

        if cmd is None:
            cmd = ["claude", "--print"]

        # Inject per-project env vars when project_id is specified
        env = build_child_env(
            project_root=cwd if project_id is not None and cwd is not None else None,
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        instance = ManagedInstance(pid=proc.pid, status="idle", project_id=project_id)
        self._processes[instance.id] = proc
        self._instances[instance.id] = instance
        self._registry.upsert(instance)

        self._log.info(
            "instance_spawned",
            instance_id=instance.id,
            pid=proc.pid,
            project_id=project_id,
        )
        return instance

    async def spawn_mapper(
        self,
        project_root: str,
        cwd: str | None = None,
        cmd: list[str] | None = None,
    ) -> tuple[ManagedInstance, asyncio.subprocess.Process]:
        """Spawn a Mapper subprocess running the stdio NDJSON server.

        Uses the same asyncio.create_subprocess_exec pattern as spawn_instance
        but runs `sys.executable -m lattice.api.stdio` instead of claude --print.

        The Mapper subprocess is NOT counted against max_instances — it is
        infrastructure, not a CC worker.

        Stores the process in both self._processes (for terminate()) and
        self._mapper_processes (keyed by project_root for NDJSON routing).

        Args:
            project_root: Absolute path to the project root directory.
                          Passed to the subprocess as LATTICE_PROJECT_ROOT env var.
            cwd: Working directory for the subprocess. Defaults to project_root.
            cmd: Command override for testing. Defaults to
                 [sys.executable, "-m", "lattice.api.stdio"].

        Returns:
            Tuple of (ManagedInstance, raw asyncio.subprocess.Process).
            ManagedInstance is registered in DuckDB with status="running".
            Raw process is needed for direct NDJSON I/O via stdin/stdout.
        """
        import sys as _sys

        if cmd is None:
            cmd = [_sys.executable, "-m", "lattice.api.stdio"]

        env = build_child_env(override={
            "LATTICE_PROJECT_ROOT": project_root,
        })

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or project_root,
            env=env,
        )

        instance = ManagedInstance(pid=proc.pid, status="running")
        self._processes[instance.id] = proc
        self._instances[instance.id] = instance
        self._registry.upsert(instance)
        # Also register by project_root for NDJSON routing
        self._mapper_processes[project_root] = proc

        self._log.info(
            "mapper_spawned",
            instance_id=instance.id,
            pid=proc.pid,
            project_root=project_root,
        )
        return instance, proc

    def get_instance(self, instance_id: str) -> ManagedInstance | None:
        """Return the in-memory instance by ID.

        Args:
            instance_id: The instance UUID to look up.

        Returns:
            ManagedInstance if found, None otherwise.
        """
        return self._instances.get(instance_id)

    async def terminate(self, instance_id: str) -> str:
        """Terminate a managed instance.

        Args:
            instance_id: The instance UUID to terminate.

        Returns:
            "not_found" if instance unknown.
            "already_dead", "graceful", or "killed" from terminate_instance.
        """
        proc = self._processes.get(instance_id)
        instance = self._instances.get(instance_id)
        if proc is None or instance is None:
            return "not_found"

        result = await terminate_instance(proc)
        updated = instance.model_copy(update={"status": "stopped"})
        self._instances[instance_id] = updated
        self._registry.upsert(updated)
        del self._processes[instance_id]

        self._log.info(
            "instance_terminated",
            instance_id=instance_id,
            result=result,
        )
        return result

    def recover_orphans(self) -> list[str]:
        """Detect and handle orphaned instances from a prior crash.

        Queries DuckDB for instances in "running" or "assigned" status.
        For each:
        - If PID is alive: send SIGTERM, mark as crashed with "orphan_terminated_on_startup"
        - If PID is dead: mark as crashed with "orphan_detected_on_startup"

        Returns:
            List of recovered instance IDs.
        """
        recovered: list[str] = []

        for status in ("running", "assigned"):
            rows = self._registry.get_all_by_status(status)
            for row in rows:
                pid = row["pid"]
                iid = row["instance_id"]

                if pid is not None and is_process_alive(pid):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                    self._registry.mark_crashed(iid, "orphan_terminated_on_startup")
                    self._log.warning(
                        "orphan_terminated",
                        instance_id=iid,
                        pid=pid,
                    )
                else:
                    self._registry.mark_crashed(iid, "orphan_detected_on_startup")
                    self._log.warning(
                        "orphan_detected",
                        instance_id=iid,
                        pid=pid,
                    )

                recovered.append(iid)

        return recovered
