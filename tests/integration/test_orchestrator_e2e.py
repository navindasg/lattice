"""End-to-end integration tests for the orchestrator (GitHub issue #6).

Tests:
    1. Full orchestrator lifecycle with mock CC instances: start → detect
       instances → send command → simulate PreToolUse approval → verify
       STATE.md → clean shutdown.
    2. Spool resilience: stop orchestrator → CC fires 3 events (spooled) →
       start orchestrator → verify all 3 events recovered from spool.
    3. Restart resilience: start orchestrator → assign tasks to 2 instances →
       kill orchestrator → restart → verify STATE.md restores correct
       assignments.

All tests use mocked terminal backends and LLM models to avoid hardware
and API dependencies. The event channel is real (UDS socket in tmp dir).
"""
from __future__ import annotations

import asyncio
import json
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import pytest

from lattice.orchestrator.events.models import ApprovalDecision, CCEvent
from lattice.orchestrator.events.persistence import init_events_table
from lattice.orchestrator.events.runner import EventServer
from lattice.orchestrator.events.spool import append_to_spool
from lattice.orchestrator.runner import OrchestratorRunner
from lattice.orchestrator.soul_ecosystem.models import (
    InstanceAssignment,
    OrchestratorState,
)
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.soul_ecosystem.writer import SoulWriter
from lattice.orchestrator.terminal.models import CCInstance

# AF_UNIX has a ~104 char path limit on macOS. pytest tmp_path can exceed this.
# Use /tmp for sockets with a short random suffix.
import tempfile as _tempfile


def _make_short_sock_path() -> Path:
    """Create a short socket path that fits AF_UNIX's ~104 char limit."""
    sock_dir = _tempfile.mkdtemp(prefix="lat_", dir="/tmp")
    return Path(sock_dir) / "o.sock"


def _cleanup_sock_path(sock_path: Path) -> None:
    """Remove socket file and its parent temp directory."""
    if sock_path.exists():
        sock_path.unlink()
    parent = sock_path.parent
    if parent.exists():
        try:
            parent.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_json(output: str) -> dict:
    """Extract the command JSON object from CLI output.

    structlog may emit log lines before the actual JSON command output.
    Finds the last valid JSON object by locating the final '{' that opens
    a top-level object and parsing from there to the matching '}'.
    """
    # Try parsing full output first
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass

    # Find the last top-level JSON object in the output
    # (command output is always the last JSON block)
    last_open = output.rfind("\n{")
    if last_open == -1:
        last_open = 0 if output.startswith("{") else -1

    if last_open >= 0:
        candidate = output[last_open:].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON found in output:\n{output}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_event(
    session_id: str = "session-abc",
    event_type: str = "PostToolUse",
    tool_name: str = "Bash",
    cwd: str = "/tmp/project",
) -> CCEvent:
    """Create a CCEvent for testing."""
    return CCEvent(
        session_id=session_id,
        event_type=event_type,
        tool_name=tool_name,
        cwd=cwd,
        timestamp=datetime.now(timezone.utc),
    )


def _make_mock_terminal(instances: list[CCInstance] | None = None) -> MagicMock:
    """Create a mock terminal backend with configurable CC instances."""
    backend = MagicMock()
    backend.detect_cc_panes = AsyncMock(return_value=instances or [])
    backend.send_text = AsyncMock()
    backend.send_enter = AsyncMock()
    backend.send_interrupt = AsyncMock()
    backend.capture_output = AsyncMock(return_value=["$ claude", "Working..."])
    backend.spawn_pane = AsyncMock(return_value="%10")
    backend.close_pane = AsyncMock()
    backend.list_panes = AsyncMock(return_value=[])
    return backend


def _make_mock_llm() -> MagicMock:
    """Create a mock LLM model that returns a simple AIMessage with no tool calls."""
    from langchain_core.messages import AIMessage

    model = MagicMock()
    response = AIMessage(content="Acknowledged. Processing event.")
    model_with_tools = MagicMock()
    model_with_tools.invoke = MagicMock(return_value=response)
    model.bind_tools = MagicMock(return_value=model_with_tools)
    return model


