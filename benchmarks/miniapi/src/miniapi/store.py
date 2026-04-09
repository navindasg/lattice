"""Shared in-memory data store for all miniapi modules.

All modules read and write to these dicts.  Keys are string UUIDs.
This is the ONLY shared state — modules must NOT create their own stores.

Usage:
    from miniapi.store import store

    store.users["abc-123"] = {"id": "abc-123", "name": "Alice", ...}
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Store:
    """Central in-memory data store.

    Each dict maps ``id`` (str) -> record (dict).

    Attributes:
        users: User records.  Keys: ``{id, name, email}``.
        projects: Project records.  Keys: ``{id, name, owner_id}``.
        tasks: Task records.  Keys: ``{id, title, status, project_id, assignee_id, tag_ids}``.
        tags: Tag records.  Keys: ``{id, name}``.
    """

    users: dict[str, dict] = field(default_factory=dict)
    projects: dict[str, dict] = field(default_factory=dict)
    tasks: dict[str, dict] = field(default_factory=dict)
    tags: dict[str, dict] = field(default_factory=dict)

    def reset(self) -> None:
        """Clear all data.  Called between test runs."""
        self.users.clear()
        self.projects.clear()
        self.tasks.clear()
        self.tags.clear()


store = Store()
