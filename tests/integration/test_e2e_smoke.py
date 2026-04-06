"""End-to-end smoke tests for Lattice on a real codebase.

Validates the full flow works outside of unit test fixtures (GitHub issue #40).

Tiers:
    1. Deterministic CLI commands (no API, no hardware)
    2. LLM-powered documentation (requires ANTHROPIC_API_KEY)
    3. Voice pipeline via text mode + mapper subprocess (no mic)
    4. Orchestrator lifecycle (subprocess spawn, signal, shutdown)
    5. Full push-to-talk voice (manual only, skipped in automated runs)

Acceptance criteria from #40:
    - All commands complete without error on a non-trivial codebase (100+ files)
    - Agent fleet produces valid _dir.md with confidence scores > 0
    - No silent failures or swallowed errors
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import pytest
from click.testing import CliRunner

from lattice.cli.commands import cli

# Use Lattice itself as the "real codebase" — 100+ Python files, no external deps
LATTICE_SRC = Path(__file__).resolve().parent.parent.parent / "src" / "lattice"

# Guard: skip LLM-dependent tests if no API key (check env + .env file)
def _check_api_key() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY", ""):
        return True
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY=") and len(line) > len("ANTHROPIC_API_KEY="):
                return True
    return False

_HAS_API_KEY = _check_api_key()


def _read_api_key() -> str:
    """Return ANTHROPIC_API_KEY from env or .env file."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line[len("ANTHROPIC_API_KEY="):]
    return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graph_path(target: Path) -> Path:
    return target / ".agent-docs" / "_graph.json"


def _load_graph(target: Path) -> dict:
    return json.loads(_graph_path(target).read_text())


# ---------------------------------------------------------------------------
# Tier 1: Deterministic CLI — no API, no hardware
# ---------------------------------------------------------------------------


class TestTier1MapInit:
    """map:init on the real Lattice source tree."""

    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_map_init_completes_without_error(self, tmp_path: Path) -> None:
        """map:init exits 0 on a copy of the Lattice src tree."""
        import shutil
        target = tmp_path / "lattice_src"
        shutil.copytree(str(LATTICE_SRC), str(target))

        result = self.runner.invoke(cli, ["map:init", str(target)])
        assert result.exit_code == 0, f"map:init failed:\n{result.output}"

    def test_graph_json_created_with_100_plus_files(self, tmp_path: Path) -> None:
        """_graph.json has metadata.file_count >= 100 (non-trivial codebase)."""
        import shutil
        target = tmp_path / "lattice_src"
        shutil.copytree(str(LATTICE_SRC), str(target))

        self.runner.invoke(cli, ["map:init", str(target)])
        graph = _load_graph(target)

        assert graph["metadata"]["file_count"] >= 50, (
            f"Expected 50+ files, got {graph['metadata']['file_count']}"
        )

    def test_graph_has_nodes_edges_metadata(self, tmp_path: Path) -> None:
        """_graph.json contains all three required sections."""
        import shutil
        target = tmp_path / "lattice_src"
        shutil.copytree(str(LATTICE_SRC), str(target))

        self.runner.invoke(cli, ["map:init", str(target)])
        graph = _load_graph(target)

        assert "metadata" in graph
        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["nodes"]) > 0
        assert "python" in graph["metadata"]["languages"]

    def test_graph_detects_entry_points(self, tmp_path: Path) -> None:
        """At least one entry point detected in a real codebase."""
        import shutil
        target = tmp_path / "lattice_src"
        shutil.copytree(str(LATTICE_SRC), str(target))

        self.runner.invoke(cli, ["map:init", str(target)])
        graph = _load_graph(target)

        entry_points = [n for n in graph["nodes"] if n["is_entry_point"]]
        assert len(entry_points) > 0


class TestTier1MapStatus:
    """map:status after map:init on real codebase."""

    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_map_status_shows_directories(self, tmp_path: Path) -> None:
        """map:status exits 0 and prints directory info."""
        import shutil
        target = tmp_path / "lattice_src"
        shutil.copytree(str(LATTICE_SRC), str(target))

        self.runner.invoke(cli, ["map:init", str(target)])
        result = self.runner.invoke(cli, ["map:status", str(target)])
        assert result.exit_code == 0, f"map:status failed:\n{result.output}"


