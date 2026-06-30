"""Shared Jinja2 templates instance.

PROVIDED. Single-sourced so every router renders from the same environment and
``app.main`` can register globals (e.g. url_for, current_user) in one place.
The templates directory resolves relative to this package, so it works no matter
the process CWD.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
