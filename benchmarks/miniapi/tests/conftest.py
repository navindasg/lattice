"""Shared test fixtures for miniapi benchmark."""
import pytest
from httpx import ASGITransport, AsyncClient

from miniapi.app import app
from miniapi.store import store


@pytest.fixture(autouse=True)
def _reset_store():
    """Clear all store data before each test."""
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    """Synchronous-style test client using httpx."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def seed_user(client):
    """Helper to create a user and return its dict."""
    async def _seed(name: str = "Alice", email: str = "alice@test.com") -> dict:
        resp = await client.post("/users/", json={"name": name, "email": email})
        assert resp.status_code == 201
        return resp.json()
    return _seed


@pytest.fixture
def seed_project(client, seed_user):
    """Helper to create a project (auto-creates owner) and return its dict."""
    async def _seed(name: str = "Project X") -> dict:
        user = await seed_user()
        resp = await client.post(
            "/projects/", json={"name": name, "owner_id": user["id"]}
        )
        assert resp.status_code == 201
        return resp.json()
    return _seed


@pytest.fixture
def seed_task(client, seed_project):
    """Helper to create a task (auto-creates project+user) and return its dict."""
    async def _seed(title: str = "Fix bug") -> dict:
        project = await seed_project()
        resp = await client.post(
            "/tasks/", json={"title": title, "project_id": project["id"]}
        )
        assert resp.status_code == 201
        return resp.json()
    return _seed
