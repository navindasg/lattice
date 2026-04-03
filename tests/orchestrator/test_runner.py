"""Tests for OrchestratorRunner: async event loop, wiring, and graceful shutdown.

Covers:
- OrchestratorRunner initializes with defaults
- run() spawns mapper and wires VoicePipeline
- shutdown() terminates mapper subprocesses and managed instances
- Signal handler triggers shutdown event
- Process monitor detects and respawns dead mapper subprocess
"""
import asyncio
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lattice.orchestrator.runner import OrchestratorRunner
from lattice.orchestrator.models import OrchestratorConfig
from lattice.orchestrator.voice.models import VoiceConfig


class TestOrchestratorRunnerInit:
    def test_default_init(self, tmp_path: Path) -> None:
        """OrchestratorRunner initializes with sensible defaults."""
        runner = OrchestratorRunner(project_root=str(tmp_path))
        assert runner._project_root == str(tmp_path)
        assert runner._voice_enabled is True
        assert runner._manager is None
        assert runner._pipeline is None

    def test_voice_disabled(self, tmp_path: Path) -> None:
        """voice_enabled=False skips voice listener task."""
        runner = OrchestratorRunner(
            project_root=str(tmp_path), voice_enabled=False
        )
        assert runner._voice_enabled is False

    def test_custom_config(self, tmp_path: Path) -> None:
        """Custom OrchestratorConfig and VoiceConfig are stored."""
        orch_cfg = OrchestratorConfig(max_instances=5)
        voice_cfg = VoiceConfig()
        runner = OrchestratorRunner(
            project_root=str(tmp_path),
            orchestrator_config=orch_cfg,
            voice_config=voice_cfg,
        )
        assert runner._orch_config.max_instances == 5


class TestOrchestratorRunnerShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_terminates_mapper_and_instances(self, tmp_path: Path) -> None:
        """shutdown() terminates mapper subprocesses then non-mapper instances."""
        runner = OrchestratorRunner(project_root=str(tmp_path))

        # Mock a live mapper subprocess
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        mock_manager = MagicMock()
        mock_manager.mapper_processes = {"/proj": mock_proc}
        mock_manager.instance_id_for_process = MagicMock(return_value="mapper-inst")
        mock_manager.instance_ids = ["mapper-inst", "cc-inst"]
        mock_manager.terminate = AsyncMock(return_value="graceful")
        runner._manager = mock_manager
        runner._db_conn = MagicMock()

        mock_db = runner._db_conn

        await runner.shutdown()

        # Mapper subprocess terminated directly
        mock_proc.terminate.assert_called_once()
        # Only CC instance terminated via manager (mapper skipped to avoid double-term)
        assert mock_manager.terminate.call_count == 1
        mock_manager.terminate.assert_called_with("cc-inst")
        mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_kills_mapper_on_timeout(self, tmp_path: Path) -> None:
        """shutdown() kills mapper if it doesn't exit within timeout."""
        runner = OrchestratorRunner(project_root=str(tmp_path))

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        # First wait (from wait_for) times out, second wait (after kill) succeeds
        mock_proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError, None])
        mock_proc.kill = MagicMock()

        mock_manager = MagicMock()
        mock_manager.mapper_processes = {"/proj": mock_proc}
        mock_manager.instance_id_for_process = MagicMock(return_value="mapper-inst")
        mock_manager.instance_ids = ["mapper-inst"]
        mock_manager.terminate = AsyncMock()
        runner._manager = mock_manager
        runner._db_conn = MagicMock()

        await runner.shutdown()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        # wait() called twice: once in wait_for (timeout), once after kill
        assert mock_proc.wait.call_count == 2

    @pytest.mark.asyncio
    async def test_shutdown_handles_no_manager(self, tmp_path: Path) -> None:
        """shutdown() is safe to call when manager was never created."""
        runner = OrchestratorRunner(project_root=str(tmp_path))
        await runner.shutdown()  # Should not raise


