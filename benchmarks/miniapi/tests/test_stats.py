"""Tests for the Stats module (ticket 06)."""
import pytest


@pytest.mark.asyncio
async def test_stats_empty(client):
    resp = await client.get("/stats/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_users"] == 0
    assert data["total_projects"] == 0
    assert data["total_tasks"] == 0
    assert data["completion_pct"] == 0.0
    assert data["tasks_by_status"] == {"todo": 0, "doing": 0, "done": 0}
    assert data["tasks_per_user"] == {}


@pytest.mark.asyncio
async def test_stats_with_data(client, seed_user):
    user = await seed_user(name="Alice", email="alice@test.com")
    proj_resp = await client.post(
        "/projects/", json={"name": "P1", "owner_id": user["id"]}
    )
    project = proj_resp.json()

    # Create 3 tasks: 1 todo, 1 doing, 1 done
    t1 = (await client.post(
        "/tasks/",
        json={"title": "T1", "project_id": project["id"], "assignee_id": user["id"]},
    )).json()
    t2 = (await client.post(
        "/tasks/",
        json={"title": "T2", "project_id": project["id"], "assignee_id": user["id"]},
    )).json()
    t3 = (await client.post(
        "/tasks/",
        json={"title": "T3", "project_id": project["id"], "assignee_id": user["id"]},
    )).json()

    await client.patch(f"/tasks/{t2['id']}", json={"status": "doing"})
    await client.patch(f"/tasks/{t3['id']}", json={"status": "done"})

    resp = await client.get("/stats/")
    data = resp.json()
    assert data["total_users"] == 1
    assert data["total_projects"] == 1
    assert data["total_tasks"] == 3
    assert data["tasks_by_status"] == {"todo": 1, "doing": 1, "done": 1}
    assert data["completion_pct"] == pytest.approx(33.3, abs=0.1)
    assert data["tasks_per_user"][user["id"]] == 3


@pytest.mark.asyncio
async def test_stats_unassigned_tasks_excluded(client, seed_project):
    project = await seed_project()
    # Task with no assignee
    await client.post(
        "/tasks/", json={"title": "Unassigned", "project_id": project["id"]}
    )

    resp = await client.get("/stats/")
    data = resp.json()
    assert data["total_tasks"] == 1
    assert data["tasks_per_user"] == {}
