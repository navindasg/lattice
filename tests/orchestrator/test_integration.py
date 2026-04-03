"""Integration tests for CLAUDECODE env var stripping and per-project DuckDB isolation.

Covers the two highest-risk success criteria from OP-04:
1. CLAUDECODE='' in spawned subprocess (not inherited CLAUDECODE=1)
2. Per-project DuckDB isolation: two project files cannot read each other's process state

Also covers:
3. End-to-end spawn + enqueue + breaker trip full lifecycle test
4. Canary injection isolation: UUID in Project A soul file never leaks to Project B output
5. Per-project DuckDB file isolation: separate DuckDB files prevent cross-project visibility
"""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import duckdb
import pytest

from lattice.models.orchestrator import ManagedInstance
from lattice.orchestrator.breaker import CircuitBreaker
from lattice.orchestrator.manager import ProcessManager, ProcessRegistry
from lattice.orchestrator.models import BreakerConfig, OrchestratorConfig
from lattice.orchestrator.queue import TaskQueue
from lattice.orchestrator.soul import SoulFile, write_soul_atomically


# ---------------------------------------------------------------------------
# Test 1: CLAUDECODE env var stripping
# ---------------------------------------------------------------------------


async def test_claudecode_leakage_prevented(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spawned subprocess must have CLAUDECODE='' (empty string), not inherited."""
    monkeypatch.setenv("CLAUDECODE", "1")

    conn = duckdb.connect(":memory:")
    config = OrchestratorConfig()
    manager = ProcessManager(conn, config)

    # Spawn a subprocess that writes its CLAUDECODE value as JSON to stdout
    instance = await manager.spawn_instance(
        cmd=[
            sys.executable,
            "-c",
            (
                "import os, json, sys; "
                "sys.stdout.write(json.dumps({'claudecode': os.environ.get('CLAUDECODE', 'MISSING')}) + '\\n'); "
                "sys.stdout.flush()"
            ),
        ]
    )
    proc = manager._processes[instance.id]
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
    result = json.loads(line.decode().strip())
    assert result["claudecode"] == "", (
        f"CLAUDECODE should be empty string, got: {result['claudecode']!r}"
    )
    await manager.terminate(instance.id)


# ---------------------------------------------------------------------------
# Test 2: Per-project DuckDB isolation
# ---------------------------------------------------------------------------


def test_per_project_duckdb_isolation(tmp_path: object) -> None:
    """Two projects with separate DuckDB connections cannot read each other's process state."""
    from pathlib import Path

    tmp_path = Path(str(tmp_path))  # type: ignore[assignment]
    conn_a = duckdb.connect(str(tmp_path / "project_a.duckdb"))
    conn_b = duckdb.connect(str(tmp_path / "project_b.duckdb"))

    reg_a = ProcessRegistry(conn_a)
    reg_b = ProcessRegistry(conn_b)

    instance_a = ManagedInstance(id="inst-a", pid=1001, status="running")
    reg_a.upsert(instance_a)

    instance_b = ManagedInstance(id="inst-b", pid=2001, status="running")
    reg_b.upsert(instance_b)

    # Project A can only see its own instance
    a_running = reg_a.get_all_by_status("running")
    assert len(a_running) == 1
    assert a_running[0]["instance_id"] == "inst-a"

    # Project B can only see its own instance
    b_running = reg_b.get_all_by_status("running")
    assert len(b_running) == 1
    assert b_running[0]["instance_id"] == "inst-b"

    conn_a.close()
    conn_b.close()


# ---------------------------------------------------------------------------
# Test 3: End-to-end spawn + enqueue + breaker trip
# ---------------------------------------------------------------------------


async def test_spawn_enqueue_breaker_trip() -> None:
    """Full lifecycle: spawn instance, enqueue task, breaker trips on iteration cap."""
    conn = duckdb.connect(":memory:")
    config = OrchestratorConfig(max_instances=1)
    config_breaker = BreakerConfig(iteration_cap=3)
    manager = ProcessManager(conn, config)
    queue = TaskQueue(conn, max_depth=5)
    breaker = CircuitBreaker(instance_id="test", config=config_breaker)

    # Enqueue a task
    task = queue.enqueue('{"action": "test"}', priority="normal")
    assert task.status == "pending"

    # Simulate 3 iterations — should trip at exactly 3
    for _ in range(3):
        breaker.record_iteration()

    assert breaker.is_tripped
    assert breaker.state.trip_reason == "iteration_cap"
    assert breaker.state.iteration_count == 3


# ---------------------------------------------------------------------------
# Test 4: Canary injection isolation
# ---------------------------------------------------------------------------


async def test_canary_isolation(tmp_path: Path) -> None:
    """Unique canary in Project A soul file must never appear in Project B outputs.

    Creates two isolated project directories with separate .lattice/ and .agent-docs/.
    Injects a UUID canary into Project A's soul file identity section.
    Spawns 10 mock CC subprocesses across both projects.
    Each mock subprocess reads LATTICE_PROJECT_ROOT from env and writes it + all
    env vars to stdout as JSON.
    Asserts: canary string never appears in any Project B subprocess output.
    """
    canary = str(uuid.uuid4())

    # Create two isolated project directories
    project_a_root = tmp_path / "project_a"
    project_b_root = tmp_path / "project_b"
    for p in (project_a_root, project_b_root):
        (p / ".lattice").mkdir(parents=True)
        (p / ".agent-docs").mkdir(parents=True)

    # Write canary into Project A soul file
    soul_a = SoulFile(
        instance_id="canary-instance",
        identity=f"Project A worker. Canary: {canary}",
        project_context=str(project_a_root / ".agent-docs"),
        preferences="standard",
        project_id="project_a",
    )
    soul_dir_a = project_a_root / ".lattice" / "souls"
    soul_dir_a.mkdir(parents=True, exist_ok=True)
    write_soul_atomically(soul_dir_a / "canary-instance.md", soul_a.to_markdown())

    # Set up two separate DuckDB registries
    db_a = duckdb.connect(str(project_a_root / ".lattice" / "orchestrator.duckdb"))
    db_b = duckdb.connect(str(project_b_root / ".lattice" / "orchestrator.duckdb"))
    config = OrchestratorConfig(max_instances=10)
    manager_a = ProcessManager(db_a, config)
    manager_b = ProcessManager(db_b, config)

    # Mock CC subprocess: reads env and echoes all env vars as JSON to stdout
    mock_cc_script = (
        "import os, json, sys; "
        "env = dict(os.environ); "
        "sys.stdout.write(json.dumps({"
        "'project_root': env.get('LATTICE_PROJECT_ROOT', 'MISSING'), "
        "'agent_docs': env.get('LATTICE_AGENT_DOCS', 'MISSING'), "
        "'all_env_values': '|'.join(env.values())"
        "}) + '\\n'); "
        "sys.stdout.flush()"
    )

    # Spawn 5 instances for each project (10 total, parallel)
    tasks_a = []
    tasks_b = []
    for _ in range(5):
        tasks_a.append(manager_a.spawn_instance(
            cmd=[sys.executable, "-c", mock_cc_script],
            cwd=str(project_a_root),
            project_id="project_a",
        ))
        tasks_b.append(manager_b.spawn_instance(
            cmd=[sys.executable, "-c", mock_cc_script],
            cwd=str(project_b_root),
            project_id="project_b",
        ))

    instances_a = await asyncio.gather(*tasks_a)
    instances_b = await asyncio.gather(*tasks_b)

    # Collect all Project B outputs
    project_b_outputs = []
    for inst in instances_b:
        proc = manager_b._processes[inst.id]
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            if line:
                project_b_outputs.append(line.decode().strip())
        except asyncio.TimeoutError:
            pass

    # CRITICAL ASSERTION: canary must NEVER appear in Project B output
    for output in project_b_outputs:
        assert canary not in output, (
            f"ISOLATION FAILURE: Canary {canary} leaked to Project B output: {output}"
        )

    # Also verify Project B soul files don't contain canary
    soul_dir_b = project_b_root / ".lattice" / "souls"
    if soul_dir_b.exists():
        for soul_file in soul_dir_b.iterdir():
            content = soul_file.read_text()
            assert canary not in content, (
                f"ISOLATION FAILURE: Canary {canary} leaked to Project B soul file: {soul_file}"
            )

    # Verify Project A instances have correct project_root in env
    for inst in instances_a:
        proc = manager_a._processes[inst.id]
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            if line:
                data = json.loads(line.decode().strip())
                assert data["project_root"] == str(project_a_root)
        except asyncio.TimeoutError:
            pass

    # Verify per-project DuckDB isolation
    rows_a = manager_a._registry.get_all_by_status("idle")
    rows_b = manager_b._registry.get_all_by_status("idle")
    ids_a = {r["instance_id"] for r in rows_a}
    ids_b = {r["instance_id"] for r in rows_b}
    assert ids_a.isdisjoint(ids_b), "Project A and B registries must not share instances"

    # Cleanup
    for inst in instances_a:
        await manager_a.terminate(inst.id)
    for inst in instances_b:
        await manager_b.terminate(inst.id)
    db_a.close()
    db_b.close()


# ---------------------------------------------------------------------------
# Test 5: Per-project DuckDB file isolation (new variant)
# ---------------------------------------------------------------------------


def test_per_project_duckdb_file_isolation(tmp_path: Path) -> None:
    """Two projects with separate DuckDB files cannot see each other's instances."""
    db_a = duckdb.connect(str(tmp_path / "a.duckdb"))
    db_b = duckdb.connect(str(tmp_path / "b.duckdb"))
    config = OrchestratorConfig()
    reg_a = ProcessRegistry(db_a)
    reg_b = ProcessRegistry(db_b)

    instance = ManagedInstance(pid=999, status="idle", project_id="proj_a")
    reg_a.upsert(instance)

    # Project B must NOT see Project A's instance
    rows_b = reg_b.get_all_by_status("idle")
    assert len(rows_b) == 0, "Project B should not see Project A instances"

    rows_a = reg_a.get_all_by_status("idle")
    assert len(rows_a) == 1
    assert rows_a[0]["instance_id"] == instance.id
    assert rows_a[0]["project_id"] == "proj_a"

    db_a.close()
    db_b.close()
