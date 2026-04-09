"""Tests for the Projects module (ticket 02)."""
import pytest


@pytest.mark.asyncio
async def test_create_project(client, seed_user):
    user = await seed_user()
    resp = await client.post(
        "/projects/", json={"name": "Project X", "owner_id": user["id"]}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Project X"
    assert data["owner_id"] == user["id"]
    assert "id" in data


@pytest.mark.asyncio
async def test_create_project_bad_owner(client):
    resp = await client.post(
        "/projects/", json={"name": "Project X", "owner_id": "nonexistent"}
    )
    assert resp.status_code == 400
    assert "owner not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_projects(client, seed_user):
    user = await seed_user()
    await client.post("/projects/", json={"name": "A", "owner_id": user["id"]})
    await client.post("/projects/", json={"name": "B", "owner_id": user["id"]})
    resp = await client.get("/projects/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_delete_project(client, seed_user):
    user = await seed_user()
    create_resp = await client.post(
        "/projects/", json={"name": "X", "owner_id": user["id"]}
    )
    project_id = create_resp.json()["id"]
    del_resp = await client.delete(f"/projects/{project_id}")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/projects/{project_id}")
    assert get_resp.status_code == 404
