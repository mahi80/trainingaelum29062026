"""structlog JSON logging configuration.

PROVIDED — real, working code. Emits single-line JSON events suitable for log
aggregation. Every event carries ``request_id`` and ``correlation_id`` when they
are bound by the request-context middleware (see app/middleware/request_context.py).

Never log request/response bodies or PII here — the access-log event built by the
middleware only includes method/path/status/latency.
"""
from __future__ import annotations

import logging
import sys

import structlog


def _add_service(_logger, _name, event_dict):
    event_dict.setdefault("service", "autoloan-docintel")
    return event_dict


def configure_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    """Configure stdlib + structlog so all logs render as JSON to stdout.

    Idempotent: safe to call from create_app() on every startup.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,  # pulls request_id/correlation_id
        structlog.processors.add_log_level,
        _add_service,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy) through structlog formatting too.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(log_level)

    # Quiet noisy access logger; our middleware emits structured access logs.
    logging.getLogger("uvicorn.access").handlers[:] = []
    logging.getLogger("uvicorn.access").propagate = False


def get_logger(name: str | None = None):
    """Convenience accessor returning a bound structlog logger."""
    return structlog.get_logger(name)
