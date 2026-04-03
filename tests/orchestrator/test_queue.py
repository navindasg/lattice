"""Tests for TaskQueue: asyncio.PriorityQueue hot path, DuckDB persistence, round-robin.

Covers:
- PrioritizedTask ordering (high before normal, FIFO within same priority)
- enqueue/dequeue with priority ordering
- Backpressure: asyncio.QueueFull at max_queue_depth
- DuckDB persistence: table creation, task records, status transitions
- mark_assigned, mark_completed, mark_failed
- get_pending_tasks ordering
- assign_next round-robin
- reload_pending recovery
"""
from __future__ import annotations

import asyncio

import duckdb
import pytest

from lattice.orchestrator.queue import PrioritizedTask, TaskQueue
from lattice.orchestrator.models import TaskRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_queue(max_depth: int = 20) -> tuple[TaskQueue, duckdb.DuckDBPyConnection]:
    """Create a TaskQueue backed by an in-memory DuckDB connection."""
    conn = duckdb.connect(":memory:")
    queue = TaskQueue(conn, max_depth=max_depth)
    return queue, conn


def _make_task_record(priority: str = "normal") -> TaskRecord:
    return TaskRecord(payload='{"task": "test"}', priority=priority)


# ---------------------------------------------------------------------------
# PrioritizedTask ordering tests
# ---------------------------------------------------------------------------

def test_prioritized_task_high_before_normal() -> None:
    """PrioritizedTask high(0) < normal(1): high-priority dequeues first."""
    t1 = _make_task_record("high")
    t2 = _make_task_record("normal")
    pt_high = PrioritizedTask(priority=0, seq=0, task=t1)
    pt_normal = PrioritizedTask(priority=1, seq=0, task=t2)
    assert pt_high < pt_normal


def test_prioritized_task_fifo_within_same_priority() -> None:
    """PrioritizedTask FIFO: lower seq wins within same priority."""
    t1 = _make_task_record("normal")
    t2 = _make_task_record("normal")
    pt_first = PrioritizedTask(priority=1, seq=0, task=t1)
    pt_second = PrioritizedTask(priority=1, seq=1, task=t2)
    assert pt_first < pt_second


# ---------------------------------------------------------------------------
# TaskQueue enqueue/dequeue tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_creates_task_record_in_duckdb() -> None:
    """enqueue('normal') creates TaskRecord in DuckDB with status='pending'."""
    queue, conn = _make_queue()
    task = queue.enqueue('{"work": 1}', priority="normal")
    rows = conn.execute(
        "SELECT status FROM orchestrator_tasks WHERE task_id = ?", [task.task_id]
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "pending"


@pytest.mark.asyncio
async def test_enqueue_high_before_normal_dequeue_order() -> None:
    """enqueue high then normal: dequeue returns high-priority task first."""
    queue, _ = _make_queue()
    queue.enqueue('{"work": "normal"}', priority="normal")
    queue.enqueue('{"work": "high"}', priority="high")
    first = await queue.dequeue()
    assert first.priority == "high"


@pytest.mark.asyncio
async def test_enqueue_normal_fifo_order() -> None:
    """enqueue 3 normal tasks: dequeue returns them in FIFO order."""
    queue, _ = _make_queue()
    t1 = queue.enqueue('{"seq": 1}', priority="normal")
    t2 = queue.enqueue('{"seq": 2}', priority="normal")
    t3 = queue.enqueue('{"seq": 3}', priority="normal")
    d1 = await queue.dequeue()
    d2 = await queue.dequeue()
    d3 = await queue.dequeue()
    assert d1.task_id == t1.task_id
    assert d2.task_id == t2.task_id
    assert d3.task_id == t3.task_id


@pytest.mark.asyncio
async def test_enqueue_raises_queue_full_at_max_depth() -> None:
    """enqueue at max_queue_depth raises asyncio.QueueFull (backpressure)."""
    queue, _ = _make_queue(max_depth=2)
    queue.enqueue('{"x": 1}', priority="normal")
    queue.enqueue('{"x": 2}', priority="normal")
    with pytest.raises(asyncio.QueueFull):
        queue.enqueue('{"x": 3}', priority="normal")


@pytest.mark.asyncio
async def test_dequeue_blocks_on_empty_queue() -> None:
    """dequeue on empty queue blocks (wait_for timeout)."""
    queue, _ = _make_queue()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.dequeue(), timeout=0.05)


# ---------------------------------------------------------------------------
# DuckDB persistence tests
# ---------------------------------------------------------------------------

def test_taskqueue_init_creates_orchestrator_tasks_table() -> None:
    """TaskQueue.__init__ creates orchestrator_tasks table."""
    conn = duckdb.connect(":memory:")
    TaskQueue(conn)
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'orchestrator_tasks'"
    ).fetchall()
    assert len(tables) == 1


