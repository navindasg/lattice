"""Tests for AgentEventLoop: event consumption and agent invocation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lattice.orchestrator.agent.event_loop import AgentEventLoop
from lattice.orchestrator.events.models import CCEvent
from lattice.orchestrator.soul_ecosystem.models import (
    InstanceAssignment,
    OrchestratorState,
)


def _make_event(
    event_type: str = "PostToolUse",
    session_id: str = "sess-001",
    tool_name: str = "Bash",
) -> CCEvent:
    """Create a CCEvent for testing."""
    return CCEvent(
        session_id=session_id,
        event_type=event_type,
        tool_name=tool_name,
        tool_input={"command": "ls"},
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_graph() -> MagicMock:
    """Create a mock compiled graph."""
    graph = MagicMock()
    graph.invoke.return_value = {"messages": []}
    return graph


@pytest.fixture
def mock_soul_reader() -> MagicMock:
    reader = MagicMock()
    reader.read_state.return_value = OrchestratorState(
        instances=[
            InstanceAssignment(
                instance_id="3",
                task_description="fix auth",
                status="active",
                assigned_at="2026-04-07T12:00:00Z",
            )
        ],
        plan=[],
        decisions=[],
        blockers=[],
    )
    return reader


@pytest.fixture
def mock_soul_writer() -> MagicMock:
    return MagicMock()


@pytest.fixture
def event_queue() -> asyncio.Queue:
    return asyncio.Queue()


@pytest.fixture
def shutdown_event() -> asyncio.Event:
    return asyncio.Event()


@pytest.fixture
def event_loop_obj(
    mock_graph: MagicMock,
    event_queue: asyncio.Queue,
    mock_soul_reader: MagicMock,
    mock_soul_writer: MagicMock,
    shutdown_event: asyncio.Event,
) -> AgentEventLoop:
    return AgentEventLoop(
        graph=mock_graph,
        event_queue=event_queue,
        soul_reader=mock_soul_reader,
        soul_writer=mock_soul_writer,
        shutdown_event=shutdown_event,
    )


class TestAgentEventLoop:
    @pytest.mark.asyncio
    async def test_processes_single_event(
        self,
        event_loop_obj: AgentEventLoop,
        event_queue: asyncio.Queue,
        mock_graph: MagicMock,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Event loop processes an event and invokes the graph."""
        event = _make_event()
        await event_queue.put(event)

        # Run for a short time then shutdown
        async def shutdown_after_delay():
            await asyncio.sleep(0.1)
            shutdown_event.set()

        await asyncio.gather(
            event_loop_obj.run(),
            shutdown_after_delay(),
        )

        assert event_loop_obj.event_count == 1
        mock_graph.invoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_processes_multiple_events(
        self,
        event_loop_obj: AgentEventLoop,
        event_queue: asyncio.Queue,
        mock_graph: MagicMock,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Event loop processes multiple events sequentially."""
        for i in range(3):
            await event_queue.put(_make_event(session_id=f"sess-{i}"))

        async def shutdown_after_delay():
            await asyncio.sleep(0.3)
            shutdown_event.set()

        await asyncio.gather(
            event_loop_obj.run(),
            shutdown_after_delay(),
        )

        assert event_loop_obj.event_count == 3
        assert mock_graph.invoke.call_count == 3

    @pytest.mark.asyncio
    async def test_tracks_per_instance_events(
        self,
        event_loop_obj: AgentEventLoop,
        event_queue: asyncio.Queue,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Event loop tracks events per instance."""
        await event_queue.put(_make_event(session_id="aaa11111-sess"))
        await event_queue.put(_make_event(session_id="bbb22222-sess"))
        await event_queue.put(_make_event(session_id="aaa11111-sess"))

        async def shutdown_after_delay():
            await asyncio.sleep(0.3)
            shutdown_event.set()

        await asyncio.gather(
            event_loop_obj.run(),
            shutdown_after_delay(),
        )

        # instance_events keyed by first 8 chars of session_id
        assert len(event_loop_obj.instance_events) == 2

    @pytest.mark.asyncio
    async def test_handles_graph_error_gracefully(
        self,
        event_loop_obj: AgentEventLoop,
        event_queue: asyncio.Queue,
        mock_graph: MagicMock,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Event loop continues after graph invocation error."""
        mock_graph.invoke.side_effect = [RuntimeError("test error"), {"messages": []}]
        await event_queue.put(_make_event())
        await event_queue.put(_make_event())

        async def shutdown_after_delay():
            await asyncio.sleep(0.3)
            shutdown_event.set()

        await asyncio.gather(
            event_loop_obj.run(),
            shutdown_after_delay(),
        )

        # First event failed, second succeeded
        assert event_loop_obj.event_count == 1

    @pytest.mark.asyncio
    async def test_stops_on_shutdown_event(
        self,
        event_loop_obj: AgentEventLoop,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Event loop exits when shutdown event is set."""
        shutdown_event.set()
        await event_loop_obj.run()
        assert event_loop_obj.event_count == 0

    @pytest.mark.asyncio
    async def test_flush_state(
        self,
        event_loop_obj: AgentEventLoop,
        mock_soul_reader: MagicMock,
        mock_soul_writer: MagicMock,
    ) -> None:
        """flush_state writes current state to soul files."""
        await event_loop_obj.flush_state()
        mock_soul_writer.update_full_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_restore_state(
        self,
        event_loop_obj: AgentEventLoop,
        mock_soul_reader: MagicMock,
    ) -> None:
        """restore_state re-reads state from soul files."""
        await event_loop_obj.restore_state()
        mock_soul_reader.read_state.assert_called()

    @pytest.mark.asyncio
    async def test_event_message_includes_event_type(
        self,
        event_loop_obj: AgentEventLoop,
    ) -> None:
        """Formatted event message includes the event type."""
        event = _make_event(event_type="PreToolUse")
        msg = event_loop_obj._format_event_message(event, "3")
        assert "PreToolUse" in msg

    @pytest.mark.asyncio
    async def test_event_message_includes_tool_name(
        self,
        event_loop_obj: AgentEventLoop,
    ) -> None:
        """Formatted event message includes tool name when present."""
        event = _make_event(tool_name="Write")
        msg = event_loop_obj._format_event_message(event, "3")
        assert "Write" in msg

    @pytest.mark.asyncio
    async def test_concurrent_events_from_different_instances(
        self,
        event_loop_obj: AgentEventLoop,
        event_queue: asyncio.Queue,
        mock_graph: MagicMock,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Events from different instances are processed independently."""
        # Simulate 6 concurrent instances sending events (unique first 8 chars)
        for i in range(1, 7):
            await event_queue.put(_make_event(session_id=f"inst{i:04d}-session"))

        async def shutdown_after_delay():
            await asyncio.sleep(0.5)
            shutdown_event.set()

        await asyncio.gather(
            event_loop_obj.run(),
            shutdown_after_delay(),
        )

        assert event_loop_obj.event_count == 6
        assert len(event_loop_obj.instance_events) == 6
