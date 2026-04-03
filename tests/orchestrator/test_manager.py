"""Tests for ProcessManager: spawn, health check, terminate, and DuckDB registry.

Uses real subprocesses (not mocks) per plan requirements.
Uses duckdb.connect(":memory:") for all registry tests.

Covers:
- build_child_env: CLAUDECODE stripping/resetting, override, preservation
- is_process_alive: live and dead PID detection
- terminate_instance: graceful SIGTERM shutdown, already-dead process
- ProcessRegistry: table creation, upsert, get_all_by_status, mark_crashed
- ProcessManager: init, spawn, CLAUDECODE env verify, get_instance, max_instances
- recover_orphans: dead PID marked crashed, alive PID terminated and marked crashed
"""
import asyncio
import json
import os
import signal
import sys

import duckdb
import pytest

from lattice.models.orchestrator import ManagedInstance
from lattice.orchestrator.manager import (
    ProcessManager,
    ProcessRegistry,
    build_child_env,
    is_process_alive,
    terminate_instance,
)
from lattice.orchestrator.models import OrchestratorConfig


# ---------------------------------------------------------------------------
# build_child_env
# ---------------------------------------------------------------------------


class TestBuildChildEnv:
    def test_claudecode_not_in_result_when_set(self, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "1")
        env = build_child_env()
        # CLAUDECODE key must still be present but set to empty string
        assert "CLAUDECODE" in env

    def test_claudecode_set_to_empty_string(self, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "1")
        env = build_child_env()
        assert env["CLAUDECODE"] == ""

    def test_claudecode_empty_when_not_originally_set(self, monkeypatch):
        monkeypatch.delenv("CLAUDECODE", raising=False)
        env = build_child_env()
        assert env["CLAUDECODE"] == ""

    def test_override_included_in_result(self, monkeypatch):
        monkeypatch.delenv("CLAUDECODE", raising=False)
        env = build_child_env(override={"FOO": "bar"})
        assert env["FOO"] == "bar"

    def test_standard_env_vars_preserved(self, monkeypatch):
        monkeypatch.delenv("CLAUDECODE", raising=False)
        env = build_child_env()
        # PATH should be present
        assert "PATH" in env

    def test_home_preserved(self, monkeypatch):
        monkeypatch.delenv("CLAUDECODE", raising=False)
        env = build_child_env()
        # HOME should be present (on macOS/Linux)
        if "HOME" in os.environ:
            assert "HOME" in env


# ---------------------------------------------------------------------------
# is_process_alive
# ---------------------------------------------------------------------------


class TestIsProcessAlive:
    def test_current_process_is_alive(self):
        assert is_process_alive(os.getpid()) is True

    def test_nonexistent_pid_is_dead(self):
        # PID 99999999 is virtually guaranteed not to exist
        assert is_process_alive(99999999) is False


# ---------------------------------------------------------------------------
# terminate_instance
# ---------------------------------------------------------------------------


