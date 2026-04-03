"""Fleet dispatcher for asyncio parallel wave execution.

Dispatches all directories in a wave in parallel using asyncio.gather.
Each directory investigation is isolated — failures return AgentResult(failed=True)
without aborting the wave. Concurrency is capped via asyncio.Semaphore.

Public API:
    FleetDispatcher — the main dispatcher class
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from lattice.fleet.models import AgentResult, Wave
from lattice.fleet.prompt import PromptBuilder
from lattice.shadow.reader import parse_dir_doc
from lattice.shadow.schema import DirDoc

log = structlog.get_logger(__name__)

# Maximum LLM call retries on transient errors before marking directory as failed
_MAX_RETRIES = 3
# Backoff intervals in seconds (1s, 2s, 4s)
_BACKOFF_INTERVALS = (1.0, 2.0, 4.0)


def _extract_token_usage(response: AIMessage) -> tuple[int, int]:
    """Extract token counts from AIMessage.response_metadata with provider normalization.

    Tries Anthropic field names first (input_tokens/output_tokens), then OpenAI
    field names (prompt_tokens/completion_tokens). Returns (0, 0) if neither found.

    Args:
        response: AIMessage from model.ainvoke().

    Returns:
        Tuple of (input_tokens, output_tokens).
    """
    usage = response.response_metadata.get("usage", {})

    # Anthropic field names
    if "input_tokens" in usage:
        return usage.get("input_tokens", 0), usage.get("output_tokens", 0)

    # OpenAI field names
    if "prompt_tokens" in usage:
        return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

    return 0, 0


def _parse_agent_response(content: str, directory: str) -> tuple[DirDoc | None, list[dict]]:
    """Parse JSON response from agent into a DirDoc instance and test_stubs.

    Injects the directory and last_analyzed fields (not provided by the LLM) before
    constructing DirDoc. Returns (None, []) on JSON parse failure. Returns (None, stubs)
    when JSON is valid but DirDoc validation fails — this is a doc-not-produced outcome,
    not a dispatch failure.

    Args:
        content: Raw string content from the LLM response.
        directory: The directory being investigated (for error context and DirDoc injection).

    Returns:
        Tuple of (dir_doc, test_stubs) where dir_doc may be None on parse or validation failure.
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("agent_response_parse_failed", directory=directory, error=str(exc))
        return None, []

    test_stubs = parsed.pop("test_stubs", [])
    if not isinstance(test_stubs, list):
        test_stubs = []

    # Inject fields the LLM doesn't produce
    parsed["directory"] = directory
    parsed["last_analyzed"] = datetime.now(timezone.utc).isoformat()

    try:
        dir_doc = DirDoc(**parsed)
        return dir_doc, test_stubs
    except ValidationError as exc:
        log.warning(
            "agent_response_dirdoc_invalid",
            directory=directory,
            error=str(exc),
        )
        return None, test_stubs


