"""Task queue with asyncio.PriorityQueue hot path and DuckDB persistence.

Uses asyncio.PriorityQueue with (priority_int, seq, TaskRecord) tuples
for in-memory ordering. DuckDB orchestrator_tasks table stores task records
for restart recovery and audit trail.

Priority mapping: high=0, normal=1, low=2 (lower int = higher priority).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import duckdb
import structlog

from lattice.orchestrator.models import TaskRecord

log = structlog.get_logger(__name__)

PRIORITY_MAP = {"high": 0, "normal": 1, "low": 2}

_CREATE_TASKS_TABLE = """
    CREATE TABLE IF NOT EXISTS orchestrator_tasks (
        task_id TEXT PRIMARY KEY,
        instance_id TEXT,
        priority TEXT NOT NULL DEFAULT 'normal',
        status TEXT NOT NULL DEFAULT 'pending',
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        assigned_at TEXT,
        completed_at TEXT,
        error TEXT
    )
"""


@dataclass(order=True)
class PrioritizedTask:
    """Wrapper for TaskRecord with priority and sequence ordering.

    Lower priority int = higher priority (high=0, normal=1, low=2).
    seq is monotonically increasing for FIFO within same priority level.
    task field is excluded from ordering comparisons.
    """

    priority: int
    seq: int
    task: Any = field(compare=False)


class TaskQueue:
    """Priority task queue with DuckDB backing.

    Hot path: asyncio.PriorityQueue for fast in-memory ordering.
    Cold path: DuckDB for persistence, recovery, and audit trail.

    Args:
        conn: An open duckdb.DuckDBPyConnection instance.
        max_depth: Maximum number of tasks in the in-memory queue (default 20).
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, max_depth: int = 20) -> None:
        self._conn = conn
        self._max_depth = max_depth
        self._queue: asyncio.PriorityQueue[PrioritizedTask] = asyncio.PriorityQueue(maxsize=max_depth)
        self._seq = 0
        self._create_tables()
        self._round_robin_index = 0

    def _create_tables(self) -> None:
        """Create orchestrator_tasks table idempotently."""
        self._conn.execute(_CREATE_TASKS_TABLE)

    def _persist_task(self, task: TaskRecord) -> None:
        """Insert or replace a task record in DuckDB."""
        self._conn.execute(
            "INSERT OR REPLACE INTO orchestrator_tasks "
            "(task_id, instance_id, priority, status, payload, created_at, assigned_at, completed_at, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                task.task_id,
                task.instance_id,
                task.priority,
                task.status,
                task.payload,
                task.created_at,
                task.assigned_at,
                task.completed_at,
                task.error,
            ],
        )

    def enqueue(self, payload: str, priority: str = "normal") -> TaskRecord:
        """Add a task to the queue.

        Raises asyncio.QueueFull if at max depth (backpressure).
        Uses put_nowait (not await put) to return backpressure error immediately.

        Args:
            payload: JSON string of task data.
            priority: Task priority — "high", "normal", or "low". Defaults to "normal".

        Returns:
            The created TaskRecord with status="pending".

        Raises:
            asyncio.QueueFull: If the in-memory queue is at max_depth.
        """
        task = TaskRecord(payload=payload, priority=priority)
        pri_int = PRIORITY_MAP.get(priority, 1)
        pt = PrioritizedTask(priority=pri_int, seq=self._seq, task=task)
        self._seq += 1
        self._queue.put_nowait(pt)  # raises asyncio.QueueFull if full
        self._persist_task(task)
        log.info("task_enqueued", task_id=task.task_id, priority=priority)
        return task

    async def dequeue(self) -> TaskRecord:
        """Get the highest-priority task from the queue.

        Blocks if the queue is empty.

        Returns:
            The highest-priority TaskRecord (FIFO within same priority).
        """
        pt = await self._queue.get()
        return pt.task

    def mark_assigned(self, task_id: str, instance_id: str) -> None:
        """Update task status to 'assigned' with instance_id and assigned_at timestamp.

        Args:
            task_id: The task UUID to update.
            instance_id: The instance that was assigned this task.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE orchestrator_tasks SET status = 'assigned', assigned_at = ?, instance_id = ? "
            "WHERE task_id = ?",
            [now, instance_id, task_id],
        )
        log.info("task_assigned", task_id=task_id, instance_id=instance_id)

    def mark_running(self, task_id: str) -> None:
        """Update task status to 'running'.

        Args:
            task_id: The task UUID to update.
        """
        self._conn.execute(
            "UPDATE orchestrator_tasks SET status = 'running' WHERE task_id = ?",
            [task_id],
        )

    def mark_completed(self, task_id: str) -> None:
        """Update task status to 'completed' with completed_at timestamp.

        Args:
            task_id: The task UUID to update.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE orchestrator_tasks SET status = 'completed', completed_at = ? "
            "WHERE task_id = ?",
            [now, task_id],
        )
        log.info("task_completed", task_id=task_id)

    def mark_failed(self, task_id: str, error: str) -> None:
        """Update task status to 'failed' with error message and completed_at timestamp.

        Args:
            task_id: The task UUID to update.
            error: Human-readable error description.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE orchestrator_tasks SET status = 'failed', completed_at = ?, error = ? "
            "WHERE task_id = ?",
            [now, error, task_id],
        )
        log.warning("task_failed", task_id=task_id, error=error)

    def mark_cancelled(self, task_id: str) -> None:
        """Update task status to 'cancelled' with completed_at timestamp.

        Args:
            task_id: The task UUID to update.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE orchestrator_tasks SET status = 'cancelled', completed_at = ? "
            "WHERE task_id = ?",
            [now, task_id],
        )

    def get_pending_tasks(self) -> list[dict[str, Any]]:
        """Get all pending tasks from DuckDB, ordered by priority then created_at.

        Returns:
            List of dicts with task fields, ordered high->normal->low then FIFO.
        """
        rows = self._conn.execute(
            "SELECT task_id, instance_id, priority, status, payload, created_at, assigned_at, completed_at, error "
            "FROM orchestrator_tasks WHERE status = 'pending' "
            "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 WHEN 'low' THEN 2 END, created_at"
        ).fetchall()
        return [
            {
                "task_id": r[0],
                "instance_id": r[1],
                "priority": r[2],
                "status": r[3],
                "payload": r[4],
                "created_at": r[5],
                "assigned_at": r[6],
                "completed_at": r[7],
                "error": r[8],
            }
            for r in rows
        ]

    def assign_next(self, idle_instance_ids: list[str]) -> str | None:
        """Round-robin assign next task to an idle instance.

        Args:
            idle_instance_ids: List of available idle instance IDs.

        Returns:
            The selected instance_id, or None if no instances available.
        """
        if not idle_instance_ids:
            return None
        instance_id = idle_instance_ids[self._round_robin_index % len(idle_instance_ids)]
        self._round_robin_index += 1
        return instance_id

    def reload_pending(self) -> int:
        """On restart, reload pending/assigned tasks from DuckDB back into in-memory queue.

        Resets assigned tasks back to pending in DuckDB so they can be
        re-dispatched. Returns count of reloaded tasks.

        Returns:
            Number of tasks successfully reloaded into the in-memory queue.
        """
        rows = self._conn.execute(
            "SELECT task_id, instance_id, priority, status, payload, created_at, assigned_at, completed_at, error "
            "FROM orchestrator_tasks WHERE status IN ('pending', 'assigned') "
            "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 WHEN 'low' THEN 2 END, created_at"
        ).fetchall()

        count = 0
        for r in rows:
            task = TaskRecord(
                task_id=r[0],
                instance_id=None,
                priority=r[2],
                status="pending",
                payload=r[4],
                created_at=r[5],
            )
            pri_int = PRIORITY_MAP.get(r[2], 1)
            pt = PrioritizedTask(priority=pri_int, seq=self._seq, task=task)
            self._seq += 1
            try:
                self._queue.put_nowait(pt)
                count += 1
            except asyncio.QueueFull:
                log.warning("reload_queue_full", task_id=r[0])
                break

        # Reset assigned tasks back to pending in DuckDB
        self._conn.execute(
            "UPDATE orchestrator_tasks SET status = 'pending', assigned_at = NULL, instance_id = NULL "
            "WHERE status = 'assigned'"
        )
        log.info("tasks_reloaded", count=count)
        return count