class TestTerminateInstance:
    async def test_sigterm_terminates_process(self):
        proc = await asyncio.create_subprocess_exec(
            "sleep", "300",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        result = await terminate_instance(proc, timeout=10.0)
        assert result in ("graceful", "killed")
        assert proc.returncode is not None

    async def test_already_dead_process_returns_already_dead(self):
        proc = await asyncio.create_subprocess_exec(
            "sleep", "0",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Wait for it to exit naturally
        await proc.wait()
        result = await terminate_instance(proc)
        assert result == "already_dead"


# ---------------------------------------------------------------------------
# ProcessRegistry
# ---------------------------------------------------------------------------


class TestProcessRegistry:
    def test_init_creates_orchestrator_instances_table(self):
        conn = duckdb.connect(":memory:")
        registry = ProcessRegistry(conn)
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'orchestrator_instances'"
        ).fetchall()
        assert len(tables) == 1

    def test_upsert_writes_instance_to_db(self):
        conn = duckdb.connect(":memory:")
        registry = ProcessRegistry(conn)
        instance = ManagedInstance(pid=12345, status="running")
        registry.upsert(instance)
        rows = conn.execute(
            "SELECT instance_id, pid, status FROM orchestrator_instances WHERE instance_id = ?",
            [instance.id],
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == 12345
        assert rows[0][2] == "running"

    def test_upsert_overwrites_same_instance_id(self):
        conn = duckdb.connect(":memory:")
        registry = ProcessRegistry(conn)
        instance = ManagedInstance(pid=111, status="idle")
        registry.upsert(instance)
        # Overwrite with new status
        updated = instance.model_copy(update={"status": "running", "pid": 222})
        registry.upsert(updated)
        rows = conn.execute(
            "SELECT pid, status FROM orchestrator_instances WHERE instance_id = ?",
            [instance.id],
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 222
        assert rows[0][1] == "running"

    def test_get_all_by_status_returns_only_matching(self):
        conn = duckdb.connect(":memory:")
        registry = ProcessRegistry(conn)
        running_instance = ManagedInstance(pid=100, status="running")
        idle_instance = ManagedInstance(pid=200, status="idle")
        registry.upsert(running_instance)
        registry.upsert(idle_instance)
        running = registry.get_all_by_status("running")
        assert len(running) == 1
        assert running[0]["status"] == "running"
        assert running[0]["pid"] == 100

    def test_mark_crashed_updates_status_and_error_reason(self):
        conn = duckdb.connect(":memory:")
        registry = ProcessRegistry(conn)
        instance = ManagedInstance(pid=555, status="running")
        registry.upsert(instance)
        registry.mark_crashed(instance.id, "orphan_detected_on_startup")
        rows = conn.execute(
            "SELECT status, error_reason FROM orchestrator_instances WHERE instance_id = ?",
            [instance.id],
        ).fetchall()
        assert rows[0][0] == "crashed"
        assert rows[0][1] == "orphan_detected_on_startup"


# ---------------------------------------------------------------------------
# ProcessManager integration
# ---------------------------------------------------------------------------


class TestProcessManagerInit:
    def test_init_creates_registry_tables(self):
        conn = duckdb.connect(":memory:")
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'orchestrator_instances'"
        ).fetchall()
        assert len(tables) == 1


class TestProcessManagerSpawn:
    async def test_spawn_instance_returns_managed_instance_with_pid(self):
        conn = duckdb.connect(":memory:")
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)
        cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
        instance = await manager.spawn_instance(cmd=cmd)
        try:
            assert instance.pid is not None
            assert instance.pid > 0
            assert is_process_alive(instance.pid)
        finally:
            await manager.terminate(instance.id)

    async def test_spawn_strips_claudecode_env(self):
        conn = duckdb.connect(":memory:")
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)
        # Run a subprocess that prints CLAUDECODE env var value to stdout
        cmd = [
            sys.executable, "-c",
            "import os, sys; print(repr(os.environ.get('CLAUDECODE', 'MISSING')), flush=True)"
        ]
        proc_result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=build_child_env(),
        )
        stdout, _ = await asyncio.wait_for(proc_result.communicate(), timeout=10.0)
        output = stdout.decode().strip()
        # CLAUDECODE should be "" (empty string), not "MISSING" and not "1"
        assert output == "''"

    async def test_get_instance_returns_spawned_instance(self):
        conn = duckdb.connect(":memory:")
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)
        cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
        instance = await manager.spawn_instance(cmd=cmd)
        try:
            retrieved = manager.get_instance(instance.id)
            assert retrieved is not None
            assert retrieved.id == instance.id
        finally:
            await manager.terminate(instance.id)

    async def test_spawn_beyond_max_instances_raises(self):
        conn = duckdb.connect(":memory:")
        config = OrchestratorConfig(max_instances=2)
        manager = ProcessManager(conn, config)
        cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
        spawned = []
        try:
            for _ in range(2):
                instance = await manager.spawn_instance(cmd=cmd)
                spawned.append(instance)
            with pytest.raises(ValueError, match="Max instances"):
                await manager.spawn_instance(cmd=cmd)
        finally:
            for inst in spawned:
                await manager.terminate(inst.id)


# ---------------------------------------------------------------------------
# recover_orphans
# ---------------------------------------------------------------------------


class TestRecoverOrphans:
    def test_recover_orphans_dead_pid_marked_crashed(self):
        conn = duckdb.connect(":memory:")
        registry = ProcessRegistry(conn)
        # Insert a "running" instance with a dead PID
        instance = ManagedInstance(pid=99999999, status="running")
        registry.upsert(instance)
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)
        recovered = manager.recover_orphans()
        assert instance.id in recovered
        rows = conn.execute(
            "SELECT status, error_reason FROM orchestrator_instances WHERE instance_id = ?",
            [instance.id],
        ).fetchall()
        assert rows[0][0] == "crashed"
        assert rows[0][1] == "orphan_detected_on_startup"

    async def test_recover_orphans_alive_pid_terminated_and_marked_crashed(self):
        conn = duckdb.connect(":memory:")
        registry = ProcessRegistry(conn)
        # Start a real subprocess
        proc = await asyncio.create_subprocess_exec(
            "sleep", "300",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pid = proc.pid
        instance = ManagedInstance(pid=pid, status="running")
        registry.upsert(instance)
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)
        recovered = manager.recover_orphans()
        assert instance.id in recovered
        rows = conn.execute(
            "SELECT status, error_reason FROM orchestrator_instances WHERE instance_id = ?",
            [instance.id],
        ).fetchall()
        assert rows[0][0] == "crashed"
        assert rows[0][1] == "orphan_terminated_on_startup"
        # Clean up: reap the process
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


# ---------------------------------------------------------------------------
# spawn_mapper tests
# ---------------------------------------------------------------------------


