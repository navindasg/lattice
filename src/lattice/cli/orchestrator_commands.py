"""Orchestrator CLI commands for Lattice.

Commands:
    orchestrator:init           — Create .lattice/soul/ with default templates
    orchestrator:start          — Full startup sequence (event server + agent + terminal)
    orchestrator:voice          — Start orchestrator with push-to-talk voice interface
    orchestrator:text <msg>     — One-shot text command to the orchestrator
    orchestrator:status         — Show detected CC instances and their state
    orchestrator:install-hooks  — Configure CC instances for hook integration
    orchestrator:uninstall-hooks— Remove Lattice hooks from CC settings
    orchestrator:check-hooks    — Report per-event-type hook status

All commands accept --json for structured JSON output.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click


@click.command("orchestrator:init")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option(
    "--soul-dir",
    default=None,
    help="Override soul directory path (default: .lattice/soul/)",
)
@click.pass_context
def orchestrator_init(ctx: click.Context, as_json: bool, soul_dir: str | None) -> None:
    """Create .lattice/soul/ with default SOUL.md, AGENTS.md, STATE.md, MEMORY.md."""
    from lattice.orchestrator.soul_ecosystem.writer import SoulWriter

    project_root = Path.cwd()
    soul_path = Path(soul_dir) if soul_dir else project_root / ".lattice" / "soul"

    writer = SoulWriter(soul_path)
    writer.init_soul_dir()

    files = ["SOUL.md", "AGENTS.md", "STATE.md", "MEMORY.md"]
    created = []
    for f in files:
        path = soul_path / f
        if path.exists() and path.stat().st_size > 0:
            created.append(f)

    if as_json:
        click.echo(json.dumps({
            "success": True,
            "soul_dir": str(soul_path),
            "files": created,
        }, indent=2))
    else:
        click.echo(f"Soul directory initialized: {soul_path}")
        for f in created:
            click.echo(f"  {f}")


@click.command("orchestrator:start")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--soul-dir", default=None, help="Override soul directory path")
@click.option("--db-path", default=".lattice/orchestrator.duckdb", help="DuckDB path")
@click.pass_context
def orchestrator_start(
    ctx: click.Context,
    as_json: bool,
    soul_dir: str | None,
    db_path: str,
) -> None:
    """Start the orchestrator: event server, agent, terminal detection.

    Runs the full startup sequence and enters the event loop. Exits on
    Ctrl+C or SIGTERM with graceful shutdown.
    """
    from lattice.orchestrator.runner import OrchestratorRunner

    project_root = str(Path.cwd())

    runner = OrchestratorRunner(
        project_root=project_root,
        db_path=db_path,
        soul_dir=soul_dir or ".lattice/soul",
        voice_enabled=False,
    )

    try:
        asyncio.run(runner.run())
    except SystemExit as exc:
        if as_json:
            click.echo(json.dumps({"success": False, "error": str(exc)}))
        else:
            click.echo(str(exc), err=True)
        ctx.exit(1)
    except KeyboardInterrupt:
        pass


@click.command("orchestrator:voice")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--soul-dir", default=None, help="Override soul directory path")
@click.option("--db-path", default=".lattice/orchestrator.duckdb", help="DuckDB path")
@click.option(
    "--text",
    "text_input",
    default=None,
    help="Process text input instead of audio (text fallback).",
)
@click.option(
    "--project",
    "project_root",
    default=None,
    help="Project root for live mapper subprocess.",
)
@click.pass_context
def orchestrator_voice(
    ctx: click.Context,
    as_json: bool,
    soul_dir: str | None,
    db_path: str,
    text_input: str | None,
    project_root: str | None,
) -> None:
    """Start voice listener or process text through intent router.

    Without --text, starts the full orchestrator with push-to-talk voice.
    With --text, processes the given text through the intent pipeline and exits.
    """
    from lattice.orchestrator.voice.models import VoiceConfig
    from lattice.orchestrator.voice.pipeline import VoicePipeline, format_voice_display
    from lattice.orchestrator.voice.router import IntentRouter

    # Text mode: lightweight path without full orchestrator
    if text_input is not None:
        import duckdb
        from lattice.api.models import success_response

        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(db_file))

        try:
            if project_root is not None:
                from lattice.orchestrator.manager import ProcessManager
                from lattice.orchestrator.models import OrchestratorConfig

                async def _spawn_and_process():
                    mgr = ProcessManager(conn, OrchestratorConfig())
                    await mgr.spawn_mapper(str(Path(project_root).resolve()))
                    procs = mgr.mapper_processes
                    _router = IntentRouter(db_conn=conn, mapper_processes=procs)
                    _pipeline = VoicePipeline(
                        config=VoiceConfig(), router=_router, mapper_processes=procs,
                    )
                    _result = await _pipeline.process_text_async(text_input)
                    for proc in mgr.mapper_processes.values():
                        if proc.returncode is None:
                            proc.terminate()
                            try:
                                await asyncio.wait_for(proc.wait(), timeout=5.0)
                            except asyncio.TimeoutError:
                                proc.kill()
                                await proc.wait()
                    return _result

                result = asyncio.run(_spawn_and_process())
            else:
                router = IntentRouter(db_conn=conn)
                pipeline = VoicePipeline(config=VoiceConfig(), router=router)
                result = pipeline.process_text(text_input)

            if as_json:
                click.echo(json.dumps(success_response("orchestrator:voice", {
                    "transcript": text_input,
                    "action": result.action,
                    "success": result.success,
                    "detail": result.detail,
                    "data": result.data,
                })))
            else:
                click.echo(format_voice_display(text_input, result))
        finally:
            conn.close()
        return

    # Voice mode: full orchestrator with push-to-talk
    from lattice.orchestrator.runner import OrchestratorRunner

    effective_root = project_root or str(Path.cwd())

    runner = OrchestratorRunner(
        project_root=effective_root,
        db_path=db_path,
        soul_dir=soul_dir or ".lattice/soul",
        voice_enabled=True,
    )

    try:
        asyncio.run(runner.run())
    except SystemExit as exc:
        if as_json:
            click.echo(json.dumps({"success": False, "error": str(exc)}))
        else:
            click.echo(str(exc), err=True)
        ctx.exit(1)
    except KeyboardInterrupt:
        pass


@click.command("orchestrator:text")
@click.argument("message")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--db-path", default=".lattice/orchestrator.duckdb", help="DuckDB path")
@click.pass_context
def orchestrator_text(
    ctx: click.Context,
    message: str,
    as_json: bool,
    db_path: str,
) -> None:
    """Send a one-shot text command to the orchestrator.

    Starts a minimal orchestrator (no voice, no event loop), processes the
    text command through the intent classifier and router, then exits.
    """
    from lattice.orchestrator.voice.models import VoiceConfig
    from lattice.orchestrator.voice.pipeline import VoicePipeline
    from lattice.orchestrator.voice.router import IntentRouter

    import duckdb

    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_file))

    try:
        router = IntentRouter(db_conn=conn)
        pipeline = VoicePipeline(config=VoiceConfig(), router=router)

        result = pipeline.process_text(message)

        if as_json:
            click.echo(json.dumps({
                "success": result.success,
                "action": result.action,
                "detail": result.detail,
                "data": result.data,
            }, indent=2, default=str))
        else:
            click.echo(f"Action: {result.action}")
            if result.detail:
                click.echo(f"Detail: {result.detail}")
    finally:
        conn.close()


@click.command("orchestrator:status")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--soul-dir", default=None, help="Override soul directory path")
@click.pass_context
def orchestrator_status(
    ctx: click.Context,
    as_json: bool,
    soul_dir: str | None,
) -> None:
    """Show detected CC instances and their assignments from STATE.md.

    Reads the soul ecosystem STATE.md and cross-references with live
    terminal state to produce an instance table.
    """
    from lattice.orchestrator.soul_ecosystem.reader import SoulReader
    from lattice.orchestrator.terminal.models import CCInstance

    project_root = Path.cwd()
    soul_path = Path(soul_dir) if soul_dir else project_root / ".lattice" / "soul"

    reader = SoulReader(soul_path)
    state = reader.read_state()

    # Try to detect live CC instances
    live_instances: list[CCInstance] = []
    try:
        from lattice.orchestrator.terminal.tmux import TmuxBackend
        backend = TmuxBackend()
        live_instances = asyncio.run(backend.detect_cc_panes())
    except (RuntimeError, ImportError):
        pass  # No tmux available — show STATE.md only

    # Build pane map from live detection
    pane_map: dict[str, str] = {}
    for inst in live_instances:
        pane_map[str(inst.user_number)] = inst.pane_id

    rows: list[dict[str, str]] = []

    # Instances from STATE.md
    for inst in state.instances:
        pane_id = pane_map.get(inst.instance_id, "unknown")
        rows.append({
            "instance": inst.instance_id,
            "pane": pane_id,
            "task": inst.task_description,
            "status": inst.status,
            "assigned_at": inst.assigned_at or "",
        })

    # Live instances not in STATE.md
    state_ids = {inst.instance_id for inst in state.instances}
    for inst in live_instances:
        num = str(inst.user_number)
        if num not in state_ids:
            rows.append({
                "instance": num,
                "pane": inst.pane_id,
                "task": "unassigned",
                "status": "idle",
                "assigned_at": "",
            })

    if as_json:
        click.echo(json.dumps({"instances": rows, "total": len(rows)}, indent=2, default=str))
    else:
        if not rows:
            click.echo("No instances found")
            return

        # Table header
        click.echo(
            f"{'Instance':<10} {'Pane':<10} {'Status':<10} "
            f"{'Task':<40} {'Assigned':<25}"
        )
        click.echo("-" * 95)
        for row in rows:
            task_display = row["task"][:37] + "..." if len(row["task"]) > 40 else row["task"]
            click.echo(
                f"{row['instance']:<10} {row['pane']:<10} {row['status']:<10} "
                f"{task_display:<40} {row['assigned_at']:<25}"
            )


@click.command("orchestrator:install-hooks")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--settings-path", default=None, help="Override CC settings.json path")
@click.option("--sock-path", default=None, help="Override orchestrator socket path")
@click.pass_context
def orchestrator_install_hooks(
    ctx: click.Context,
    as_json: bool,
    settings_path: str | None,
    sock_path: str | None,
) -> None:
    """Install Lattice hooks into Claude Code settings.json.

    Adds HTTP hooks for all 6 event types. Preserves existing user hooks.
    Idempotent: safe to run multiple times.
    """
    from lattice.orchestrator.hooks.installer import HookInstaller

    installer = HookInstaller(
        settings_path=Path(settings_path) if settings_path else None,
        sock_path=Path(sock_path) if sock_path else None,
    )
    result = installer.install()

    if as_json:
        click.echo(json.dumps({
            "success": result.success,
            "installed_count": result.installed_count,
            "already_present": result.already_present,
            "settings_path": result.settings_path,
            **({"error": result.error} if result.error else {}),
        }, indent=2))
    else:
        if result.success:
            click.echo(
                f"Hooks installed: {result.installed_count} new, "
                f"{result.already_present} already present"
            )
            click.echo(f"Settings: {result.settings_path}")
        else:
            click.echo(f"Error: {result.error}", err=True)
            ctx.exit(1)


@click.command("orchestrator:uninstall-hooks")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--settings-path", default=None, help="Override CC settings.json path")
@click.pass_context
def orchestrator_uninstall_hooks(
    ctx: click.Context,
    as_json: bool,
    settings_path: str | None,
) -> None:
    """Remove Lattice hooks from Claude Code settings.json.

    Only removes Lattice-managed hooks (identified by URL marker).
    User's custom hooks are left untouched.
    """
    from lattice.orchestrator.hooks.installer import HookInstaller

    installer = HookInstaller(
        settings_path=Path(settings_path) if settings_path else None,
    )
    result = installer.uninstall()

    if as_json:
        click.echo(json.dumps({
            "success": result.success,
            "removed_count": result.removed_count,
            "settings_path": result.settings_path,
            **({"error": result.error} if result.error else {}),
        }, indent=2))
    else:
        if result.success:
            click.echo(f"Hooks removed: {result.removed_count}")
            click.echo(f"Settings: {result.settings_path}")
        else:
            click.echo(f"Error: {result.error}", err=True)
            ctx.exit(1)


@click.command("orchestrator:check-hooks")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--settings-path", default=None, help="Override CC settings.json path")
@click.option("--sock-path", default=None, help="Override orchestrator socket path")
@click.pass_context
def orchestrator_check_hooks(
    ctx: click.Context,
    as_json: bool,
    settings_path: str | None,
    sock_path: str | None,
) -> None:
    """Check hook installation status and orchestrator reachability.

    Reports per-event-type status: installed, missing, or unreachable.
    """
    from lattice.orchestrator.hooks.installer import HookInstaller

    installer = HookInstaller(
        settings_path=Path(settings_path) if settings_path else None,
        sock_path=Path(sock_path) if sock_path else None,
    )
    result = installer.check()

    events_output = [
        {
            "event_type": e.event_type,
            "installed": e.installed,
            "reachable": e.reachable,
            "status": (
                "installed" if e.installed and e.reachable
                else "unreachable" if e.installed and e.reachable is False
                else "installed (socket not checked)" if e.installed and e.reachable is None
                else "missing"
            ),
        }
        for e in result.events
    ]

    if as_json:
        click.echo(json.dumps({
            "all_installed": result.all_installed,
            "events": events_output,
            "settings_path": result.settings_path,
            **({"error": result.error} if result.error else {}),
        }, indent=2))
    else:
        if result.error:
            click.echo(f"Error: {result.error}", err=True)
            ctx.exit(1)
            return

        click.echo(f"{'Event Type':<20} {'Status':<30}")
        click.echo("-" * 50)
        for evt in events_output:
            click.echo(f"{evt['event_type']:<20} {evt['status']:<30}")

        click.echo()
        if result.all_installed:
            click.echo("All hooks installed")
        else:
            missing = [e["event_type"] for e in events_output if not e["installed"]]
            click.echo(
                f"Missing hooks: {', '.join(missing)}. "
                f"Run 'lattice orchestrator:install-hooks' to fix."
            )
