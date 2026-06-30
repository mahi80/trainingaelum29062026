"""AutoLoan-DocIntel FastAPI + HTMX application shell.

This package provides the *provided* serving scaffold: app factory, config,
async DB engine, structlog JSON logging, request-context middleware, argon2 +
Postgres-session auth, and the HTMX page/router wiring. The candidate fills in
the real graph output paths that are clearly marked ``TODO(candidate)``.
"""
from __future__ import annotations

__all__ = ["create_app"]


def create_app():  # pragma: no cover - thin re-export
    """Re-export of :func:`app.main.create_app` for ``app:create_app`` targets."""
    from .main import create_app as _factory

    return _factory()