def test_enqueue_persists_task_to_duckdb() -> None:
    """After enqueue, SELECT from orchestrator_tasks returns task with status='pending'."""
    queue, conn = _make_queue()
    task = queue.enqueue('{"data": "test"}', priority="high")
    row = conn.execute(
        "SELECT task_id, priority, status FROM orchestrator_tasks WHERE task_id = ?",
        [task.task_id],
    ).fetchone()
    assert row is not None
    assert row[0] == task.task_id
    assert row[1] == "high"
    assert row[2] == "pending"


def test_mark_assigned_updates_status_and_timestamps() -> None:
    """mark_assigned updates status to 'assigned', sets assigned_at and instance_id."""
    queue, conn = _make_queue()
    task = queue.enqueue('{"work": 1}')
    queue.mark_assigned(task.task_id, "inst-001")
    row = conn.execute(
        "SELECT status, assigned_at, instance_id FROM orchestrator_tasks WHERE task_id = ?",
        [task.task_id],
    ).fetchone()
    assert row[0] == "assigned"
    assert row[1] is not None  # assigned_at set
    assert row[2] == "inst-001"


def test_mark_completed_updates_status_and_completed_at() -> None:
    """mark_completed updates status to 'completed' and sets completed_at."""
    queue, conn = _make_queue()
    task = queue.enqueue('{"work": 1}')
    queue.mark_completed(task.task_id)
    row = conn.execute(
        "SELECT status, completed_at FROM orchestrator_tasks WHERE task_id = ?",
        [task.task_id],
    ).fetchone()
    assert row[0] == "completed"
    assert row[1] is not None


def test_mark_failed_updates_status_and_error() -> None:
    """mark_failed updates status to 'failed' and sets error field."""
    queue, conn = _make_queue()
    task = queue.enqueue('{"work": 1}')
    queue.mark_failed(task.task_id, error="boom")
    row = conn.execute(
        "SELECT status, error FROM orchestrator_tasks WHERE task_id = ?",
        [task.task_id],
    ).fetchone()
    assert row[0] == "failed"
    assert row[1] == "boom"


def test_get_pending_tasks_returns_only_pending() -> None:
    """get_pending_tasks returns only tasks with status='pending'."""
    queue, conn = _make_queue()
    t1 = queue.enqueue('{"a": 1}', priority="normal")
    t2 = queue.enqueue('{"b": 2}', priority="high")
    t3 = queue.enqueue('{"c": 3}', priority="low")
    # Complete one task
    queue.mark_completed(t1.task_id)
    pending = queue.get_pending_tasks()
    pending_ids = {r["task_id"] for r in pending}
    assert t1.task_id not in pending_ids
    assert t2.task_id in pending_ids
    assert t3.task_id in pending_ids


def test_get_pending_tasks_ordered_by_priority() -> None:
    """get_pending_tasks returns tasks ordered by priority then created_at."""
    queue, _ = _make_queue()
    queue.enqueue('{"x": 1}', priority="low")
    queue.enqueue('{"x": 2}', priority="normal")
    queue.enqueue('{"x": 3}', priority="high")
    pending = queue.get_pending_tasks()
    assert pending[0]["priority"] == "high"
    assert pending[1]["priority"] == "normal"
    assert pending[2]["priority"] == "low"


# ---------------------------------------------------------------------------
# Round-robin assignment test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_next_round_robin_across_instances() -> None:
    """assign_next alternates instances across multiple calls."""
    queue, _ = _make_queue()
    instances = ["inst-A", "inst-B"]
    assignments = [queue.assign_next(instances) for _ in range(4)]
    assert assignments[0] == "inst-A"
    assert assignments[1] == "inst-B"
    assert assignments[2] == "inst-A"
    assert assignments[3] == "inst-B"


def test_assign_next_returns_none_when_no_instances() -> None:
    """assign_next returns None when no idle instances available."""
    queue, _ = _make_queue()
    assert queue.assign_next([]) is None


# ---------------------------------------------------------------------------
# Recovery test
# ---------------------------------------------------------------------------

def test_reload_pending_loads_tasks_back_into_queue() -> None:
    """reload_pending loads tasks with status in ('pending', 'assigned') back into queue."""
    conn = duckdb.connect(":memory:")
    queue1 = TaskQueue(conn)
    t1 = queue1.enqueue('{"task": 1}', priority="normal")
    t2 = queue1.enqueue('{"task": 2}', priority="high")
    # Simulate assigned task
    queue1.mark_assigned(t2.task_id, "inst-001")

    # Create new queue (restart scenario) — reuse same conn (same DB)
    queue2 = TaskQueue(conn)
    count = queue2.reload_pending()
    assert count == 2  # both pending and assigned tasks reloaded


@pytest.mark.asyncio
async def test_reload_pending_dequeues_high_priority_first() -> None:
    """After reload_pending, high-priority task dequeues before normal."""
    conn = duckdb.connect(":memory:")
    queue1 = TaskQueue(conn)
    queue1.enqueue('{"task": "normal"}', priority="normal")
    queue1.enqueue('{"task": "high"}', priority="high")

    queue2 = TaskQueue(conn)
    queue2.reload_pending()
    first = await queue2.dequeue()
    assert first.priority == "high"