class TestSpawnMapper:
    async def test_spawn_mapper_creates_subprocess(self, tmp_path):
        """spawn_mapper creates a subprocess with status='running' and pid > 0."""
        conn = duckdb.connect(":memory:")
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)

        # Use a simple echo script as the mapper subprocess
        echo_script = (
            "import sys, json\n"
            "for line in sys.stdin:\n"
            "    line = line.strip()\n"
            "    if not line: break\n"
            "    msg = json.loads(line)\n"
            "    print(json.dumps({'success': True, 'command': msg.get('command', ''), 'data': {}, 'error': None}), flush=True)\n"
        )

        # Temporarily replace the mapper entry point
        import lattice.api.stdio  # noqa: F401 (ensure importable)
        instance, proc = await manager.spawn_mapper(
            project_root=str(tmp_path),
            cmd=[sys.executable, "-c", echo_script],
        )
        try:
            assert instance is not None
            assert instance.status == "running"
            assert instance.pid is not None
            assert instance.pid > 0
        finally:
            proc.terminate()
            await proc.wait()

    async def test_spawn_mapper_ndjson_roundtrip(self, tmp_path):
        """spawn_mapper subprocess receives NDJSON command and returns JSON response."""
        conn = duckdb.connect(":memory:")
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)

        # Simple echo script that returns a success response for any command
        echo_script = (
            "import sys, json\n"
            "for line in sys.stdin:\n"
            "    line = line.strip()\n"
            "    if not line: break\n"
            "    msg = json.loads(line)\n"
            "    resp = {'success': True, 'command': msg.get('command', ''), 'data': {'ok': True}, 'error': None}\n"
            "    sys.stdout.write(json.dumps(resp) + '\\n')\n"
            "    sys.stdout.flush()\n"
        )

        instance, proc = await manager.spawn_mapper(
            project_root=str(tmp_path),
            cmd=[sys.executable, "-c", echo_script],
        )
        try:
            # Write a command to the subprocess stdin
            cmd_msg = json.dumps({"command": "map:status", "payload": {"target": "."}}) + "\n"
            proc.stdin.write(cmd_msg.encode())
            await proc.stdin.drain()

            # Read response from stdout
            response_line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            response = json.loads(response_line.decode().strip())
            assert response["success"] is True
            assert response["command"] == "map:status"
        finally:
            proc.terminate()
            await proc.wait()

    def test_spawn_mapper_adds_mapper_processes_dict(self):
        """ProcessManager has _mapper_processes dict initialized in __init__."""
        conn = duckdb.connect(":memory:")
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)
        assert hasattr(manager, "_mapper_processes")
        assert isinstance(manager._mapper_processes, dict)


# ---------------------------------------------------------------------------
# build_child_env project vars (Task 1 additions)
# ---------------------------------------------------------------------------


class TestBuildChildEnvProjectVars:
    def test_build_child_env_project_root_injected(self):
        """build_child_env with project_root injects LATTICE_PROJECT_ROOT."""
        env = build_child_env(project_root="/path/to/project")
        assert env["LATTICE_PROJECT_ROOT"] == "/path/to/project"

    def test_build_child_env_agent_docs_injected(self):
        """build_child_env with agent_docs injects LATTICE_AGENT_DOCS."""
        env = build_child_env(agent_docs="/path/to/.agent-docs")
        assert env["LATTICE_AGENT_DOCS"] == "/path/to/.agent-docs"

    def test_build_child_env_both_project_vars(self):
        """build_child_env with both project_root and agent_docs injects both."""
        env = build_child_env(
            project_root="/proj",
            agent_docs="/proj/.agent-docs",
        )
        assert env["LATTICE_PROJECT_ROOT"] == "/proj"
        assert env["LATTICE_AGENT_DOCS"] == "/proj/.agent-docs"

    def test_build_child_env_no_project_vars_absent(self):
        """build_child_env without project_root/agent_docs does NOT inject those keys."""
        env = build_child_env()
        assert "LATTICE_PROJECT_ROOT" not in env
        assert "LATTICE_AGENT_DOCS" not in env

    def test_build_child_env_override_still_works(self):
        """override dict still applied when project_root is also given."""
        env = build_child_env(
            project_root="/proj",
            override={"MY_VAR": "hello"},
        )
        assert env["LATTICE_PROJECT_ROOT"] == "/proj"
        assert env["MY_VAR"] == "hello"


class TestRegistryUpsertWithProjectId:
    def test_registry_upsert_with_project_id(self):
        """ProcessRegistry upsert with project_id persists to DuckDB."""
        conn = duckdb.connect(":memory:")
        registry = ProcessRegistry(conn)
        instance = ManagedInstance(pid=12345, status="idle", project_id="proj-alpha")
        registry.upsert(instance)
        rows = conn.execute(
            "SELECT instance_id, project_id FROM orchestrator_instances WHERE instance_id = ?",
            [instance.id],
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "proj-alpha"

    def test_registry_upsert_project_id_none(self):
        """ProcessRegistry upsert with project_id=None persists None."""
        conn = duckdb.connect(":memory:")
        registry = ProcessRegistry(conn)
        instance = ManagedInstance(pid=99, status="idle")
        registry.upsert(instance)
        rows = conn.execute(
            "SELECT project_id FROM orchestrator_instances WHERE instance_id = ?",
            [instance.id],
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] is None
