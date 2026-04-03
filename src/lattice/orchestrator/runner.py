"""Async orchestrator runner: shared event loop for ProcessManager + VoicePipeline.

Provides OrchestratorRunner which wires ProcessManager mapper subprocesses to
VoicePipeline, keeps both alive in a single asyncio event loop, and handles
graceful shutdown on SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

import duckdb
import structlog

from lattice.orchestrator.manager import ProcessManager
from lattice.orchestrator.models import OrchestratorConfig
from lattice.orchestrator.voice.models import VoiceConfig
from lattice.orchestrator.voice.pipeline import VoicePipeline
from lattice.orchestrator.voice.router import IntentRouter

log = structlog.get_logger(__name__)


class OrchestratorRunner:
    """Runs ProcessManager and VoicePipeline in a shared async event loop.

    Lifecycle:
        1. Opens DuckDB registry
        2. Creates ProcessManager, spawns mapper subprocess per project
        3. Creates VoicePipeline wired to live mapper_processes dict
        4. Runs voice listener and process health monitor concurrently
        5. On SIGTERM/SIGINT, gracefully shuts down all subprocesses

    Args:
        project_root: Absolute path to the project root directory.
        db_path: Path to orchestrator DuckDB file.
        orchestrator_config: Fleet-wide orchestrator settings.
        voice_config: Voice pipeline configuration.
        voice_enabled: Whether to start the voice listener. When False, only
            the process manager and mapper subprocess run.
    """

    def __init__(
        self,
        project_root: str,
        db_path: str = ".lattice/orchestrator.duckdb",
        orchestrator_config: OrchestratorConfig | None = None,
        voice_config: VoiceConfig | None = None,
        voice_enabled: bool = True,
    ) -> None:
        self._project_root = project_root
        self._db_path = db_path
        self._orch_config = orchestrator_config or OrchestratorConfig()
        self._voice_config = voice_config or VoiceConfig()
        self._voice_enabled = voice_enabled
        self._manager: ProcessManager | None = None
        self._pipeline: VoicePipeline | None = None
        self._db_conn: duckdb.DuckDBPyConnection | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._log = structlog.get_logger(__name__)

    async def run(self) -> None:
        """Start the orchestrator event loop.

        Opens DuckDB, spawns mapper subprocess, wires VoicePipeline, and
        runs until shutdown signal or fatal error.
        """
        # Create Event inside the running loop to avoid cross-loop issues
        self._shutdown_event = asyncio.Event()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal, sig)

        db_file = Path(self._db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self._db_conn = duckdb.connect(str(db_file))

        try:
            self._manager = ProcessManager(self._db_conn, self._orch_config)
            self._manager.recover_orphans()

            await self._manager.spawn_mapper(self._project_root)
            self._log.info(
                "orchestrator_mapper_ready",
                project_root=self._project_root,
            )

            # Pass the live dict reference so router/pipeline always see
            # current state (including respawned processes).
            mapper_procs = self._manager.mapper_processes

            router = IntentRouter(
                db_conn=self._db_conn,
                mapper_processes=mapper_procs,
            )
            self._pipeline = VoicePipeline(
                config=self._voice_config,
                router=router,
                mapper_processes=mapper_procs,
            )

            tasks: list[asyncio.Task[Any]] = [
                asyncio.create_task(
                    self._monitor_processes(), name="process_monitor"
                ),
            ]
            if self._voice_enabled:
                tasks.append(
                    asyncio.create_task(
                        self._pipeline.run_listener(), name="voice_listener"
                    )
                )

            self._log.info(
                "orchestrator_running",
                project_root=self._project_root,
                voice_enabled=self._voice_enabled,
            )

            # Wait for shutdown signal or any task to complete.
            # _monitor_processes exits when _shutdown_event is set (by signal
            # handler), which causes FIRST_COMPLETED to return and triggers
            # cleanup of remaining tasks.
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                if task.exception() is not None:
                    self._log.error(
                        "orchestrator_task_failed",
                        task_name=task.get_name(),
                        error=str(task.exception()),
                    )

            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully terminate all managed subprocesses and close DB."""
        if self._shutdown_event is not None:
            self._shutdown_event.set()

        if self._manager is not None:
            # Terminate mapper subprocesses directly (not via manager.terminate
            # since mapper procs are also registered in _instances and we want
            # to avoid double-termination).
            mapper_instance_ids: set[str] = set()
            for project, proc in self._manager.mapper_processes.items():
                iid = self._manager.instance_id_for_process(proc)
                if iid is not None:
                    mapper_instance_ids.add(iid)

                if proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                    self._log.info(
                        "mapper_terminated",
                        project=project,
                    )

            # Terminate remaining managed CC instances (skip mappers)
            for instance_id in self._manager.instance_ids:
                if instance_id in mapper_instance_ids:
                    continue
                try:
                    await self._manager.terminate(instance_id)
                except Exception as exc:
                    self._log.warning(
                        "shutdown_terminate_error",
                        instance_id=instance_id,
                        error=str(exc),
                    )
            self._log.info("orchestrator_shutdown_complete")

        if self._db_conn is not None:
            self._db_conn.close()
            self._db_conn = None

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Signal handler that triggers graceful shutdown."""
        self._log.info("orchestrator_signal_received", signal=sig.name)
        if self._shutdown_event is not None:
            self._shutdown_event.set()

    async def _monitor_processes(self) -> None:
        """Periodically check mapper subprocess health and respawn if dead."""
        assert self._shutdown_event is not None
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=5.0
                )
            except TimeoutError:
                pass

            if self._shutdown_event.is_set():
                break

            if self._manager is not None:
                for project, proc in list(self._manager.mapper_processes.items()):
                    if proc.returncode is not None:
                        self._log.warning(
                            "mapper_process_died",
                            project=project,
                            returncode=proc.returncode,
                        )
                        try:
                            await self._manager.spawn_mapper(project)
                            self._log.info(
                                "mapper_process_respawned", project=project
                            )
                        except Exception as exc:
                            self._log.error(
                                "mapper_respawn_failed",
                                project=project,
                                error=str(exc),
                            )
                            self._shutdown_event.set()
                            break
