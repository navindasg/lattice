"""Tests for the Users module (ticket 01)."""
import pytest


@pytest.mark.asyncio
async def test_create_user(client):
    resp = await client.post("/users/", json={"name": "Alice", "email": "alice@test.com"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Alice"
    assert data["email"] == "alice@test.com"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_users(client):
    await client.post("/users/", json={"name": "Alice", "email": "alice@test.com"})
    await client.post("/users/", json={"name": "Bob", "email": "bob@test.com"})
    resp = await client.get("/users/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_user_not_found(client):
    resp = await client.get("/users/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "not found"


@pytest.mark.asyncio
async def test_delete_user(client):
    create_resp = await client.post(
        "/users/", json={"name": "Alice", "email": "alice@test.com"}
    )
    user_id = create_resp.json()["id"]
    del_resp = await client.delete(f"/users/{user_id}")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/users/{user_id}")
    assert get_resp.status_code == 404
