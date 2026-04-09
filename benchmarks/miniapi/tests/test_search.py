"""Tests for the Search module (ticket 05)."""
import pytest

from miniapi.store import store


@pytest.mark.asyncio
async def test_search_by_status(client, seed_task):
    task = await seed_task(title="Task A")
    # Patch to "doing"
    await client.patch(f"/tasks/{task['id']}", json={"status": "doing"})
    # Create a second task that stays "todo"
    project_id = task["project_id"]
    await client.post("/tasks/", json={"title": "Task B", "project_id": project_id})

    resp = await client.get("/search/tasks", params={"status": "doing"})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["title"] == "Task A"


@pytest.mark.asyncio
async def test_search_by_title_substring(client, seed_task):
    await seed_task(title="Deploy to production")
    await seed_task(title="Fix login bug")

    resp = await client.get("/search/tasks", params={"q": "deploy"})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert "Deploy" in results[0]["title"]


@pytest.mark.asyncio
async def test_search_combined_filters(client, seed_task):
    task1 = await seed_task(title="Deploy v1")
    task2 = await seed_task(title="Deploy v2")
    # Mark task1 as done
    await client.patch(f"/tasks/{task1['id']}", json={"status": "done"})

    resp = await client.get(
        "/search/tasks", params={"q": "deploy", "status": "todo"}
    )
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["id"] == task2["id"]