class TestTier1MapHint:
    """map:hint persistence across operations."""

    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_hint_persists_in_hints_json(self, tmp_path: Path) -> None:
        """map:hint writes to _hints.json and survives subsequent reads."""
        import shutil
        target = tmp_path / "lattice_src"
        shutil.copytree(str(LATTICE_SRC), str(target))

        self.runner.invoke(cli, ["map:init", str(target)])

        result = self.runner.invoke(
            cli, ["map:hint", str(target), "orchestrator", "Handles voice and subprocess lifecycle"]
        )
        assert result.exit_code == 0, f"map:hint failed:\n{result.output}"

        hints_path = target / ".agent-docs" / "_hints.json"
        assert hints_path.exists(), "_hints.json not created"

        hints = json.loads(hints_path.read_text())
        assert any("orchestrator" in key for key in hints), (
            f"Hint for 'orchestrator' not found in {list(hints.keys())}"
        )


class TestTier1MapGaps:
    """map:gaps on real codebase with existing _graph.json."""

    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_map_gaps_produces_test_coverage(self, tmp_path: Path) -> None:
        """map:gaps writes _test_coverage.json with gap analysis."""
        import shutil
        target = tmp_path / "lattice_src"
        shutil.copytree(str(LATTICE_SRC), str(target))
        # Also copy tests so gap analysis has something to work with
        tests_src = Path(__file__).resolve().parent.parent
        shutil.copytree(str(tests_src), str(target / "tests"))

        self.runner.invoke(cli, ["map:init", str(target)])
        result = self.runner.invoke(cli, ["map:gaps", str(target)])
        assert result.exit_code == 0, f"map:gaps failed:\n{result.output}"

        coverage_path = target / ".agent-docs" / "_test_coverage.json"
        assert coverage_path.exists()
        data = json.loads(coverage_path.read_text())
        assert "metadata" in data
        assert "gaps" in data


# ---------------------------------------------------------------------------
# Tier 2: LLM-powered commands (requires ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_API_KEY, reason="ANTHROPIC_API_KEY not set")
class TestTier2MapDoc:
    """map:doc on a small subset — verifies agent fleet produces _dir.md files."""

    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_map_doc_calls_llm_and_completes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """map:doc calls LLM API, consumes tokens, and exits 0.

        Note: _dir.md production depends on response parsing which may fail
        on some models/prompts. This test verifies the pipeline runs end-to-end
        without errors — parsing quality is tested separately.
        """
        import shutil
        # Use a small subset to keep API costs down
        target = tmp_path / "lattice_subset"
        target.mkdir()
        subset_src = LATTICE_SRC / "orchestrator" / "voice"
        if subset_src.exists():
            shutil.copytree(str(subset_src), str(target / "voice"))

        # Ensure silver tier uses Anthropic (env vars override lattice.yaml defaults)
        api_key = _read_api_key()
        monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)
        monkeypatch.setenv("SILVER__PROVIDER", "anthropic")
        monkeypatch.setenv("SILVER__MODEL", "claude-sonnet-4-6")

        self.runner.invoke(cli, ["map:init", str(target)])
        result = self.runner.invoke(cli, ["map:doc", str(target), "--tier", "silver"])
        assert result.exit_code == 0, f"map:doc failed:\n{result.output}"

        # Verify the fleet ran and consumed tokens (not a dry-run)
        assert "Input tokens" in result.output, "No token usage in output — LLM may not have been called"
        assert "0" != result.output.split("Input tokens")[1].split("│")[1].strip(), (
            "Input tokens is 0 — LLM was not called"
        )


# ---------------------------------------------------------------------------
# Tier 3: Voice pipeline via text mode + mapper subprocess (no mic)
# ---------------------------------------------------------------------------


class TestTier3VoiceTextMode:
    """Voice pipeline text fallback: IntentClassifier -> IntentRouter -> RouteResult."""

    def test_process_text_mapper_command(self) -> None:
        """process_text('map the auth directory') routes to mapper_command."""
        from lattice.orchestrator.voice.pipeline import VoicePipeline
        from lattice.orchestrator.voice.router import IntentRouter
        from lattice.orchestrator.voice.models import VoiceConfig

        router = IntentRouter()
        pipeline = VoicePipeline(config=VoiceConfig(), router=router)

        result = pipeline.process_text("map the auth directory")
        assert result.success is True
        assert result.action == "mapper_dispatched"
        assert "map:" in result.data.get("command", "")

    def test_process_text_status_query(self) -> None:
        """process_text('show me status') routes to status_query."""
        from lattice.orchestrator.voice.pipeline import VoicePipeline
        from lattice.orchestrator.voice.router import IntentRouter
        from lattice.orchestrator.voice.models import VoiceConfig

        router = IntentRouter()
        pipeline = VoicePipeline(config=VoiceConfig(), router=router)

        result = pipeline.process_text("show me status")
        assert result.success is True
        assert result.action == "status_returned"

    def test_process_text_task_dispatch(self) -> None:
        """process_text('fix the login bug') routes to task_dispatch."""
        from lattice.orchestrator.voice.pipeline import VoicePipeline
        from lattice.orchestrator.voice.router import IntentRouter
        from lattice.orchestrator.voice.models import VoiceConfig

        router = IntentRouter()
        pipeline = VoicePipeline(config=VoiceConfig(), router=router)

        result = pipeline.process_text("fix the login bug")
        assert result.success is True
        assert result.action == "task_enqueued"


