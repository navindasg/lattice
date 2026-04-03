"""NDJSON stdin/stdout protocol for CC instance communication.

Each message is a complete JSON object terminated with \n.
Request/response correlation via UUID request_id field.
Stderr captured separately for diagnostics (logged, not parsed).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog

log = structlog.get_logger(__name__)


async def write_message(stdin: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    """Write a JSON message to stdin as NDJSON (JSON + newline).

    Calls drain() after write to flush the buffer.

    Args:
        stdin: asyncio.StreamWriter for the process stdin.
        payload: Dict to serialize as JSON.
    """
    data = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
    stdin.write(data)
    await stdin.drain()


async def read_message(stdout: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one NDJSON line from stdout.

    Returns parsed dict, or None on EOF or parse failure.
    JSONDecodeError on partial/corrupt lines is caught and logged.

    Args:
        stdout: asyncio.StreamReader for the process stdout.

    Returns:
        Parsed dict on success, None on EOF or parse error.
    """
    line = await stdout.readline()
    if not line:
        return None
    try:
        return json.loads(line.decode().strip())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("ndjson_parse_error", raw=line[:200], error=str(exc))
        return None


async def drain_stderr(stderr: asyncio.StreamReader, logger: Any | None = None) -> None:
    """Background task: consume stderr so buffer doesn't block stdout.

    Each line is logged at debug level via structlog.

    Args:
        stderr: asyncio.StreamReader for the process stderr.
        logger: Optional structlog logger. Defaults to module logger.
    """
    _log = logger or log
    async for line in stderr:
        _log.debug("cc_stderr", line=line.decode().rstrip())


def create_request_envelope(task_payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a task payload in a request envelope with auto-generated request_id.

    Args:
        task_payload: Dict of task data to wrap.

    Returns:
        New dict with request_id prepended and all task_payload keys included.
    """
    return {"request_id": str(uuid.uuid4()), **task_payload}


def parse_response_envelope(msg: dict[str, Any]) -> dict[str, Any]:
    """Parse a response envelope into structured result.

    Returns dict with keys: request_id, success, data (on success), error (on failure).

    Args:
        msg: Raw response dict from the CC instance.

    Returns:
        Structured dict with request_id, success, and either data or error.
    """
    result: dict[str, Any] = {
        "request_id": msg.get("request_id"),
        "success": msg.get("success", False),
    }
    if result["success"]:
        result["data"] = msg.get("data", {})
    else:
        result["error"] = msg.get("error", {"code": "UNKNOWN", "message": "No error details"})
    return result
