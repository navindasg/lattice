"""Agent event loop: consumes events from the event channel and feeds to agent.

AgentEventLoop bridges the event channel (asyncio.Queue of CCEvents) with
the LangGraph orchestrator agent. On each event:
    1. Updates STATE.md with event info
    2. Formats the event as a human message to the agent
    3. Invokes the agent graph
    4. If the agent calls cc_approve/cc_deny, submits the decision back
       to the event channel's approval waiter
    5. Logs decisions to STATE.md ## Decisions section

The loop runs concurrently with the voice pipeline and process manager.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph

from lattice.orchestrator.events.models import ApprovalDecision, CCEvent
from lattice.orchestrator.soul_ecosystem.models import DecisionRecord
from lattice.orchestrator.soul_ecosystem.reader import SoulReader
from lattice.orchestrator.soul_ecosystem.writer import SoulWriter

logger = structlog.get_logger(__name__)

# Maximum time to process a single event before timing out.
# Must be generous enough for cc_spawn (PTY spawn + 2s init delay + LLM calls).
_EVENT_PROCESSING_TIMEOUT = 120.0


class AgentEventLoop:
    """Consumes CC events and feeds them to the orchestrator agent.

    Args:
        graph: Compiled LangGraph StateGraph for the orchestrator.
        event_queue: asyncio.Queue of CCEvent objects from the event channel.
        soul_reader: SoulReader for state lookups.
        soul_writer: SoulWriter for state updates.
        approval_submit: Callable to submit approval decisions back to event channel.
        thread_id: LangGraph thread_id for checkpointing (default "orchestrator").
        shutdown_event: asyncio.Event to signal shutdown.
    """

    def __init__(
        self,
        graph: Any,  # Compiled graph
        event_queue: asyncio.Queue,
        soul_reader: SoulReader,
        soul_writer: SoulWriter,
        approval_submit: Any | None = None,
        thread_id: str = "orchestrator",
        shutdown_event: asyncio.Event | None = None,
        project_root: str | None = None,
    ) -> None:
        self._graph = graph
        self._event_queue = event_queue
        self._soul_reader = soul_reader
        self._soul_writer = soul_writer
        self._approval_submit = approval_submit
        self._thread_id = thread_id
        self._shutdown_event = shutdown_event or asyncio.Event()
        self._project_root = project_root
        self._event_count = 0
        self._instance_events: dict[str, list[dict[str, Any]]] = {}

    @property
    def event_count(self) -> int:
        """Number of events processed so far."""
        return self._event_count

    @property
    def instance_events(self) -> dict[str, list[dict[str, Any]]]:
        """Per-instance event history."""
        return self._instance_events

    async def run(self) -> None:
        """Main event processing loop.

        Runs until the shutdown event is set. Processes events from the
        queue one at a time, invoking the agent graph for each.
        """
        logger.info("agent_event_loop.started", thread_id=self._thread_id)

        while not self._shutdown_event.is_set():
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                continue

            # Filter: skip events from CC instances outside our project.
            # Synthetic events (voice commands) use session_id="voice-command"
            # and have no CWD — always accept those.
            if (
                self._project_root
                and event.cwd
                and event.session_id != "voice-command"
                and not event.cwd.startswith(self._project_root)
            ):
                logger.debug(
                    "agent_event_loop.filtered",
                    session_id=event.session_id[:8],
                    cwd=event.cwd,
                    project_root=self._project_root,
                )
                continue

            try:
                await asyncio.wait_for(
                    self._process_event(event),
                    timeout=_EVENT_PROCESSING_TIMEOUT,
                )
                self._event_count += 1
            except asyncio.TimeoutError:
                logger.warning(
                    "agent_event_loop.event_timeout",
                    event_type=event.event_type,
                    session_id=event.session_id,
                )
            except Exception as exc:
                logger.error(
                    "agent_event_loop.event_error",
                    event_type=event.event_type,
                    error=str(exc),
                )

        logger.info(
            "agent_event_loop.stopped",
            events_processed=self._event_count,
        )

    async def _process_event(self, event: CCEvent) -> None:
        """Process a single CC event through the agent graph.

        Args:
            event: The CCEvent to process.
        """
        # Track event in per-instance history
        instance_id = self._resolve_instance(event)
        event_dict = {
            "event_type": event.event_type,
            "tool_name": event.tool_name,
            "timestamp": event.timestamp.isoformat(),
            "session_id": event.session_id,
        }

        if instance_id:
            self._instance_events.setdefault(instance_id, []).append(event_dict)

        # Format event as a human message for the agent
        message_content = self._format_event_message(event, instance_id)
        human_msg = HumanMessage(content=message_content)

        # Invoke the agent graph
        config = {
            "configurable": {
                "thread_id": self._thread_id,
            }
        }

        result = await asyncio.to_thread(
            self._graph.invoke,
            {"messages": [human_msg]},
            config,
        )

        logger.info(
            "agent_event_loop.event_processed",
            event_type=event.event_type,
            instance_id=instance_id,
        )

    def _resolve_instance(self, event: CCEvent) -> str:
        """Resolve which CC instance produced this event.

        Uses session_id matching against known instances. Returns empty
        string if the instance cannot be resolved.

        Args:
            event: The CCEvent to resolve.

        Returns:
            Instance number as string, or "" if unresolved.
        """
        # For now, use session_id as a proxy. In production, the event
        # channel would include instance metadata.
        return event.session_id[:8] if event.session_id else ""

    def _format_event_message(self, event: CCEvent, instance_id: str) -> str:
        """Format a CCEvent as a human-readable message for the agent.

        Args:
            event: The CCEvent to format.
            instance_id: Resolved instance identifier.

        Returns:
            Formatted message string.
        """
        parts = [
            f"[EVENT] {event.event_type}",
            f"Instance: {instance_id or 'unknown'}",
            f"Session: {event.session_id}",
        ]

        if event.tool_name:
            parts.append(f"Tool: {event.tool_name}")

        if event.tool_input:
            # Summarize tool input (avoid massive payloads)
            input_summary = str(event.tool_input)
            if len(input_summary) > 500:
                input_summary = input_summary[:500] + "..."
            parts.append(f"Input: {input_summary}")

        if event.tool_response:
            parts.append(f"Message: {event.tool_response}")

        if event.cwd:
            parts.append(f"CWD: {event.cwd}")

        parts.append(f"Time: {event.timestamp.isoformat()}")

        return "\n".join(parts)

    async def flush_state(self) -> None:
        """Flush all in-context state to soul files.

        Called before context compaction to ensure no state is lost.
        Writes current instance assignments and recent decisions to STATE.md.
        """
        state = self._soul_reader.read_state()
        self._soul_writer.update_full_state(state)
        logger.info("agent_event_loop.state_flushed")

    async def restore_state(self) -> None:
        """Restore state from soul files after compaction.

        Re-reads soul files to rebuild in-context state after the
        context window has been compacted.
        """
        state = self._soul_reader.read_state()
        logger.info(
            "agent_event_loop.state_restored",
            instances=len(state.instances),
            decisions=len(state.decisions),
        )