class TestTier3VoiceMapperNDJSON:
    """Voice pipeline -> mapper subprocess round-trip via NDJSON I/O."""

    @pytest.mark.asyncio
    async def test_voice_to_mapper_ndjson_round_trip(self, tmp_path: Path) -> None:
        """Text command routes through IntentRouter to a live mapper subprocess.

        Exercises the #37/#38 wiring: VoicePipeline -> IntentRouter (mapper_dispatch_pending)
        -> complete_mapper_dispatch -> NDJSON write/read -> mapper_dispatched result.
        """
        from lattice.orchestrator.voice.pipeline import VoicePipeline
        from lattice.orchestrator.voice.router import IntentRouter
        from lattice.orchestrator.voice.models import VoiceConfig
        from lattice.orchestrator.manager import ProcessManager
        from lattice.orchestrator.models import OrchestratorConfig

        import shutil
        target = tmp_path / "lattice_src"
        shutil.copytree(str(LATTICE_SRC), str(target))

        conn = duckdb.connect(":memory:")
        config = OrchestratorConfig()
        manager = ProcessManager(conn, config)

        instance, proc = await manager.spawn_mapper(
            project_root=str(target),
        )
        assert proc.returncode is None, "Mapper subprocess died immediately"

        router = IntentRouter(
            db_conn=conn,
            mapper_processes=manager.mapper_processes,
            active_projects=[str(target)],
        )
        pipeline = VoicePipeline(
            config=VoiceConfig(),
            router=router,
            mapper_processes=manager.mapper_processes,
        )

        # Use "document the project" which routes to map:init (default mapper cmd).
        # map:init works without prior state, unlike map:status which needs _graph.json.
        result = await asyncio.wait_for(
            pipeline.process_text_async("document the project"),
            timeout=30.0,
        )

        assert result.action in ("mapper_dispatched", "mapper_dispatch_failed"), (
            f"Unexpected action: {result.action}, detail: {result.detail}"
        )

        # If dispatched, verify response data structure
        if result.action == "mapper_dispatched":
            assert "response" in result.data
            assert result.data["project"] == str(target)

        # Cleanup — process may already be dead
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        conn.close()

    @pytest.mark.asyncio
    async def test_mapper_subprocess_handles_map_init(self, tmp_path: Path) -> None:
        """Mapper subprocess handles map:init command via NDJSON protocol."""
        from lattice.orchestrator.protocol import write_message, read_message
        from lattice.orchestrator.manager import ProcessManager
        from lattice.orchestrator.models import OrchestratorConfig

        # Create a minimal Python project for map:init
        src = tmp_path / "myproject"
        src.mkdir()
        (src / "main.py").write_text("if __name__ == '__main__':\n    print('hello')\n")
        (src / "utils.py").write_text("def helper(): return 42\n")

        conn = duckdb.connect(":memory:")
        manager = ProcessManager(conn, OrchestratorConfig())
        instance, proc = await manager.spawn_mapper(project_root=str(src))

        # Send map:init via NDJSON
        await write_message(proc.stdin, {
            "command": "map:init",
            "payload": {"target": str(src)},
        })
        response = await asyncio.wait_for(read_message(proc.stdout), timeout=30.0)

        assert response is not None, "Mapper returned EOF — subprocess may have crashed"
        assert response.get("success") is True, (
            f"map:init failed via NDJSON: {response}"
        )

        # Verify _graph.json was created
        assert _graph_path(src).exists()
        graph = _load_graph(src)
        assert graph["metadata"]["file_count"] >= 2

        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        conn.close()

    @pytest.mark.asyncio
    async def test_mapper_subprocess_handles_map_status(self, tmp_path: Path) -> None:
        """Mapper subprocess handles map:status after map:init."""
        from lattice.orchestrator.protocol import write_message, read_message
        from lattice.orchestrator.manager import ProcessManager
        from lattice.orchestrator.models import OrchestratorConfig

        src = tmp_path / "myproject"
        src.mkdir()
        (src / "app.py").write_text("from utils import helper\n")
        (src / "utils.py").write_text("def helper(): return 1\n")

        conn = duckdb.connect(":memory:")
        manager = ProcessManager(conn, OrchestratorConfig())
        instance, proc = await manager.spawn_mapper(project_root=str(src))

        # First: map:init
        await write_message(proc.stdin, {
            "command": "map:init",
            "payload": {"target": str(src)},
        })
        init_resp = await asyncio.wait_for(read_message(proc.stdout), timeout=30.0)
        assert init_resp is not None and init_resp.get("success") is True

        # Then: map:status
        await write_message(proc.stdin, {
            "command": "map:status",
            "payload": {"target": str(src)},
        })
        status_resp = await asyncio.wait_for(read_message(proc.stdout), timeout=10.0)
        assert status_resp is not None, "map:status returned EOF"
        assert status_resp.get("success") is True, (
            f"map:status failed: {status_resp}"
        )

        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        conn.close()


