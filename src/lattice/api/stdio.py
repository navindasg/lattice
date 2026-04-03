"""Thin NDJSON stdin->HANDLERS->stdout loop for Mapper subprocess mode.

This module allows the Mapper API to be invoked as a subprocess communicating
via NDJSON (one JSON object per line) on stdin/stdout, rather than over HTTP.

Usage:
    python -m lattice.api.stdio

Each line of stdin must be a JSON object with fields:
    command: str   — one of the HANDLERS keys (e.g. "map:status")
    payload: dict  — arbitrary command arguments

Each line of stdout is a JSON response envelope (success or error).

Design decisions:
- Uses asyncio.connect_read_pipe (non-blocking) — never sys.stdin.readline()
  which would block the event loop (RESEARCH Pitfall 2)
- Looks up handlers directly in HANDLERS dict, not via CommandRequest Pydantic
  validation — HANDLERS is the ground truth (RESEARCH Open Question 2)
- Passes None for background_tasks — handlers must tolerate None (see handlers.py)
- Malformed JSON lines are skipped silently (no crash, continue loop)
- EOF (empty readline) exits cleanly

Exports:
    run_stdio_server       — async entry point for subprocess NDJSON loop
    _dispatch_loop         — testable inner loop (accepts reader/write_fn)
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Callable, Awaitable

import structlog

from lattice.api.app import HANDLERS
from lattice.api.models import error_response

log = structlog.get_logger(__name__)


async def _dispatch_loop(
    reader: asyncio.StreamReader,
    write_fn: Callable[[bytes], None],
) -> None:
    """Inner dispatch loop — reads NDJSON from reader, writes responses via write_fn.

    Extracted for testability: callers can inject any reader/writer combination.

    Args:
        reader: AsyncIO StreamReader that provides NDJSON lines.
        write_fn: Callable that accepts bytes and writes them to output.
    """
    while True:
        line = await reader.readline()

        # Empty line = EOF — exit cleanly
        if not line:
            log.debug("stdio_eof_received")
            break

        # Parse JSON — skip malformed lines without crashing
        try:
            msg: dict[str, Any] = json.loads(line.decode().strip())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("stdio_parse_error", error=str(exc), raw=line[:100])
            continue

        command: str = msg.get("command", "")
        payload: dict[str, Any] = msg.get("payload", {})

        # Dispatch to handler
        handler = HANDLERS.get(command)
        if handler is None:
            response = error_response(
                command,
                "UNKNOWN_COMMAND",
                f"No handler for {command!r}",
            )
        else:
            try:
                # Pass None for background_tasks — handlers must tolerate None
                response = await handler(payload, None)
            except Exception as exc:
                log.error("stdio_handler_error", command=command, error=str(exc))
                response = error_response(
                    command,
                    "HANDLER_ERROR",
                    f"Handler raised: {exc}",
                )

        # Write response as compact NDJSON
        encoded = json.dumps(response, separators=(",", ":")).encode() + b"\n"
        write_fn(encoded)


async def run_stdio_server() -> None:
    """Run the NDJSON stdin->HANDLERS->stdout dispatch loop.

    Sets up async stdin/stdout pipes using connect_read_pipe/connect_write_pipe
    and delegates to _dispatch_loop.

    Runs until stdin is closed (EOF) or the process receives a signal.
    """
    loop = asyncio.get_running_loop()

    # --- Set up async stdin reader ---
    reader = asyncio.StreamReader()
    read_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: read_protocol, sys.stdin.buffer)

    # --- Set up async stdout writer ---
    write_transport, _ = await loop.connect_write_pipe(
        asyncio.BaseProtocol, sys.stdout.buffer
    )

    log.debug("stdio_server_started")

    try:
        await _dispatch_loop(reader, write_transport.write)
    finally:
        log.debug("stdio_server_stopped")


if __name__ == "__main__":
    asyncio.run(run_stdio_server())
