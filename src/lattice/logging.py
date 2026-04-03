"""Structured logging configuration for Lattice.

Provides a single configure_logging() entry point that:
- Uses ConsoleRenderer (colored key=value) when stderr is a tty (dev)
- Uses JSONRenderer when stderr is not a tty (CI, piped output, production)
- Supports correlation ID binding via structlog.contextvars.bind_contextvars()
- Configures log level filtering via make_filtering_bound_logger
"""
import logging
import sys

import structlog
import structlog.contextvars
import structlog.dev
import structlog.processors


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog for the application.

    Args:
        log_level: Minimum log level to emit. One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
                   Defaults to "INFO".

    Usage:
        Call once at application startup (CLI entry points, test conftest.py):

            from lattice.logging import configure_logging
            configure_logging(log_level="INFO")

        Bind correlation IDs before logging:

            import structlog.contextvars
            structlog.contextvars.bind_contextvars(session_id="abc123")
            log = structlog.get_logger()
            log.info("session_started", path="/some/path")
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if sys.stderr.isatty():
        processors: list[structlog.types.Processor] = shared_processors + [
            structlog.dev.ConsoleRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
