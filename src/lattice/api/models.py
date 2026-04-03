"""Shared Pydantic request/response models for CLI --json and HTTP endpoints.

All models are frozen (immutable after construction).

Exports:
    MapperError      — structured error with code and message
    CommandRequest   — typed command request envelope
    CommandResponse  — typed command response envelope
    error_response   — helper to build an error response dict
    success_response — helper to build a success response dict
"""
from typing import Literal

from pydantic import BaseModel, Field


class MapperError(BaseModel):
    """Structured error returned in a CommandResponse.

    code: Machine-readable error code (e.g. "GRAPH_NOT_FOUND").
    message: Human-readable description.
    """

    code: str
    message: str

    model_config = {"frozen": True}


class CommandRequest(BaseModel):
    """Typed command request sent to the mapper.

    command: One of the six supported mapper commands.
    payload: Arbitrary key-value arguments for the command.
    """

    command: Literal[
        "map:init",
        "map:status",
        "map:hint",
        "map:doc",
        "map:gaps",
        "map:cross",
        "map:correct",
        "map:skip",
    ]
    payload: dict = Field(default_factory=dict)

    model_config = {"frozen": True}


class CommandResponse(BaseModel):
    """Typed command response envelope.

    success: True when the command succeeded, False on error.
    command: The command name that produced this response.
    data: Result payload; present when success=True, None on error.
    error: Structured error; present when success=False, None on success.
    """

    success: bool
    command: str
    data: dict | None = None
    error: MapperError | None = None

    model_config = {"frozen": True}


def error_response(command: str, code: str, message: str) -> dict:
    """Build a serialisable error response dict.

    Returns:
        {"success": False, "command": command, "data": None,
         "error": {"code": code, "message": message}}
    """
    return {
        "success": False,
        "command": command,
        "data": None,
        "error": {"code": code, "message": message},
    }


def success_response(command: str, data: dict) -> dict:
    """Build a serialisable success response dict.

    Returns:
        {"success": True, "command": command, "data": data, "error": None}
    """
    return {
        "success": True,
        "command": command,
        "data": data,
        "error": None,
    }