class FleetDispatcher:
    """Asyncio parallel wave dispatcher for fleet execution.

    Executes all directories in a wave concurrently via asyncio.gather.
    Failed directories produce AgentResult(failed=True) without aborting the wave.
    Concurrency is capped via asyncio.Semaphore.

    Args:
        tier: Model tier string ('silver' or 'bronze').
        project_root: Project root directory for relative path computation.
        file_graph: File-level NetworkX DiGraph from load_graph_from_json().
        coverage_data: Coverage dict from _test_coverage.json (may be empty).
        agent_docs_root: Root of the shadow tree (.agent-docs/).
        checkpoint: FleetCheckpoint instance for wave progress tracking.
        concurrency_cap: Maximum concurrent LLM calls per wave (default 8).
        force: If True, re-investigate developer-protected directories (default False).
        _checkpointer: Optional DuckDBSaver for LangGraph checkpointing (injected in tests).
        _model_override: Optional mock model for testing (bypasses get_model()).
    """

    def __init__(
        self,
        tier: str,
        project_root: Path,
        file_graph: Any,
        coverage_data: dict,
        agent_docs_root: Path,
        checkpoint: Any,  # FleetCheckpoint
        concurrency_cap: int = 8,
        *,
        force: bool = False,
        _checkpointer: Any = None,
        _model_override: Any = None,
    ) -> None:
        self._tier = tier
        self._project_root = project_root
        self._file_graph = file_graph
        self._coverage_data = coverage_data
        self._agent_docs_root = agent_docs_root
        self._fleet_checkpoint = checkpoint
        self._concurrency_cap = concurrency_cap
        self._force = force
        self._semaphore = asyncio.Semaphore(concurrency_cap)
        self._prompt_builder = PromptBuilder()
        self._checkpointer = _checkpointer
        self._model_override = _model_override
        # Populated at the start of each dispatch_wave call
        self._idk_directories: set[str] = set()

    def _get_model(self):
        """Return the model to use for LLM invocation."""
        if self._model_override is not None:
            return self._model_override
        from lattice.llm.factory import get_model

        return get_model(self._tier)

    def _is_developer_protected(self, directory: str) -> bool:
        """Check if a directory is protected by a developer-authored _dir.md.

        Returns True when {agent_docs_root}/{directory}/_dir.md exists and has
        source='developer'. Returns False for missing file or non-developer source.

        Args:
            directory: Relative directory path string.

        Returns:
            True if the directory is developer-protected, False otherwise.
        """
        dir_md = self._agent_docs_root / directory / "_dir.md"
        if not dir_md.exists():
            return False
        try:
            doc = parse_dir_doc(dir_md)
            return doc.source == "developer"
        except Exception:
            return False

    def _load_idk_directories(self) -> set[str]:
        """Load IDK-flagged directories from _hints.json.

        Returns a set of directory strings where at least one entry has type='idk'.

        Returns:
            Set of directory strings with IDK entries, or empty set if no hints file.
        """
        hints_path = self._agent_docs_root / "_hints.json"
        if not hints_path.exists():
            return set()
        try:
            data = json.loads(hints_path.read_text(encoding="utf-8"))
            return {
                directory
                for directory, entries in data.items()
                if any(e.get("type") == "idk" for e in entries)
            }
        except Exception as exc:
            log.warning("idk_hints_load_failed", error=str(exc))
            return set()

    async def _run_single_investigation(
        self,
        directory: str,
        prompt_angle: str = "default",
        idk_mode: bool = False,
    ) -> AgentResult:
        """Run one LLM investigation pass for a directory.

        Does NOT acquire the semaphore — caller is responsible for concurrency control.
        Includes the retry loop with exponential backoff.

        Args:
            directory: The directory path to investigate.
            prompt_angle: Prompt angle for angle-differentiated investigation.
            idk_mode: If True, include 2-hop neighbor context in the prompt.

        Returns:
            AgentResult with investigation outcome.
        """
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                messages, _ = self._prompt_builder.build(
                    directory=directory,
                    project_root=self._project_root,
                    file_graph=self._file_graph,
                    coverage_data=self._coverage_data,
                    agent_docs_root=self._agent_docs_root,
                    idk_mode=idk_mode,
                    prompt_angle=prompt_angle,
                )

                model = self._get_model()
                response: AIMessage = await model.ainvoke(messages)

                input_tokens, output_tokens = _extract_token_usage(response)
                dir_doc, test_stubs = _parse_agent_response(
                    str(response.content), directory
                )

                log.info(
                    "directory_investigated",
                    directory=directory,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    prompt_angle=prompt_angle,
                    idk_mode=idk_mode,
                )

                return AgentResult(
                    directory=directory,
                    failed=False,
                    error=None,
                    dir_doc=dir_doc,
                    test_stubs=test_stubs,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    backoff = _BACKOFF_INTERVALS[min(attempt, len(_BACKOFF_INTERVALS) - 1)]
                    log.warning(
                        "agent_retry",
                        directory=directory,
                        attempt=attempt + 1,
                        backoff=backoff,
                        error=str(exc),
                    )
                    await asyncio.sleep(backoff)
                else:
                    log.warning(
                        "agent_failed",
                        directory=directory,
                        attempts=_MAX_RETRIES,
                        error=str(exc),
                    )

        return AgentResult(
            directory=directory,
            failed=True,
            error=str(last_exc),
            dir_doc=None,
            test_stubs=[],
            input_tokens=0,
            output_tokens=0,
        )

    async def _investigate_idk_directory(self, directory: str) -> AgentResult:
        """Run IDK double-pass investigation with angle-differentiated prompts.

        Runs two investigation passes:
          - Pass 1: prompt_angle="integration"
          - Pass 2: prompt_angle="data_flow"

        Picks the result with higher dir_doc confidence. If only one pass
        produces a dir_doc, returns that one. If neither produces a dir_doc,
        returns pass 1 result. Token counts from both passes are summed.

        Args:
            directory: The directory path to investigate.

        Returns:
            AgentResult from the higher-confidence pass.
        """
        log.info(
            "idk_mode",
            directory=directory,
            idk_mode=True,
            search_radius=2,
            passes=2,
        )

        result1 = await self._run_single_investigation(
            directory, prompt_angle="integration", idk_mode=True
        )
        result2 = await self._run_single_investigation(
            directory, prompt_angle="data_flow", idk_mode=True
        )

        # Sum token counts from both passes
        total_input = result1.input_tokens + result2.input_tokens
        total_output = result1.output_tokens + result2.output_tokens

        # Pick the result with higher confidence
        if result1.dir_doc is not None and result2.dir_doc is not None:
            winner = result1 if result1.dir_doc.confidence >= result2.dir_doc.confidence else result2
        elif result1.dir_doc is not None:
            winner = result1
        elif result2.dir_doc is not None:
            winner = result2
        else:
            winner = result1

        # Return winner with summed token counts
        return AgentResult(
            directory=winner.directory,
            failed=winner.failed,
            error=winner.error,
            dir_doc=winner.dir_doc,
            test_stubs=winner.test_stubs,
            input_tokens=total_input,
            output_tokens=total_output,
        )

    async def _investigate_directory_async(self, directory: str) -> AgentResult:
        """Investigate a single directory with developer-protection check and IDK branching.

        Developer-protected directories (source='developer' in existing _dir.md)
        are returned immediately without an LLM call unless force=True.

        IDK directories run double-pass investigation.

        Acquires the semaphore to respect concurrency cap.

        Args:
            directory: The directory path to investigate.

        Returns:
            AgentResult with investigation outcome (success or failure).
        """
        # Check developer-protected skip BEFORE semaphore acquisition
        if not self._force and self._is_developer_protected(directory):
            log.info("developer_protected_skip", directory=directory)
            return AgentResult(
                directory=directory,
                failed=False,
                error=None,
                dir_doc=None,
                test_stubs=[],
                input_tokens=0,
                output_tokens=0,
            )

        async with self._semaphore:
            # Check if IDK mode applies — compare both absolute and relative forms
            is_idk = directory in self._idk_directories
            if not is_idk:
                # Try converting absolute path to relative path for matching
                try:
                    rel = str(Path(directory).relative_to(self._project_root)).replace("\\", "/")
                    is_idk = rel in self._idk_directories
                except ValueError:
                    pass

            if is_idk:
                return await self._investigate_idk_directory(directory)

            return await self._run_single_investigation(directory)

    async def dispatch_wave(
        self,
        wave: Wave,
        run_id: str = "default-run",
    ) -> list[AgentResult]:
        """Dispatch all directories in a wave via parallel async tasks.

        Uses asyncio.gather to run all directory investigations concurrently,
        with the asyncio.Semaphore inside each task to cap actual concurrency.

        Args:
            wave: The Wave to execute.
            run_id: Stable run identifier for checkpoint tracking.

        Returns:
            List of AgentResult for ALL directories in the wave (successes + failures).
        """
        directories = list(wave.directories)

        # Load IDK directories at wave start
        self._idk_directories = self._load_idk_directories()

        log.info(
            "wave_dispatch_start",
            run_id=run_id,
            wave_index=wave.index,
            dir_count=len(directories),
        )

        # Record wave start in FleetCheckpoint
        if self._fleet_checkpoint is not None:
            self._fleet_checkpoint.record_wave_start(
                run_id=run_id,
                wave_index=wave.index,
                total_dirs=len(directories),
            )

        # Dispatch all directories concurrently; semaphore caps actual LLM concurrency
        # return_exceptions=True ensures one failed task doesn't abort others
        tasks = [self._investigate_directory_async(d) for d in directories]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert any unexpected exceptions to failed AgentResult entries
        results: list[AgentResult] = []
        for directory, outcome in zip(directories, raw_results):
            if isinstance(outcome, BaseException):
                log.warning(
                    "agent_unexpected_failure",
                    directory=directory,
                    error=str(outcome),
                )
                results.append(
                    AgentResult(
                        directory=directory,
                        failed=True,
                        error=str(outcome),
                        dir_doc=None,
                        test_stubs=[],
                        input_tokens=0,
                        output_tokens=0,
                    )
                )
            else:
                results.append(outcome)

        # Record wave completion
        if self._fleet_checkpoint is not None:
            failed_dirs = sum(1 for r in results if r.failed)
            completed_dirs = len(results) - failed_dirs
            self._fleet_checkpoint.record_wave_complete(
                run_id=run_id,
                wave_index=wave.index,
                completed_dirs=completed_dirs,
                failed_dirs=failed_dirs,
            )

        log.info(
            "wave_dispatch_complete",
            run_id=run_id,
            wave_index=wave.index,
            total=len(results),
            failed=sum(1 for r in results if r.failed),
        )

        return list(results)