class TestOrchestratorRunnerSignal:
    @pytest.mark.asyncio
    async def test_handle_signal_sets_event(self, tmp_path: Path) -> None:
        """_handle_signal sets the shutdown event."""
        runner = OrchestratorRunner(project_root=str(tmp_path))
        # Simulate run() creating the event inside the running loop
        runner._shutdown_event = asyncio.Event()
        assert not runner._shutdown_event.is_set()
        runner._handle_signal(signal.SIGTERM)
        assert runner._shutdown_event.is_set()

    def test_handle_signal_noop_without_event(self, tmp_path: Path) -> None:
        """_handle_signal is safe before run() creates the event."""
        runner = OrchestratorRunner(project_root=str(tmp_path))
        assert runner._shutdown_event is None
        runner._handle_signal(signal.SIGTERM)  # Should not raise


class TestOrchestratorRunnerRun:
    @pytest.mark.asyncio
    async def test_run_spawns_mapper_and_shuts_down(self, tmp_path: Path) -> None:
        """run() creates ProcessManager, spawns mapper, then shuts down."""
        db_path = str(tmp_path / "test.duckdb")
        runner = OrchestratorRunner(
            project_root=str(tmp_path),
            db_path=db_path,
            voice_enabled=False,
        )

        mock_manager = MagicMock()
        mock_manager.recover_orphans.return_value = []
        mock_manager.spawn_mapper = AsyncMock()
        mock_manager.mapper_processes = {}
        mock_manager.instance_id_for_process = MagicMock(return_value=None)
        mock_manager.instance_ids = []
        mock_manager.terminate = AsyncMock()

        with patch(
            "lattice.orchestrator.runner.ProcessManager", return_value=mock_manager
        ):
            async def _trigger_shutdown():
                await asyncio.sleep(0.1)
                runner._shutdown_event.set()

            asyncio.create_task(_trigger_shutdown())
            await runner.run()

        mock_manager.recover_orphans.assert_called_once()
        mock_manager.spawn_mapper.assert_called_once_with(str(tmp_path))


class TestOrchestratorRunnerMonitor:
    @pytest.mark.asyncio
    async def test_monitor_respawns_dead_mapper(self, tmp_path: Path) -> None:
        """_monitor_processes respawns a mapper that has died."""
        runner = OrchestratorRunner(project_root=str(tmp_path))
        runner._shutdown_event = asyncio.Event()

        dead_proc = MagicMock()
        dead_proc.returncode = 1  # Dead

        call_count = 0
        original_items = {"/proj": dead_proc}.items

        mock_manager = MagicMock()
        mock_manager.mapper_processes = {"/proj": dead_proc}

        async def _spawn_and_stop(project: str) -> None:
            nonlocal call_count
            call_count += 1
            # After respawn, stop the monitor
            runner._shutdown_event.set()

        mock_manager.spawn_mapper = AsyncMock(side_effect=_spawn_and_stop)
        runner._manager = mock_manager

        # Use a very short poll interval by patching asyncio.wait_for to timeout immediately
        original_wait_for = asyncio.wait_for

        async def _fast_wait_for(coro, *, timeout):
            try:
                return await original_wait_for(coro, timeout=0.01)
            except TimeoutError:
                raise

        with patch("asyncio.wait_for", side_effect=_fast_wait_for):
            await runner._monitor_processes()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_monitor_shuts_down_on_respawn_failure(self, tmp_path: Path) -> None:
        """_monitor_processes triggers shutdown if respawn fails."""
        runner = OrchestratorRunner(project_root=str(tmp_path))
        runner._shutdown_event = asyncio.Event()

        dead_proc = MagicMock()
        dead_proc.returncode = 1

        mock_manager = MagicMock()
        mock_manager.mapper_processes = {"/proj": dead_proc}
        mock_manager.spawn_mapper = AsyncMock(side_effect=RuntimeError("spawn failed"))
        runner._manager = mock_manager

        original_wait_for = asyncio.wait_for

        async def _fast_wait_for(coro, *, timeout):
            try:
                return await original_wait_for(coro, timeout=0.01)
            except TimeoutError:
                raise

        with patch("asyncio.wait_for", side_effect=_fast_wait_for):
            await runner._monitor_processes()

        assert runner._shutdown_event.is_set()
