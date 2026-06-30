"""HTTP routers for the app shell.

Aggregates every router so app.main can include them in one place. Each module
documents which routes are PROVIDED vs where the candidate wires real graph output.
"""
from __future__ import annotations

from . import (
    admin_routes,
    auth_routes,
    chat_routes,
    docs_routes,
    health,
    underwriting_routes,
)

__all__ = [
    "auth_routes",
    "chat_routes",
    "docs_routes",
    "underwriting_routes",
    "admin_routes",
    "health",
]
