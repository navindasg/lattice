"""Integration tests for the /command HTTP endpoint."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lattice.api.app import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# map:status
# ---------------------------------------------------------------------------


class TestMapStatusHTTP:
    def test_cold_start(self, tmp_path: Path) -> None:
        """Cold start: no .agent-docs/ returns zeroed status with success=True."""
        response = client.post(
            "/command",
            json={"command": "map:status", "payload": {"target": str(tmp_path)}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["command"] == "map:status"
        assert body["data"]["passes_complete"]["init"] is False

    def test_with_graph(self, tmp_path: Path) -> None:
        """With _graph.json present, passes_complete.init is True."""
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir()
        (agent_docs / "_graph.json").write_text(
            json.dumps({"nodes": [], "edges": [], "metadata": {}}), encoding="utf-8"
        )

        response = client.post(
            "/command",
            json={"command": "map:status", "payload": {"target": str(tmp_path)}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["passes_complete"]["init"] is True


# ---------------------------------------------------------------------------
# map:init
# ---------------------------------------------------------------------------


class TestMapInitHTTP:
    def test_creates_graph(self, tmp_path: Path) -> None:
        """map:init creates _graph.json and returns metadata/nodes/edges."""
        response = client.post(
            "/command",
            json={"command": "map:init", "payload": {"target": str(tmp_path)}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["command"] == "map:init"
        assert "metadata" in body["data"]
        assert "nodes" in body["data"]
        assert "edges" in body["data"]

        graph_path = tmp_path / ".agent-docs" / "_graph.json"
        assert graph_path.exists()


# ---------------------------------------------------------------------------
# map:hint
# ---------------------------------------------------------------------------


class TestMapHintHTTP:
    def test_stores_hint(self, tmp_path: Path) -> None:
        """Storing a hint returns success with directory echoed back."""
        response = client.post(
            "/command",
            json={
                "command": "map:hint",
                "payload": {
                    "target": str(tmp_path),
                    "directory": "src/auth",
                    "text": "handles OAuth",
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["directory"] == "src/auth"

    def test_missing_fields(self, tmp_path: Path) -> None:
        """Missing directory or text returns error envelope with INVALID_PAYLOAD."""
        response = client.post(
            "/command",
            json={
                "command": "map:hint",
                "payload": {"target": str(tmp_path)},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_PAYLOAD"


# ---------------------------------------------------------------------------
# map:doc
# ---------------------------------------------------------------------------


class TestMapDocHTTP:
    def test_fire_and_forget_returns_run_id(self, tmp_path: Path) -> None:
        """With _graph.json present, map:doc returns immediately with run_id."""
        from lattice.cli.commands import _map_init_impl

        # Create graph first
        graph_data = _map_init_impl(tmp_path)
        agent_docs = tmp_path / ".agent-docs"
        agent_docs.mkdir(parents=True, exist_ok=True)
        (agent_docs / "_graph.json").write_text(
            json.dumps(graph_data), encoding="utf-8"
        )

        response = client.post(
            "/command",
            json={"command": "map:doc", "payload": {"target": str(tmp_path)}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["run_id"] is not None
        assert body["data"]["status"] == "started"

    def test_no_graph_returns_error(self, tmp_path: Path) -> None:
        """Without _graph.json, map:doc returns error envelope GRAPH_NOT_FOUND."""
        response = client.post(
            "/command",
            json={"command": "map:doc", "payload": {"target": str(tmp_path)}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "GRAPH_NOT_FOUND"


# ---------------------------------------------------------------------------
# map:gaps
# ---------------------------------------------------------------------------


class TestMapGapsHTTP:
    def test_no_graph_returns_error(self, tmp_path: Path) -> None:
        """Without _graph.json, map:gaps returns error envelope GRAPH_NOT_FOUND."""
        response = client.post(
            "/command",
            json={"command": "map:gaps", "payload": {"target": str(tmp_path)}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "GRAPH_NOT_FOUND"


# ---------------------------------------------------------------------------
# map:cross
# ---------------------------------------------------------------------------


class TestMapCrossHTTP:
    def test_no_graph_returns_error(self, tmp_path: Path) -> None:
        """Without _graph.json, map:cross returns error envelope GRAPH_NOT_FOUND."""
        response = client.post(
            "/command",
            json={"command": "map:cross", "payload": {"target": str(tmp_path)}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "GRAPH_NOT_FOUND"


# ---------------------------------------------------------------------------
# map:correct
# ---------------------------------------------------------------------------


class TestMapCorrectHTTP:
    def test_correct_summary_success(self, tmp_path: Path) -> None:
        """map:correct updates summary and returns success envelope."""
        from datetime import datetime, timezone

        from lattice.shadow.schema import DirDoc
        from lattice.shadow.writer import write_dir_doc

        doc = DirDoc(
            directory="src/auth",
            confidence=0.7,
            source="agent",
            confidence_factors=["static_analysis"],
            last_analyzed=datetime.now(timezone.utc),
            summary="Original summary",
        )
        write_dir_doc(doc, tmp_path / ".agent-docs")

        response = client.post(
            "/command",
            json={
                "command": "map:correct",
                "payload": {
                    "target": str(tmp_path),
                    "directory": "src/auth",
                    "field": "summary",
                    "value": "New improved summary",
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["command"] == "map:correct"
        assert body["data"]["directory"] == "src/auth"
        assert body["data"]["field"] == "summary"
        assert body["data"]["confidence"] == 1.0
        assert body["data"]["source"] == "developer"

    def test_correct_no_documentation(self, tmp_path: Path) -> None:
        """map:correct returns NO_DOCUMENTATION error when _dir.md is missing."""
        response = client.post(
            "/command",
            json={
                "command": "map:correct",
                "payload": {
                    "target": str(tmp_path),
                    "directory": "src/nonexistent",
                    "field": "summary",
                    "value": "New text",
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "NO_DOCUMENTATION"

    def test_correct_missing_fields(self, tmp_path: Path) -> None:
        """map:correct returns INVALID_PAYLOAD when required fields are missing."""
        response = client.post(
            "/command",
            json={
                "command": "map:correct",
                "payload": {"target": str(tmp_path)},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_PAYLOAD"


# ---------------------------------------------------------------------------
# map:skip
# ---------------------------------------------------------------------------


class TestMapSkipHTTP:
    def test_skip_success(self, tmp_path: Path) -> None:
        """map:skip stores skip entry and returns success envelope."""
        response = client.post(
            "/command",
            json={
                "command": "map:skip",
                "payload": {
                    "target": str(tmp_path),
                    "directory": "src/vendor",
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["command"] == "map:skip"
        assert body["data"]["directory"] == "src/vendor"
        assert body["data"]["skipped"] is True

    def test_skip_missing_directory(self, tmp_path: Path) -> None:
        """map:skip returns INVALID_PAYLOAD when directory is missing."""
        response = client.post(
            "/command",
            json={
                "command": "map:skip",
                "payload": {"target": str(tmp_path)},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_PAYLOAD"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_unknown_command(self) -> None:
        """Unknown command returns 422 (Pydantic Literal constraint violation)."""
        response = client.post(
            "/command",
            json={"command": "map:invalid", "payload": {}},
        )
        assert response.status_code == 422

    def test_missing_command_field(self) -> None:
        """Missing command field returns 422."""
        response = client.post("/command", json={})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# curl-compatibility and OpenAPI
# ---------------------------------------------------------------------------


def test_curl_compatible(tmp_path: Path) -> None:
    """Response Content-Type is application/json and body parses as JSON."""
    response = client.post(
        "/command",
        json={"command": "map:status", "payload": {"target": str(tmp_path)}},
    )
    assert "application/json" in response.headers["content-type"]
    parsed = response.json()
    assert isinstance(parsed, dict)


def test_openapi_spec_available() -> None:
    """GET /openapi.json returns 200 with /command in paths."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert "paths" in spec
    assert "/command" in spec["paths"]


def test_swagger_ui_available() -> None:
    """GET /docs returns 200 (Swagger UI)."""
    response = client.get("/docs")
    assert response.status_code == 200
