"""Entry point for `python -m lattice.api` invocation.

Runs the NDJSON stdio server for subprocess communication.
Equivalent to `python -m lattice.api.stdio`.
"""
import asyncio

from lattice.api.stdio import run_stdio_server

asyncio.run(run_stdio_server())
