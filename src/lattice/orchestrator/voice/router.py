"""Intent router: dispatches classified intents to orchestrator infrastructure.

Routes IntentResult objects to the appropriate orchestrator subsystem:
    cc_command             -> orchestrator agent (send message to CC instance)
    cc_approve             -> orchestrator agent (approve tool use)
    cc_deny                -> orchestrator agent (deny tool use)
    cc_deny_redirect       -> orchestrator agent (deny + redirect message)
    cc_status              -> orchestrator agent (query instance status)
    cc_interrupt           -> orchestrator agent (interrupt CC instance)
    orchestrator_freeform  -> orchestrator agent (LLM interpretation)
    task_dispatch          -> TaskQueue.enqueue
    status_query           -> get_instance_status / get_all_instance_status
    mapper_command         -> CLI command details (caller invokes lattice CLI)
    context_injection      -> write_message / ContextManager
    external_fetch         -> ConnectorRegistry.fetch (async, returns pending RouteResult)
    unrecognized           -> echo transcript with closest-match suggestion

Multi-project guard: if multiple active_projects are configured and a
task_dispatch intent does not specify which project (via extracted["project"]),
returns confirmation_required instead of enqueuing.

All CC intents (cc_*) and orchestrator_freeform are dispatched to the
orchestrator agent rather than directly to tmux. Each RouteResult includes
a voice_request_id for end-to-end tracing.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from pydantic import BaseModel

from lattice.orchestrator.status import get_all_instance_status, get_instance_status
from lattice.orchestrator.voice.models import IntentResult

try:
    import duckdb as _duckdb
    DuckDBConnection = _duckdb.DuckDBPyConnection
except ImportError:  # pragma: no cover
    DuckDBConnection = Any  # type: ignore[assignment,misc]

try:
    from lattice.orchestrator.queue import TaskQueue as _TaskQueue
except ImportError:  # pragma: no cover
    _TaskQueue = Any  # type: ignore[assignment,misc]

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# RouteResult model
# ---------------------------------------------------------------------------


class RouteResult(BaseModel):
    """Result of routing a classified intent to an orchestrator subsystem.

    Frozen — use model_copy(update=...) to create modified instances.

    Fields:
        success: True if routing succeeded and the action was taken.
        action: Machine-readable action identifier, e.g. "task_enqueued",
                "status_returned", "mapper_dispatched", "context_injected",
                "cc_command_dispatched", "orchestrator_freeform_dispatched",
                "confirmation_required", "unrecognized", "empty_transcript".
        detail: Human-readable summary of what happened.
        data: Structured payload for downstream use (e.g. task_id, instances,
              voice_request_id for tracing).
    """

    model_config = {"frozen": True}

    success: bool
    action: str
    detail: str = ""
    data: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Keyword lists for closest-match suggestion
# ---------------------------------------------------------------------------

_STATUS_KEYWORDS = frozenset(["status", "show", "progress", "running", "utilization", "instances"])
_MAP_KEYWORDS = frozenset(["map", "document", "analyze", "mapper"])
_START_KEYWORDS = frozenset(["start", "fix", "implement", "work", "begin"])
_CONTEXT_KEYWORDS = frozenset(["tell", "add", "inject", "context", "note", "inform"])
_FETCH_KEYWORDS = frozenset(["look", "search", "fetch", "find", "check", "github", "mattermost"])


# ---------------------------------------------------------------------------
# IntentRouter
# ---------------------------------------------------------------------------


class IntentRouter:
    """Routes IntentResult objects to orchestrator subsystems.

    All dependencies are optional. When a dependency is absent, routing
    returns a successful result with a "dry_run" marker (task_dispatch)
    or empty data (status_query).

    CC instance intents (cc_command, cc_approve, cc_deny, cc_deny_redirect,
    cc_status, cc_interrupt) and orchestrator_freeform are routed to the
    orchestrator agent for LLM-powered processing. Each RouteResult includes
    a voice_request_id for end-to-end tracing.

    Args:
        task_queue: Optional TaskQueue instance for task_dispatch routing.
        db_conn: Optional DuckDB connection for status_query routing.
        active_projects: Optional list of active project names for multi-project
            confirmation guard on ambiguous task_dispatch intents.
        connector_registry: Optional ConnectorRegistry for external_fetch routing.
        mapper_processes: Optional dict of project -> asyncio.subprocess.Process.
        orchestrator_agent: Optional orchestrator agent for CC intent dispatch.
    """

    def __init__(
        self,
        task_queue: Any | None = None,
        db_conn: Any | None = None,
        active_projects: list[str] | None = None,
        connector_registry: Any | None = None,
        mapper_processes: dict[str, Any] | None = None,
        orchestrator_agent: Any | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._db_conn = db_conn
        self._active_projects = active_projects or []
        self._connector_registry = connector_registry
        # mapper_processes: keyed by project_root, value is asyncio.subprocess.Process
        self._mapper_processes: dict[str, Any] = mapper_processes or {}
        self._orchestrator_agent = orchestrator_agent

    def dispatch(self, intent: IntentResult) -> RouteResult:
        """Route a classified intent to the appropriate orchestrator subsystem.

        CC intents are dispatched to the orchestrator agent. General intents
        are dispatched to their respective subsystems.

        Args:
            intent: Classified IntentResult from IntentClassifier.

        Returns:
            RouteResult describing the action taken, always including
            voice_request_id in data for tracing.
        """
        # CC instance control intents → orchestrator agent
        cc_dispatch_map = {
            "cc_command": self._dispatch_cc_command,
            "cc_approve": self._dispatch_cc_approve,
            "cc_deny": self._dispatch_cc_deny,
            "cc_deny_redirect": self._dispatch_cc_deny_redirect,
            "cc_status": self._dispatch_cc_status,
            "cc_interrupt": self._dispatch_cc_interrupt,
            "orchestrator_freeform": self._dispatch_orchestrator_freeform,
        }

        handler = cc_dispatch_map.get(intent.category)
        if handler is not None:
            return handler(intent)

        # General intents
        if intent.category == "task_dispatch":
            return self._dispatch_task(intent)
        elif intent.category == "status_query":
            return self._dispatch_status(intent)
        elif intent.category == "mapper_command":
            return self._dispatch_mapper(intent)
        elif intent.category == "context_injection":
            return self._dispatch_context(intent)
        elif intent.category == "external_fetch":
            return self._dispatch_external_fetch(intent)
        else:
            return self._dispatch_unrecognized(intent)

    # ------------------------------------------------------------------
    # CC instance control dispatch methods
    # ------------------------------------------------------------------

    def _make_voice_request_id(self) -> str:
        """Generate a unique voice_request_id for tracing."""
        return str(uuid.uuid4())

    def _dispatch_cc_command(self, intent: IntentResult) -> RouteResult:
        """Route cc_command intent to orchestrator agent.

        Sends a message to a specific CC instance via the orchestrator.
        """
        instance = intent.extracted.get("instance", "")
        message = intent.extracted.get("message", intent.transcript)
        request_id = self._make_voice_request_id()

        log.info(
            "voice_cc_command",
            instance=instance,
            message=message,
            voice_request_id=request_id,
        )

        return RouteResult(
            success=True,
            action="cc_command_dispatched",
            detail=f"Send to instance {instance}: {message}",
            data={
                "intent": "cc_command",
                "instance": instance,
                "message": message,
                "voice_request_id": request_id,
            },
        )

    def _dispatch_cc_approve(self, intent: IntentResult) -> RouteResult:
        """Route cc_approve intent to orchestrator agent.

        Approves a pending tool-use request for a CC instance.
        """
        instance = intent.extracted.get("instance", "")
        request_id = self._make_voice_request_id()

        log.info(
            "voice_cc_approve",
            instance=instance,
            voice_request_id=request_id,
        )

        return RouteResult(
            success=True,
            action="cc_approve_dispatched",
            detail=f"Approve instance {instance}",
            data={
                "intent": "cc_approve",
                "instance": instance,
                "voice_request_id": request_id,
            },
        )

    def _dispatch_cc_deny(self, intent: IntentResult) -> RouteResult:
        """Route cc_deny intent to orchestrator agent.

        Denies a pending tool-use request for a CC instance.
        """
        instance = intent.extracted.get("instance", "")
        request_id = self._make_voice_request_id()

        log.info(
            "voice_cc_deny",
            instance=instance,
            voice_request_id=request_id,
        )

        return RouteResult(
            success=True,
            action="cc_deny_dispatched",
            detail=f"Deny instance {instance}",
            data={
                "intent": "cc_deny",
                "instance": instance,
                "voice_request_id": request_id,
            },
        )

    def _dispatch_cc_deny_redirect(self, intent: IntentResult) -> RouteResult:
        """Route cc_deny_redirect intent to orchestrator agent.

        Denies a pending request and provides a redirect message.
        """
        instance = intent.extracted.get("instance", "")
        message = intent.extracted.get("message", "")
        request_id = self._make_voice_request_id()

        log.info(
            "voice_cc_deny_redirect",
            instance=instance,
            message=message,
            voice_request_id=request_id,
        )

        return RouteResult(
            success=True,
            action="cc_deny_redirect_dispatched",
            detail=f"Deny instance {instance}, redirect: {message}",
            data={
                "intent": "cc_deny_redirect",
                "instance": instance,
                "message": message,
                "voice_request_id": request_id,
            },
        )

    def _dispatch_cc_status(self, intent: IntentResult) -> RouteResult:
        """Route cc_status intent to orchestrator agent.

        Queries the status of a specific CC instance.
        """
        instance = intent.extracted.get("instance", "")
        request_id = self._make_voice_request_id()

        log.info(
            "voice_cc_status",
            instance=instance,
            voice_request_id=request_id,
        )

        return RouteResult(
            success=True,
            action="cc_status_dispatched",
            detail=f"Status query for instance {instance}",
            data={
                "intent": "cc_status",
                "instance": instance,
                "voice_request_id": request_id,
            },
        )

    def _dispatch_cc_interrupt(self, intent: IntentResult) -> RouteResult:
        """Route cc_interrupt intent to orchestrator agent.

        Sends Ctrl+C to a specific CC instance.
        """
        instance = intent.extracted.get("instance", "")
        request_id = self._make_voice_request_id()

        log.info(
            "voice_cc_interrupt",
            instance=instance,
            voice_request_id=request_id,
        )

        return RouteResult(
            success=True,
            action="cc_interrupt_dispatched",
            detail=f"Interrupt instance {instance}",
            data={
                "intent": "cc_interrupt",
                "instance": instance,
                "voice_request_id": request_id,
            },
        )

    def _dispatch_orchestrator_freeform(self, intent: IntentResult) -> RouteResult:
        """Route orchestrator_freeform intent to orchestrator agent.

        Sends the full transcript to the orchestrator LLM for interpretation.
        Replaces the old "unrecognized" fallback — nothing is truly unrecognized,
        just requires LLM reasoning.
        """
        message = intent.extracted.get("message", intent.transcript)
        request_id = self._make_voice_request_id()

        log.info(
            "voice_orchestrator_freeform",
            message=message,
            voice_request_id=request_id,
        )

        return RouteResult(
            success=True,
            action="orchestrator_freeform_dispatched",
            detail=f"Freeform to orchestrator: {message}",
            data={
                "intent": "orchestrator_freeform",
                "message": message,
                "voice_request_id": request_id,
            },
        )

    # ------------------------------------------------------------------
    # Private routing methods
    # ------------------------------------------------------------------

    def _dispatch_task(self, intent: IntentResult) -> RouteResult:
        """Route task_dispatch intent to TaskQueue."""
        # Multi-project confirmation guard
        if len(self._active_projects) > 1 and "project" not in intent.extracted:
            return RouteResult(
                success=False,
                action="confirmation_required",
                detail=(
                    f"Multiple projects active: {', '.join(self._active_projects)}. "
                    "Which project?"
                ),
                data={"projects": list(self._active_projects)},
            )

        payload = json.dumps({
            "type": "task",
            "description": intent.transcript,
            **intent.extracted,
        })

        task_id = "dry_run"
        if self._task_queue is not None:
            record = self._task_queue.enqueue(payload, priority="normal")
            task_id = record.task_id

        log.info("voice_task_dispatched", transcript=intent.transcript, task_id=task_id)
        return RouteResult(
            success=True,
            action="task_enqueued",
            detail=f"Task queued: {intent.transcript}",
            data={"task_id": task_id},
        )

    def _dispatch_status(self, intent: IntentResult) -> RouteResult:
        """Route status_query intent to get_instance_status or get_all_instance_status."""
        if self._db_conn is None:
            return RouteResult(
                success=True,
                action="status_returned",
                detail="No database connection",
                data={"instances": []},
            )

        instance_id = intent.extracted.get("instance_id")
        if instance_id:
            status = get_instance_status(self._db_conn, instance_id)
            rows: list[dict[str, Any]] = [status]
        else:
            rows = get_all_instance_status(self._db_conn)

        return RouteResult(
            success=True,
            action="status_returned",
            detail=f"{len(rows)} instance(s) found",
            data={"instances": rows},
        )

    def _dispatch_mapper(self, intent: IntentResult) -> RouteResult:
        """Route mapper_command intent to Mapper subprocess or CLI fallback.

        If a live mapper subprocess is available for the resolved project,
        returns mapper_dispatch_pending with metadata so the caller
        (VoicePipeline.complete_mapper_dispatch) can perform async NDJSON I/O.

        This method remains synchronous — async I/O is deferred to the caller
        to preserve the existing dispatch() contract.

        Falls back to mapper_dispatched (pre-Phase 14 CLI details) when no
        subprocess is available.
        """
        target = intent.extracted.get("target", ".")
        transcript_lower = intent.transcript.lower()

        # Determine subcommand from transcript keywords
        if "init" in transcript_lower:
            cmd = "map:init"
        elif "status" in transcript_lower:
            cmd = "map:status"
        elif "gap" in transcript_lower:
            cmd = "map:gaps"
        elif "doc" in transcript_lower:
            cmd = "map:doc"
        else:
            cmd = "map:init"  # default

        # Resolve project for mapper dispatch
        project = intent.extracted.get("project")
        if not project and len(self._active_projects) > 1:
            return RouteResult(
                success=False,
                action="confirmation_required",
                detail=(
                    f"Multiple projects active: {', '.join(self._active_projects)}. "
                    f"Which project for {cmd}?"
                ),
                data={
                    "projects": list(self._active_projects),
                    "command": cmd,
                    "target": target,
                },
            )
        if not project and len(self._active_projects) == 1:
            project = self._active_projects[0]

        # If a live mapper subprocess is available, signal pending NDJSON dispatch
        mapper_proc = self._mapper_processes.get(project) if project else None
        if mapper_proc is not None and mapper_proc.returncode is None:
            log.info(
                "voice_mapper_ndjson_dispatch",
                command=cmd,
                target=target,
                project=project,
            )
            return RouteResult(
                success=True,
                action="mapper_dispatch_pending",
                detail=f"{cmd} {target} -> project {project}",
                data={"command": cmd, "target": target, "project": project},
            )

        # Fallback: no subprocess available — return CLI details (pre-Phase 14)
        log.info("voice_mapper_dispatched", command=cmd, target=target)
        return RouteResult(
            success=True,
            action="mapper_dispatched",
            detail=f"{cmd} {target}",
            data={"command": cmd, "target": target},
        )

    def _dispatch_context(self, intent: IntentResult) -> RouteResult:
        """Route context_injection intent."""
        content = intent.transcript
        instance_id = intent.extracted.get("instance_id")

        log.info(
            "voice_context_injected",
            instance_id=instance_id or "broadcast",
            content_length=len(content),
        )
        return RouteResult(
            success=True,
            action="context_injected",
            detail=f"Context: {content}",
            data={
                "instance_id": instance_id or "broadcast",
                "content": content,
            },
        )

    def _dispatch_external_fetch(self, intent: IntentResult) -> RouteResult:
        """Route external_fetch intent to ConnectorRegistry or return unavailable.

        Does NOT perform the async fetch itself — returns a pending RouteResult
        with the connector name and query so the caller (VoicePipeline or CLI)
        can perform the async fetch at an appropriate time.

        Connector routing heuristic (keyword-based):
        - 'github', 'issues', 'prs', 'ci', 'pull request' → "github"
        - 'mattermost', 'slack', 'channel' → "mattermost"
        - anything else → "tavily" (web search default)
        """
        if self._connector_registry is None:
            return RouteResult(
                success=False,
                action="external_fetch_unavailable",
                detail="No connector registry configured",
            )

        query = intent.extracted.get("query", intent.transcript)
        transcript_lower = intent.transcript.lower()

        # Determine target connector from transcript keywords
        if any(kw in transcript_lower for kw in ("github", "issues", "prs", "ci ", "pull request")):
            connector_name = "github"
        elif any(kw in transcript_lower for kw in ("mattermost", "slack", "channel")):
            connector_name = "mattermost"
        else:
            connector_name = "tavily"

        log.info(
            "voice_external_fetch_pending",
            connector=connector_name,
            query=query,
        )

        return RouteResult(
            success=True,
            action="external_fetch_pending",
            detail=f"Fetching from {connector_name}: {query}",
            data={"connector": connector_name, "query": query},
        )

    def _dispatch_unrecognized(self, intent: IntentResult) -> RouteResult:
        """Route unrecognized intent with transcript echo and suggestion."""
        suggestion = self._suggest_closest(intent.transcript)
        return RouteResult(
            success=False,
            action="unrecognized",
            detail=f'Unrecognized: "{intent.transcript}". Did you mean: {suggestion}?',
            data={
                "transcript": intent.transcript,
                "suggestion": suggestion,
            },
        )

    def _suggest_closest(self, transcript: str) -> str:
        """Return a closest-match command suggestion based on keyword overlap.

        Uses simple keyword counting across 4 intent categories.
        Returns the category with the most keyword matches, or a generic
        fallback if no keywords match.

        Args:
            transcript: The unrecognized utterance.

        Returns:
            A short suggestion string like "try 'status ...'" or "try 'map ...'".
        """
        words = set(transcript.lower().split())

        scores = {
            "status": len(words & _STATUS_KEYWORDS),
            "map": len(words & _MAP_KEYWORDS),
            "start": len(words & _START_KEYWORDS),
            "tell": len(words & _CONTEXT_KEYWORDS),
            "fetch": len(words & _FETCH_KEYWORDS),
        }

        best_cmd, best_score = max(scores.items(), key=lambda x: x[1])

        if best_score == 0:
            return "try rephrasing or type the command directly"

        suggestions = {
            "status": "try 'status' or 'show me progress'",
            "map": "try 'map <directory>' or 'document <directory>'",
            "start": "try 'start working on <task>' or 'fix <issue>'",
            "tell": "try 'tell instance <id> about <context>'",
            "fetch": "try 'look up <query>' or 'search for <topic>'",
        }
        return suggestions[best_cmd]
