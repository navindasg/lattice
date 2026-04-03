"""Lattice Mapper HTTP API — single /command endpoint.

The FastAPI app dispatches all mapper commands through POST /command
with a JSON envelope: {"command": "map:init", "payload": {...}}.

FastAPI auto-generates an OpenAPI spec at /docs and /openapi.json.

Exports:
    app        — the FastAPI application instance
    create_app — factory function that creates a fresh FastAPI app
"""
from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks, FastAPI
from lattice.api.handlers import (
    handle_map_correct,
    handle_map_cross,
    handle_map_doc,
    handle_map_gaps,
    handle_map_hint,
    handle_map_init,
    handle_map_queue,
    handle_map_skip,
    handle_map_status,
    handle_map_test_status,
)
from lattice.api.models import CommandRequest, CommandResponse

HANDLERS: dict[str, Any] = {
    "map:init": handle_map_init,
    "map:status": handle_map_status,
    "map:hint": handle_map_hint,
    "map:doc": handle_map_doc,
    "map:gaps": handle_map_gaps,
    "map:cross": handle_map_cross,
    "map:correct": handle_map_correct,
    "map:skip": handle_map_skip,
    "map:queue": handle_map_queue,
    "map:test-status": handle_map_test_status,
}


def create_app() -> FastAPI:
    """Factory for the Lattice Mapper API.

    Returns:
        A configured FastAPI application with the /command endpoint registered.
    """
    _app = FastAPI(
        title="Lattice Mapper API",
        description=(
            "Codebase intelligence mapper — single /command endpoint "
            "for all mapper operations."
        ),
        version="0.1.0",
    )

    @_app.post("/command", response_model=CommandResponse)
    async def dispatch_command(
        request: CommandRequest,
        background_tasks: BackgroundTasks,
    ) -> dict:
        """Dispatch a mapper command.

        The command field must be one of: map:init, map:status, map:hint,
        map:doc, map:gaps, map:cross.  The payload is forwarded to the
        appropriate handler.  Unknown commands are rejected with 422 by
        Pydantic's Literal constraint on CommandRequest.
        """
        handler = HANDLERS[request.command]
        return await handler(request.payload, background_tasks)

    return _app


app = create_app()