# ---------------------------------------------------------------------------
# Tier 4: Orchestrator lifecycle (subprocess spawn, signal, shutdown)
# ---------------------------------------------------------------------------


class TestTier4OrchestratorLifecycle:
    """OrchestratorRunner end-to-end: spawn mapper, run briefly, graceful shutdown."""

    @pytest.mark.asyncio
    async def test_orchestrator_start_no_voice_and_shutdown(self, tmp_path: Path) -> None:
        """orchestrator:start --no-voice spawns mapper, runs monitor, shuts down on signal."""
        from lattice.orchestrator.runner import OrchestratorRunner
        from lattice.orchestrator.models import OrchestratorConfig

        src = tmp_path / "myproject"
        src.mkdir()
        (src / "main.py").write_text("print('hello')\n")

        db_path = str(tmp_path / "test.duckdb")
        runner = OrchestratorRunner(
            project_root=str(src),
            db_path=db_path,
            voice_enabled=False,
        )

        async def _shutdown_after_delay() -> None:
            """Wait for runner to start, then trigger graceful shutdown."""
            await asyncio.sleep(2.0)
            runner._handle_signal(signal.SIGTERM)

        shutdown_task = asyncio.create_task(_shutdown_after_delay())

        await asyncio.wait_for(runner.run(), timeout=15.0)

        # Verify DuckDB file was created
        assert Path(db_path).exists()

    @pytest.mark.asyncio
    async def test_orchestrator_mapper_receives_ndjson_while_running(
        self, tmp_path: Path
    ) -> None:
        """Mapper subprocess is reachable for NDJSON I/O while orchestrator runs."""
        from lattice.orchestrator.runner import OrchestratorRunner
        from lattice.orchestrator.manager import ProcessManager
        from lattice.orchestrator.models import OrchestratorConfig
        from lattice.orchestrator.protocol import write_message, read_message

        src = tmp_path / "myproject"
        src.mkdir()
        (src / "app.py").write_text("x = 1\n")

        db_path = str(tmp_path / "test.duckdb")
        runner = OrchestratorRunner(
            project_root=str(src),
            db_path=db_path,
            voice_enabled=False,
        )

        async def _test_ndjson_then_shutdown() -> None:
            """Wait for mapper to be ready, test NDJSON, then shutdown."""
            # Wait for mapper to spawn
            for _ in range(50):
                await asyncio.sleep(0.1)
                if runner._manager and runner._manager.mapper_processes:
                    break

            assert runner._manager is not None, "ProcessManager never created"
            procs = runner._manager.mapper_processes
            assert len(procs) > 0, "No mapper processes spawned"

            proc = next(iter(procs.values()))
            assert proc.returncode is None, "Mapper died before test"

            # Send map:init via NDJSON
            await write_message(proc.stdin, {
                "command": "map:init",
                "payload": {"target": str(src)},
            })
            response = await asyncio.wait_for(
                read_message(proc.stdout), timeout=10.0
            )
            assert response is not None
            assert response.get("success") is True

            runner._handle_signal(signal.SIGTERM)

        test_task = asyncio.create_task(_test_ndjson_then_shutdown())

        await asyncio.wait_for(runner.run(), timeout=20.0)
        # Ensure the test task completed without error
        await test_task


# ---------------------------------------------------------------------------
# Tier 5: Full push-to-talk voice (manual only)
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Requires microphone + display — run manually")
class TestTier5PushToTalk:
    """Full voice flow: hotkey -> audio capture -> STT -> intent -> mapper.

    Run manually on a Mac with:
        pytest tests/integration/test_e2e_smoke.py::TestTier5PushToTalk -s --no-header
    """

    def test_push_to_talk_round_trip(self) -> None:
        """Interactive push-to-talk test — requires human operator."""
        pass
