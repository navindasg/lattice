"""Tests for the Tags module (ticket 04)."""
import pytest


@pytest.mark.asyncio
async def test_create_tag(client):
    resp = await client.post("/tags/", json={"name": "urgent"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "urgent"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_duplicate_tag(client):
    await client.post("/tags/", json={"name": "urgent"})
    resp = await client.post("/tags/", json={"name": "urgent"})
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_attach_tag_to_task(client, seed_task):
    task = await seed_task()
    tag_resp = await client.post("/tags/", json={"name": "bug"})
    tag = tag_resp.json()

    attach_resp = await client.post(f"/tags/{tag['id']}/tasks/{task['id']}")
    assert attach_resp.status_code == 200
    assert attach_resp.json()["status"] == "attached"

    # Verify tag_ids updated on the task
    task_resp = await client.get(f"/tasks/{task['id']}")
    assert tag["id"] in task_resp.json()["tag_ids"]


@pytest.mark.asyncio
async def test_detach_tag_from_task(client, seed_task):
    task = await seed_task()
    tag_resp = await client.post("/tags/", json={"name": "bug"})
    tag = tag_resp.json()

    await client.post(f"/tags/{tag['id']}/tasks/{task['id']}")
    detach_resp = await client.delete(f"/tags/{tag['id']}/tasks/{task['id']}")
    assert detach_resp.status_code == 200
    assert detach_resp.json()["status"] == "detached"

    task_resp = await client.get(f"/tasks/{task['id']}")
    assert tag["id"] not in task_resp.json()["tag_ids"]