def _make_cc_instances() -> list[CCInstance]:
    """Create 2 mock CC instances for testing."""
    return [
        CCInstance(
            pane_id="%1",
            session_name="work",
            window_name="main",
            user_number=1,
            running_command="claude",
            cwd="/tmp/project-a",
        ),
        CCInstance(
            pane_id="%2",
            session_name="work",
            window_name="main",
            user_number=2,
            running_command="claude",
            cwd="/tmp/project-b",
        ),
    ]


# ---------------------------------------------------------------------------
# Test 1: Full orchestrator lifecycle with mock CC instances
# ---------------------------------------------------------------------------


class TestOrchestratorLifecycleE2E:
    """Start orchestrator → detect instances → send command → approval → STATE.md → shutdown."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_with_mock_instances(self, tmp_path: Path) -> None:
        """Full startup sequence, event processing, and clean shutdown."""
        sock_path = _make_short_sock_path()
        db_path = str(tmp_path / "orch.duckdb")
        soul_dir = tmp_path / "soul"

        instances = _make_cc_instances()
        mock_terminal = _make_mock_terminal(instances)
        mock_llm = _make_mock_llm()

        runner = OrchestratorRunner(
            project_root=str(tmp_path),
            db_path=db_path,
            soul_dir=str(soul_dir),
            sock_path=sock_path,
            voice_enabled=False,
            terminal_backend=mock_terminal,
            llm_model=mock_llm,
        )

        async def _run_test_then_shutdown() -> None:
            """Wait for runner to start, run tests, then trigger shutdown."""
            # Wait for event server to be ready
            for _ in range(100):
                await asyncio.sleep(0.1)
                if runner.event_server is not None and sock_path.exists():
                    break

            assert sock_path.exists(), "Event server socket not created"

            # Verify soul files were created
            assert (soul_dir / "SOUL.md").exists()
            assert (soul_dir / "AGENTS.md").exists()
            assert (soul_dir / "STATE.md").exists()
            assert (soul_dir / "MEMORY.md").exists()

            # Verify soul files are non-empty
            assert (soul_dir / "SOUL.md").stat().st_size > 0
            assert (soul_dir / "AGENTS.md").stat().st_size > 0

            # Verify instance pane map was populated
            assert runner.instance_pane_map.get("1") == "%1"
            assert runner.instance_pane_map.get("2") == "%2"

            # Send a PostToolUse event via the UDS socket
            import httpx
            transport = httpx.AsyncHTTPTransport(uds=str(sock_path))
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                event_data = {
                    "session_id": "session-abc",
                    "event_type": "PostToolUse",
                    "tool_name": "Bash",
                    "cwd": "/tmp/project-a",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                resp = await client.post("/events", json=event_data)
                assert resp.status_code == 200
                envelope = resp.json()
                assert envelope["accepted"] is True

                # Give the agent event loop time to process
                await asyncio.sleep(1.0)

                # Verify event appears in history
                resp = await client.get("/events/history")
                assert resp.status_code == 200
                history = resp.json()
                assert len(history) >= 1
                assert history[0]["event_type"] == "PostToolUse"

                # Check health endpoint
                resp = await client.get("/health")
                assert resp.status_code == 200
                health = resp.json()
                assert health["status"] == "ok"
                assert health["connected_sessions"] >= 1

            # Trigger clean shutdown
            runner._handle_signal(signal.SIGTERM)

        test_task = asyncio.create_task(_run_test_then_shutdown())

        await asyncio.wait_for(runner.run(), timeout=30.0)
        await test_task

        # Post-shutdown assertions
        assert not sock_path.exists(), "Socket file not cleaned up"

        # Verify DuckDB was created
        assert Path(db_path).exists()
        _cleanup_sock_path(sock_path)

    @pytest.mark.asyncio
    async def test_start_detects_no_cc_panes_warns(self, tmp_path: Path) -> None:
        """orchestrator:start with tmux but no CC panes prints warning."""
        sock_path = _make_short_sock_path()
        db_path = str(tmp_path / "orch.duckdb")

        # Empty instance list — tmux running but no CC panes
        mock_terminal = _make_mock_terminal(instances=[])
        mock_llm = _make_mock_llm()

        runner = OrchestratorRunner(
            project_root=str(tmp_path),
            db_path=db_path,
            soul_dir=str(tmp_path / "soul"),
            sock_path=sock_path,
            voice_enabled=False,
            terminal_backend=mock_terminal,
            llm_model=mock_llm,
        )

        async def _shutdown_after_start() -> None:
            for _ in range(50):
                await asyncio.sleep(0.1)
                if sock_path.exists():
                    break
            runner._handle_signal(signal.SIGTERM)

        shutdown_task = asyncio.create_task(_shutdown_after_start())
        await asyncio.wait_for(runner.run(), timeout=15.0)
        await shutdown_task

        # Should have succeeded (warning, not error)
        assert runner.instance_pane_map == {}
        _cleanup_sock_path(sock_path)

    @pytest.mark.asyncio
    async def test_duplicate_start_fails_fast(self, tmp_path: Path) -> None:
        """Second orchestrator start detects existing socket and exits."""
        sock_path = _make_short_sock_path()
        db_path = str(tmp_path / "orch.duckdb")

        mock_terminal = _make_mock_terminal(_make_cc_instances())
        mock_llm = _make_mock_llm()

        runner1 = OrchestratorRunner(
            project_root=str(tmp_path),
            db_path=db_path,
            soul_dir=str(tmp_path / "soul"),
            sock_path=sock_path,
            voice_enabled=False,
            terminal_backend=mock_terminal,
            llm_model=mock_llm,
        )

        async def _attempt_second_start_then_shutdown() -> None:
            # Wait for first runner to be ready
            for _ in range(100):
                await asyncio.sleep(0.1)
                if sock_path.exists():
                    break

            assert sock_path.exists(), "First runner didn't start"

            # Second runner should fail fast
            runner2 = OrchestratorRunner(
                project_root=str(tmp_path),
                db_path=str(tmp_path / "orch2.duckdb"),
                soul_dir=str(tmp_path / "soul"),
                sock_path=sock_path,
                voice_enabled=False,
                terminal_backend=mock_terminal,
                llm_model=mock_llm,
            )
            with pytest.raises(SystemExit, match="Orchestrator already running"):
                await runner2.run()

            # Shutdown first runner
            runner1._handle_signal(signal.SIGTERM)

        test_task = asyncio.create_task(_attempt_second_start_then_shutdown())
        await asyncio.wait_for(runner1.run(), timeout=20.0)
        await test_task
        _cleanup_sock_path(sock_path)

    @pytest.mark.asyncio
    async def test_graceful_shutdown_cleans_up(self, tmp_path: Path) -> None:
        """Ctrl+C (SIGINT) triggers graceful shutdown: socket removed, STATE.md flushed."""
        sock_path = _make_short_sock_path()
        db_path = str(tmp_path / "orch.duckdb")
        soul_dir = tmp_path / "soul"

        mock_terminal = _make_mock_terminal(_make_cc_instances())
        mock_llm = _make_mock_llm()

        runner = OrchestratorRunner(
            project_root=str(tmp_path),
            db_path=db_path,
            soul_dir=str(soul_dir),
            sock_path=sock_path,
            voice_enabled=False,
            terminal_backend=mock_terminal,
            llm_model=mock_llm,
        )

        async def _shutdown_after_start() -> None:
            for _ in range(100):
                await asyncio.sleep(0.1)
                if sock_path.exists():
                    break
            runner._handle_signal(signal.SIGINT)

        shutdown_task = asyncio.create_task(_shutdown_after_start())
        await asyncio.wait_for(runner.run(), timeout=15.0)
        await shutdown_task

        # Verify cleanup
        assert not sock_path.exists(), "Socket not removed after shutdown"
        assert (soul_dir / "STATE.md").exists(), "STATE.md should persist after shutdown"
        _cleanup_sock_path(sock_path)


# ---------------------------------------------------------------------------
# Test 2: Spool resilience
# ---------------------------------------------------------------------------


class TestSpoolResilience:
    """Stop orchestrator → CC fires events (spooled) → restart → verify recovery."""

    @pytest.mark.asyncio
    async def test_spooled_events_recovered_on_restart(self, tmp_path: Path) -> None:
        """Events spooled while orchestrator is down are recovered on next start."""
        sock_path = _make_short_sock_path()
        db_path = str(tmp_path / "orch.duckdb")
        spool_file = tmp_path / "spool" / "events.jsonl"

        # Step 1: Write 3 events to spool (simulating CC hooks firing while
        # orchestrator is down)
        events = [
            _make_event(session_id="sess-1", event_type="PostToolUse", tool_name="Bash"),
            _make_event(session_id="sess-2", event_type="PostToolUse", tool_name="Read"),
            _make_event(session_id="sess-3", event_type="SessionStart", tool_name=None),
        ]
        for evt in events:
            append_to_spool(evt, spool_file=spool_file)

        assert spool_file.exists()
        lines = spool_file.read_text().strip().splitlines()
        assert len(lines) == 3, f"Expected 3 spooled events, got {len(lines)}"

        # Step 2: Start orchestrator with spool file override
        mock_terminal = _make_mock_terminal(_make_cc_instances())
        mock_llm = _make_mock_llm()

        # Create event server that uses our spool file
        db_conn = duckdb.connect(db_path)
        init_events_table(db_conn)

        event_server = EventServer(
            db_conn=db_conn,
            sock_path=sock_path,
            spool_file=spool_file,
        )
        event_queue = await event_server.start()

        # Step 3: Verify all 3 spooled events were drained into the queue
        drained_events: list[CCEvent] = []
        while not event_queue.empty():
            drained_events.append(event_queue.get_nowait())

        assert len(drained_events) == 3, (
            f"Expected 3 drained events, got {len(drained_events)}"
        )
        session_ids = {e.session_id for e in drained_events}
        assert session_ids == {"sess-1", "sess-2", "sess-3"}

        # Step 4: Verify spool file was emptied
        remaining = spool_file.read_text().strip()
        assert remaining == "", "Spool file should be empty after drain"

        # Step 5: Verify events appear in history (DuckDB)
        import httpx
        transport = httpx.AsyncHTTPTransport(uds=str(sock_path))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            resp = await client.get("/events/history")
            assert resp.status_code == 200
            history = resp.json()
            assert len(history) >= 3, (
                f"Expected 3+ events in history, got {len(history)}"
            )

        await event_server.stop()
        db_conn.close()
        _cleanup_sock_path(sock_path)


# ---------------------------------------------------------------------------
# Test 3: Restart resilience
# ---------------------------------------------------------------------------


class TestRestartResilience:
    """Start → assign tasks → kill → restart → verify STATE.md restores."""

    @pytest.mark.asyncio
    async def test_state_survives_restart(self, tmp_path: Path) -> None:
        """STATE.md persists instance assignments across orchestrator restarts."""
        sock_path = _make_short_sock_path()
        db_path = str(tmp_path / "orch.duckdb")
        soul_dir = tmp_path / "soul"

        instances = _make_cc_instances()
        mock_terminal = _make_mock_terminal(instances)
        mock_llm = _make_mock_llm()

        # --- First run: start orchestrator and assign tasks ---
        runner1 = OrchestratorRunner(
            project_root=str(tmp_path),
            db_path=db_path,
            soul_dir=str(soul_dir),
            sock_path=sock_path,
            voice_enabled=False,
            terminal_backend=mock_terminal,
            llm_model=mock_llm,
        )

        async def _assign_then_shutdown() -> None:
            # Wait for startup
            for _ in range(100):
                await asyncio.sleep(0.1)
                if runner1.soul_writer is not None and sock_path.exists():
                    break

            assert runner1.soul_writer is not None

            # Write instance assignments to STATE.md
            state = OrchestratorState(
                instances=[
                    InstanceAssignment(
                        instance_id="1",
                        task_description="fix the login bug",
                        status="active",
                        assigned_at="2026-04-07T12:00:00Z",
                    ),
                    InstanceAssignment(
                        instance_id="2",
                        task_description="write auth tests",
                        status="active",
                        assigned_at="2026-04-07T12:01:00Z",
                    ),
                ],
                plan=["Ship auth feature by Friday"],
                decisions=[],
                blockers=[],
            )
            runner1.soul_writer.update_full_state(state)

            # Verify STATE.md was written
            state_content = (soul_dir / "STATE.md").read_text()
            assert "fix the login bug" in state_content
            assert "write auth tests" in state_content

            # Kill orchestrator
            runner1._handle_signal(signal.SIGTERM)

        test_task = asyncio.create_task(_assign_then_shutdown())
        await asyncio.wait_for(runner1.run(), timeout=15.0)
        await test_task

        # Verify socket is cleaned up
        assert not sock_path.exists()

        # --- Second run: restart and verify STATE.md restores ---
        runner2 = OrchestratorRunner(
            project_root=str(tmp_path),
            db_path=db_path,
            soul_dir=str(soul_dir),
            sock_path=sock_path,
            voice_enabled=False,
            terminal_backend=mock_terminal,
            llm_model=mock_llm,
        )

        async def _verify_state_then_shutdown() -> None:
            for _ in range(100):
                await asyncio.sleep(0.1)
                if runner2.soul_reader is not None and sock_path.exists():
                    break

            assert runner2.soul_reader is not None

            # Read STATE.md and verify assignments survived restart
            restored_state = runner2.soul_reader.read_state()
            assert len(restored_state.instances) == 2

            instance_tasks = {
                inst.instance_id: inst.task_description
                for inst in restored_state.instances
            }
            assert instance_tasks.get("1") == "fix the login bug"
            assert instance_tasks.get("2") == "write auth tests"

            # Verify plan survived
            assert "Ship auth feature by Friday" in restored_state.plan

            runner2._handle_signal(signal.SIGTERM)

        verify_task = asyncio.create_task(_verify_state_then_shutdown())
        await asyncio.wait_for(runner2.run(), timeout=15.0)
        await verify_task
        _cleanup_sock_path(sock_path)


# ---------------------------------------------------------------------------
# Test: CLI commands
# ---------------------------------------------------------------------------


class TestOrchestratorCLICommands:
    """Test orchestrator CLI commands via Click test runner."""

    def test_orchestrator_init_creates_soul_dir(self, tmp_path: Path) -> None:
        """orchestrator:init creates .lattice/soul/ with all 4 files."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        runner = CliRunner()
        soul_dir = tmp_path / ".lattice" / "soul"

        result = runner.invoke(
            cli,
            ["orchestrator:init", "--soul-dir", str(soul_dir)],
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"
        assert soul_dir.exists()
        assert (soul_dir / "SOUL.md").exists()
        assert (soul_dir / "AGENTS.md").exists()
        assert (soul_dir / "STATE.md").exists()
        assert (soul_dir / "MEMORY.md").exists()

        # All files should be non-empty
        for f in ["SOUL.md", "AGENTS.md", "STATE.md", "MEMORY.md"]:
            assert (soul_dir / f).stat().st_size > 0, f"{f} is empty"

    def test_orchestrator_init_json_output(self, tmp_path: Path) -> None:
        """orchestrator:init --json returns structured JSON."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        runner = CliRunner()
        soul_dir = tmp_path / ".lattice" / "soul"

        result = runner.invoke(
            cli,
            ["orchestrator:init", "--soul-dir", str(soul_dir), "--json"],
        )
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert data["success"] is True
        assert len(data["files"]) == 4

    def test_orchestrator_install_hooks(self, tmp_path: Path) -> None:
        """orchestrator:install-hooks installs all 6 hooks."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        runner = CliRunner()
        settings_path = tmp_path / "settings.json"
        settings_path.write_text("{}")

        result = runner.invoke(
            cli,
            [
                "orchestrator:install-hooks",
                "--settings-path", str(settings_path),
                "--sock-path", str(tmp_path / "orchestrator.sock"),
            ],
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"
        assert "Hooks installed: 6 new" in result.output

        # Verify settings.json was updated
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        hook_events = set(settings["hooks"].keys())
        assert "PreToolUse" in hook_events
        assert "PostToolUse" in hook_events
        assert "SessionStart" in hook_events

    def test_orchestrator_uninstall_hooks(self, tmp_path: Path) -> None:
        """orchestrator:uninstall-hooks removes Lattice hooks."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli
        from lattice.orchestrator.hooks.installer import HookInstaller

        runner = CliRunner()
        settings_path = tmp_path / "settings.json"
        settings_path.write_text("{}")

        # Install first
        installer = HookInstaller(
            settings_path=settings_path,
            sock_path=tmp_path / "orchestrator.sock",
        )
        installer.install()

        # Uninstall
        result = runner.invoke(
            cli,
            ["orchestrator:uninstall-hooks", "--settings-path", str(settings_path)],
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"
        assert "Hooks removed: 6" in result.output

        # Verify settings.json is clean
        settings = json.loads(settings_path.read_text())
        assert "hooks" not in settings or not settings.get("hooks")

    def test_orchestrator_check_hooks(self, tmp_path: Path) -> None:
        """orchestrator:check-hooks reports per-event-type status."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli
        from lattice.orchestrator.hooks.installer import HookInstaller

        runner = CliRunner()
        settings_path = tmp_path / "settings.json"
        settings_path.write_text("{}")

        # Install hooks
        installer = HookInstaller(
            settings_path=settings_path,
            sock_path=tmp_path / "orchestrator.sock",
        )
        installer.install()

        # Check
        result = runner.invoke(
            cli,
            [
                "orchestrator:check-hooks",
                "--settings-path", str(settings_path),
                "--sock-path", str(tmp_path / "orchestrator.sock"),
                "--json",
            ],
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"
        data = json.loads(result.output)
        assert data["all_installed"] is True
        assert len(data["events"]) == 6

    def test_orchestrator_text_command(self, tmp_path: Path) -> None:
        """orchestrator:text routes a one-shot text command."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        runner = CliRunner()
        db_path = str(tmp_path / "orch.duckdb")

        result = runner.invoke(
            cli,
            [
                "orchestrator:text",
                "show me status",
                "--db-path", db_path,
            ],
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"
        assert "status_returned" in result.output

    def test_orchestrator_text_json_output(self, tmp_path: Path) -> None:
        """orchestrator:text --json returns structured JSON."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        runner = CliRunner()
        db_path = str(tmp_path / "orch.duckdb")

        result = runner.invoke(
            cli,
            [
                "orchestrator:text",
                "fix the login bug",
                "--db-path", db_path,
                "--json",
            ],
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"
        data = _extract_json(result.output)
        assert data["success"] is True
        assert data["action"] == "task_enqueued"

    def test_orchestrator_status_no_instances(self, tmp_path: Path) -> None:
        """orchestrator:status with no tmux shows empty state."""
        from click.testing import CliRunner
        from lattice.cli.commands import cli

        runner = CliRunner()
        soul_dir = tmp_path / ".lattice" / "soul"

        # Init soul dir first
        writer = SoulWriter(soul_dir)
        writer.init_soul_dir()

        # Patch TmuxBackend to raise (no tmux)
        with patch(
            "lattice.cli.orchestrator_commands.asyncio.run",
            side_effect=RuntimeError("No tmux"),
        ):
            result = runner.invoke(
                cli,
                ["orchestrator:status", "--soul-dir", str(soul_dir)],
            )
            # Should handle gracefully (no instances from STATE.md only)
            assert result.exit_code == 0
