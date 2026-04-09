"""Full orchestrator runner: wires event server, agent, terminal, voice, and soul.

Provides OrchestratorRunner which integrates all orchestrator subsystems into
a single async lifecycle:

1. Init soul directory and verify files
2. Start EventServer on UDS socket (with socket lock)
3. Detect CC instances via TmuxBackend
4. Build LangGraph agent with ToolContext wired to terminal backend
5. Start AgentEventLoop consuming from event queue
6. Optionally start VoicePipeline with push-to-talk
7. Monitor CC instance health and event server health
8. Graceful shutdown: flush STATE.md, stop event server, remove socket, terminate children

Handles: duplicate start detection, event server crash recovery, CC instance exit
detection, SIGTERM/SIGINT, and context compaction flushes.
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

import duckdb
import structlog

from lattice.orchestrator.events.runner import EventServer
from lattice.orchestrator.events.server import submit_approval
from lattice.orchestrator.agent.checkpointer import DuckDBCheckpointer
from lattice.orchestrator.agent.event_loop import AgentEventLoop
from lattice.orchestrator.agent.graph import build_orchestrator_graph
from lattice.orchestrator.agent.tools import ToolContext
from lattice.orchestrator.hooks.installer import HookInstaller
from lattice.orchestrator.manager import ProcessManager
from lattice.orchestrator.models import OrchestratorConfig
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.soul_ecosystem.writer import SoulWriter
from lattice.orchestrator.voice.models import VoiceConfig
from lattice.orchestrator.voice.pipeline import VoicePipeline
from lattice.orchestrator.voice.router import IntentRouter

log = structlog.get_logger(__name__)

_DEFAULT_SOUL_DIR = ".lattice/soul"
_DEFAULT_SOCK_PATH = Path.home() / ".lattice" / "orchestrator.sock"
_DEFAULT_DB_PATH = ".lattice/orchestrator.duckdb"

# How often to check CC instance health and event server health (seconds)
_HEALTH_CHECK_INTERVAL = 5.0

# Max event server restart attempts before giving up
_MAX_EVENT_SERVER_RESTARTS = 3


class OrchestratorRunner:
    """Full orchestrator lifecycle: event server + agent + terminal + voice + soul.

    Integrates EventServer, AgentEventLoop, TmuxBackend (optional),
    SoulReader/Writer, VoicePipeline, and ProcessManager into a single
    async event loop with graceful shutdown.

    Args:
        project_root: Absolute path to the project root directory.
        db_path: Path to orchestrator DuckDB file.
        soul_dir: Path to .lattice/soul/ directory.
        sock_path: Path to UDS socket for event channel.
        orchestrator_config: Fleet-wide orchestrator settings.
        voice_config: Voice pipeline configuration.
        voice_enabled: Whether to start the voice listener.
        terminal_backend: Optional pre-created terminal backend (for testing).
            If None, auto-detects tmux.
        llm_model: Optional pre-created LLM model for agent (for testing).
    """

    def __init__(
        self,
        project_root: str,
        db_path: str = _DEFAULT_DB_PATH,
        soul_dir: str = _DEFAULT_SOUL_DIR,
        sock_path: Path | None = None,
        orchestrator_config: OrchestratorConfig | None = None,
        voice_config: VoiceConfig | None = None,
        voice_enabled: bool = True,
        terminal_backend: Any | None = None,
        llm_model: Any | None = None,
        initial_task: str | None = None,
    ) -> None:
        self._project_root = project_root
        self._db_path = db_path
        self._soul_dir_path = Path(project_root) / soul_dir
        self._initial_task = initial_task
        self._sock_path = sock_path or _DEFAULT_SOCK_PATH
        self._orch_config = orchestrator_config or OrchestratorConfig()
        self._voice_config = voice_config or VoiceConfig()
        self._voice_enabled = voice_enabled
        self._terminal_backend = terminal_backend
        self._llm_model = llm_model

        # Runtime state — populated during run()
        self._manager: ProcessManager | None = None
        self._pipeline: VoicePipeline | None = None
        self._event_server: EventServer | None = None
        self._agent_loop: AgentEventLoop | None = None
        self._soul_reader: SoulReader | None = None
        self._soul_writer: SoulWriter | None = None
        self._db_conn: duckdb.DuckDBPyConnection | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._event_server_restarts = 0
        self._instance_pane_map: dict[str, str] = {}

    @property
    def event_server(self) -> EventServer | None:
        """The EventServer instance (available after run() starts)."""
        return self._event_server

    @property
    def agent_loop(self) -> AgentEventLoop | None:
        """The AgentEventLoop instance (available after run() starts)."""
        return self._agent_loop

    @property
    def soul_reader(self) -> SoulReader | None:
        """The SoulReader instance."""
        return self._soul_reader

    @property
    def soul_writer(self) -> SoulWriter | None:
        """The SoulWriter instance."""
        return self._soul_writer

    @property
    def instance_pane_map(self) -> dict[str, str]:
        """Map of CC instance number → tmux pane_id."""
        return self._instance_pane_map

    async def run(self) -> None:
        """Start the full orchestrator lifecycle.

        Executes the startup sequence, runs all subsystems concurrently,
        and handles graceful shutdown on signal or fatal error.
        """
        self._shutdown_event = asyncio.Event()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal, sig)

        # Step 1: Check for existing orchestrator (socket lock)
        if self._sock_path.exists():
            import socket as _socket
            try:
                with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
                    s.settimeout(2.0)
                    s.connect(str(self._sock_path))
                raise SystemExit("Orchestrator already running")
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                # Stale socket — will be cleaned up by EventServer.start()
                pass

        try:
            # Step 2: Open DuckDB
            db_file = Path(self._db_path)
            db_file.parent.mkdir(parents=True, exist_ok=True)
            self._db_conn = duckdb.connect(str(db_file))

            # Step 3: Init soul directory
            self._soul_reader = SoulReader(self._soul_dir_path)
            self._soul_writer = SoulWriter(self._soul_dir_path)
            self._soul_writer.init_soul_dir()

            # Step 4: Start event server
            self._event_server = EventServer(
                db_conn=self._db_conn,
                sock_path=self._sock_path,
            )
            event_queue = await self._event_server.start()

            # Step 5: Check hooks (warn if not installed)
            hook_installer = HookInstaller(sock_path=self._sock_path)
            hook_check = hook_installer.check()
            if not hook_check.all_installed:
                missing = [e.event_type for e in hook_check.events if not e.installed]
                log.warning(
                    "hooks_not_fully_installed",
                    missing=missing,
                    hint="Run 'lattice orchestrator:install-hooks' to configure",
                )

            # Step 6: Detect CC instances via terminal backend
            cc_instances = await self._detect_cc_instances()

            # Step 7: Build agent graph with ToolContext
            tool_context = ToolContext(
                terminal=self._terminal_backend,
                soul_reader=self._soul_reader,
                soul_writer=self._soul_writer,
                event_loop=asyncio.get_running_loop(),
                instance_pane_map=self._instance_pane_map,
                shadow_root=Path(self._project_root) / ".agent-docs",
                approval_submit=lambda eid, dec: submit_approval(
                    self._event_server.app, eid, dec
                ),
            )

            compiled_graph = self._build_agent_graph(tool_context)

            # Step 8: Start agent event loop
            self._agent_loop = AgentEventLoop(
                graph=compiled_graph,
                event_queue=event_queue,
                soul_reader=self._soul_reader,
                soul_writer=self._soul_writer,
                approval_submit=tool_context.approval_submit,
                shutdown_event=self._shutdown_event,
            )

            # Step 9: Setup process manager and mapper
            self._manager = ProcessManager(self._db_conn, self._orch_config)
            self._manager.recover_orphans()
            await self._manager.spawn_mapper(self._project_root)

            mapper_procs = self._manager.mapper_processes

            # Step 10: Setup voice pipeline
            router = IntentRouter(
                db_conn=self._db_conn,
                mapper_processes=mapper_procs,
            )
            self._pipeline = VoicePipeline(
                config=self._voice_config,
                router=router,
                mapper_processes=mapper_procs,
            )

            # Print instance table
            self._print_instance_table(cc_instances)

            # Step 11: Launch concurrent tasks
            tasks: list[asyncio.Task[Any]] = [
                asyncio.create_task(
                    self._agent_loop.run(), name="agent_event_loop"
                ),
                asyncio.create_task(
                    self._monitor_health(), name="health_monitor"
                ),
            ]
            if self._voice_enabled:
                tasks.append(
                    asyncio.create_task(
                        self._pipeline.run_listener(), name="voice_listener"
                    )
                )

            log.info(
                "orchestrator_running",
                project_root=self._project_root,
                voice_enabled=self._voice_enabled,
                instances=len(cc_instances),
                socket=str(self._sock_path),
            )
            if self._voice_enabled:
                log.info("voice_listener_ready")

            # Inject initial task as a synthetic event if provided
            if self._initial_task:
                from datetime import datetime, timezone
                from lattice.orchestrator.events.models import CCEvent

                synthetic = CCEvent(
                    session_id="initial-task",
                    event_type="TaskAssignment",
                    tool_name=None,
                    tool_input=None,
                    tool_response=self._initial_task,
                    transcript_path=None,
                    cwd=self._project_root,
                    timestamp=datetime.now(timezone.utc),
                )
                await event_queue.put(synthetic)
                log.info(
                    "initial_task_injected",
                    task=self._initial_task[:100],
                )

            # Wait for shutdown signal or task failure
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                if task.exception() is not None:
                    log.error(
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
        """Gracefully terminate all subsystems and clean up resources.

        Order: flush state → stop event server → terminate children → close DB.
        Safe to call multiple times.
        """
        if self._shutdown_event is not None:
            self._shutdown_event.set()

        # Flush STATE.md before shutting down
        if self._agent_loop is not None:
            try:
                await self._agent_loop.flush_state()
            except Exception as exc:
                log.warning("shutdown_flush_error", error=str(exc))

        # Stop event server (removes socket file)
        if self._event_server is not None:
            try:
                await self._event_server.stop()
            except Exception as exc:
                log.warning("shutdown_event_server_error", error=str(exc))

        # Terminate mapper and managed CC instances
        if self._manager is not None:
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
                    log.info("mapper_terminated", project=project)

            for instance_id in self._manager.instance_ids:
                if instance_id in mapper_instance_ids:
                    continue
                try:
                    await self._manager.terminate(instance_id)
                except Exception as exc:
                    log.warning(
                        "shutdown_terminate_error",
                        instance_id=instance_id,
                        error=str(exc),
                    )

        # Shut down PTY backend if applicable
        try:
            from lattice.orchestrator.terminal.pty_backend import PTYBackend
            if isinstance(self._terminal_backend, PTYBackend):
                self._terminal_backend.shutdown()
                log.info("pty_backend_shutdown")
        except Exception as exc:
            log.warning("shutdown_pty_error", error=str(exc))

        # Ensure socket file is removed even if event server didn't clean up
        if self._sock_path.exists():
            self._sock_path.unlink(missing_ok=True)

        log.info("orchestrator_shutdown_complete")

        if self._db_conn is not None:
            self._db_conn.close()
            self._db_conn = None

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Signal handler that triggers graceful shutdown."""
        log.info("orchestrator_signal_received", signal=sig.name)
        if self._shutdown_event is not None:
            self._shutdown_event.set()

    async def _detect_cc_instances(self) -> list[Any]:
        """Detect CC instances via terminal backend.

        Uses PTYBackend by default (direct PTY management). Falls back
        to TmuxBackend if PTYBackend is not available.

        Returns:
            List of CCInstance objects detected.
        """
        if self._terminal_backend is None:
            try:
                from lattice.orchestrator.terminal.pty_backend import PTYBackend
                from lattice.ui.pty_manager import PTYManager

                pty_mgr = PTYManager()
                self._terminal_backend = PTYBackend(pty_mgr)
                log.info("terminal_backend.pty_initialized")
            except Exception as exc:
                log.warning(
                    "pty_backend_init_failed",
                    error=str(exc),
                    hint="Falling back to TmuxBackend",
                )
                try:
                    from lattice.orchestrator.terminal.tmux import TmuxBackend
                    self._terminal_backend = TmuxBackend()
                except RuntimeError as tmux_exc:
                    error_msg = str(tmux_exc)
                    if "No tmux server" in error_msg:
                        log.error("no_terminal_backend")
                        raise SystemExit(
                            "No terminal backend available. "
                            "PTYBackend failed and no tmux server found."
                        ) from tmux_exc
                    raise

        instances = await self._terminal_backend.detect_cc_panes()

        if not instances:
            log.info(
                "no_cc_instances_detected",
                hint="Use cc_spawn to create Claude Code instances.",
            )

        # Build instance → pane map for agent tools (immutable rebuild)
        self._instance_pane_map = {
            str(inst.user_number): inst.pane_id for inst in instances
        }

        return instances

    def _build_agent_graph(self, tool_context: ToolContext) -> Any:
        """Build and compile the LangGraph orchestrator agent.

        If no LLM model is provided, creates a default Anthropic model.

        Args:
            tool_context: Populated ToolContext for agent tools.

        Returns:
            Compiled LangGraph StateGraph.
        """
        model = self._llm_model
        if model is None:
            from langchain_anthropic import ChatAnthropic
            from lattice.llm.config import LatticeSettings

            settings = LatticeSettings()
            api_key = settings.anthropic_api_key
            if not api_key:
                import os
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise SystemExit(
                    "ANTHROPIC_API_KEY not set. Add it to .env or export it."
                )

            model = ChatAnthropic(
                model="claude-sonnet-4-6",
                temperature=0.0,
                max_tokens=8192,
                api_key=api_key,
            )

        graph = build_orchestrator_graph(
            model=model,
            tool_context=tool_context,
            soul_reader=self._soul_reader,
        )

        checkpointer = DuckDBCheckpointer(self._db_conn)
        return graph.compile(checkpointer=checkpointer)

    def _print_instance_table(self, instances: list[Any]) -> None:
        """Print a formatted table of detected CC instances.

        Args:
            instances: List of CCInstance objects to display.
        """
        if not instances:
            return

        log.info("detected_cc_instances", count=len(instances))
        for inst in instances:
            log.info(
                "cc_instance",
                instance=inst.user_number,
                pane=inst.pane_id,
                cwd=inst.cwd,
                command=inst.running_command,
            )

    async def _monitor_health(self) -> None:
        """Periodically check subsystem health.

        Monitors:
        - Mapper subprocess health (respawn if dead)
        - Event server health (restart if crashed, up to max retries)
        - CC instance health (update STATE.md on exit)
        """
        assert self._shutdown_event is not None

        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=_HEALTH_CHECK_INTERVAL,
                )
            except TimeoutError:
                pass

            if self._shutdown_event.is_set():
                break

            # Check mapper processes
            if self._manager is not None:
                for project, proc in list(self._manager.mapper_processes.items()):
                    if proc.returncode is not None:
                        log.warning(
                            "mapper_process_died",
                            project=project,
                            returncode=proc.returncode,
                        )
                        try:
                            await self._manager.spawn_mapper(project)
                            log.info("mapper_process_respawned", project=project)
                        except Exception as exc:
                            log.error(
                                "mapper_respawn_failed",
                                project=project,
                                error=str(exc),
                            )
                            self._shutdown_event.set()
                            return

            # Check event server health
            if self._event_server is not None and not self._event_server.is_serving:
                exc = self._event_server.serve_error
                if exc is not None or not self._event_server.is_serving:
                    if self._event_server_restarts < _MAX_EVENT_SERVER_RESTARTS:
                        self._event_server_restarts += 1
                        log.error(
                            "event_server_crashed_restarting",
                            attempt=self._event_server_restarts,
                            error=str(exc) if exc else "cancelled",
                        )
                        try:
                            await self._event_server.start()
                            log.info("event_server_restarted")
                        except Exception as restart_exc:
                            log.error(
                                "event_server_restart_failed",
                                error=str(restart_exc),
                            )
                            self._shutdown_event.set()
                            return
                    else:
                        log.error(
                            "event_server_max_restarts_exceeded",
                            max_restarts=_MAX_EVENT_SERVER_RESTARTS,
                        )
                        self._shutdown_event.set()
                        return

            # Check CC instance health (detect exited instances)
            await self._check_cc_instance_health()

    async def _check_cc_instance_health(self) -> None:
        """Detect exited CC instances and update STATE.md.

        Re-scans terminal backend for CC panes and marks any missing
        instances as 'exited' in STATE.md.
        """
        if self._terminal_backend is None or self._soul_writer is None:
            return

        try:
            current_instances = await self._terminal_backend.detect_cc_panes()
        except Exception:
            return

        current_pane_ids = {inst.pane_id for inst in current_instances}

        # Detect exited instances and update STATE.md
        exited_instances: list[str] = []
        for instance_num, pane_id in self._instance_pane_map.items():
            if pane_id not in current_pane_ids:
                log.warning(
                    "cc_instance_exited",
                    instance=instance_num,
                    pane_id=pane_id,
                )
                exited_instances.append(instance_num)

        if exited_instances:
            from lattice.orchestrator.soul_ecosystem.models import OrchestratorState

            state = self._soul_reader.read_state()
            exited_set = frozenset(exited_instances)
            updated_instances = [
                inst.model_copy(update={"status": "exited"})
                if inst.instance_id in exited_set
                else inst
                for inst in state.instances
            ]
            updated_state = OrchestratorState(
                instances=updated_instances,
                plan=list(state.plan),
                decisions=list(state.decisions),
                blockers=list(state.blockers),
            )
            self._soul_writer.update_full_state(updated_state)

        # Rebuild pane map immutably: keep live instances, add new ones
        updated_map = {
            num: pid
            for num, pid in self._instance_pane_map.items()
            if pid in current_pane_ids
        }
        for inst in current_instances:
            num = str(inst.user_number)
            if num not in updated_map:
                updated_map[num] = inst.pane_id
        self._instance_pane_map = updated_map

    async def process_text_command(self, text: str) -> dict[str, Any]:
        """Process a one-shot text command through the voice pipeline.

        Used by `lattice orchestrator:text "command"`.

        Args:
            text: The text command to process.

        Returns:
            Dict with action and detail from RouteResult.
        """
        if self._pipeline is None:
            return {"success": False, "error": "Pipeline not initialized"}

        result = await self._pipeline.process_text_async(text)
        return {
            "success": result.success,
            "action": result.action,
            "detail": result.detail,
            "data": result.data,
        }

    def get_status_table(self) -> list[dict[str, Any]]:
        """Build a status table of all known CC instances.

        Reads STATE.md for assignments and cross-references with
        the live instance_pane_map.

        Returns:
            List of dicts with instance status information.
        """
        if self._soul_reader is None:
            return []

        state = self._soul_reader.read_state()
        rows: list[dict[str, Any]] = []

        for inst in state.instances:
            pane_id = self._instance_pane_map.get(inst.instance_id, "unknown")
            rows.append({
                "instance": inst.instance_id,
                "pane_id": pane_id,
                "task": inst.task_description,
                "status": inst.status,
                "assigned_at": inst.assigned_at,
            })

        # Add instances in pane map but not in STATE.md
        state_ids = {inst.instance_id for inst in state.instances}
        for num, pane_id in self._instance_pane_map.items():
            if num not in state_ids:
                rows.append({
                    "instance": num,
                    "pane_id": pane_id,
                    "task": "unassigned",
                    "status": "idle",
                    "assigned_at": None,
                })

        return rows
