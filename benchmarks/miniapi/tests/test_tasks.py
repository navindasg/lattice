"""Tests for the Tasks module (ticket 03)."""
import pytest


@pytest.mark.asyncio
async def test_create_task(client, seed_project):
    project = await seed_project()
    resp = await client.post(
        "/tasks/", json={"title": "Fix bug", "project_id": project["id"]}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Fix bug"
    assert data["status"] == "todo"
    assert data["project_id"] == project["id"]
    assert data["assignee_id"] is None
    assert data["tag_ids"] == []


@pytest.mark.asyncio
async def test_create_task_bad_project(client):
    resp = await client.post(
        "/tasks/", json={"title": "Fix bug", "project_id": "nonexistent"}
    )
    assert resp.status_code == 400
    assert "project not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_task_status(client, seed_task):
    task = await seed_task()
    resp = await client.patch(
        f"/tasks/{task['id']}", json={"status": "doing"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "doing"


@pytest.mark.asyncio
async def test_patch_task_invalid_status(client, seed_task):
    task = await seed_task()
    resp = await client.patch(
        f"/tasks/{task['id']}", json={"status": "invalid"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_task(client, seed_task):
    task = await seed_task()
    del_resp = await client.delete(f"/tasks/{task['id']}")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/tasks/{task['id']}")
    assert get_resp.status_code == 404
