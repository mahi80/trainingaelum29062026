"""Shared pytest fixtures + path resolution for the AutoLoan-DocIntel grading harness.

The harness is run by evaluators from anywhere; it locates the repo deterministically
by walking up from this file until it finds a directory that contains both
``challenge-pack/`` and ``grading-kit/``. Environment overrides:

    SUBMISSION_ROOT   path to the candidate submission repo root (default: auto-detected)
    APP_BASE_URL      e.g. http://localhost:8000 ; if unset, live-app tests skip
    DATABASE_URL      postgresql://... ; if unset, DB-backed checks fall back to SQL parsing
    REDIS_URL         redis://... ; optional, only used by a few live checks

Nothing here imports heavy/optional deps at module load — psycopg/httpx are imported
lazily inside fixtures so the file imports cleanly without them installed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# path resolution
# --------------------------------------------------------------------------- #
def _find_repo_root(start: Path) -> Path:
    """Walk upward until we find the repo root (has challenge-pack/ + grading-kit/)."""
    env = os.environ.get("SUBMISSION_ROOT")
    if env:
        return Path(env).resolve()
    cur = start.resolve()
    for cand in [cur, *cur.parents]:
        if (cand / "challenge-pack").is_dir() and (cand / "grading-kit").is_dir():
            return cand
    # Fallback: two levels up from grading-kit/harness/.
    return start.resolve().parents[2]


REPO_ROOT = _find_repo_root(Path(__file__))
CHALLENGE_PACK = REPO_ROOT / "challenge-pack"
GRADING_KIT = REPO_ROOT / "grading-kit"
DDL_DIR = CHALLENGE_PACK / "db" / "ddl"
EXAMPLE_DIR = CHALLENGE_PACK / "example"
GT_DIR = EXAMPLE_DIR / "gt"
HIDDEN_GT_DIR = GRADING_KIT / "hidden" / "gt_test"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def challenge_pack() -> Path:
    return CHALLENGE_PACK


@pytest.fixture(scope="session")
def ddl_dir() -> Path:
    return DDL_DIR


@pytest.fixture(scope="session")
def example_dir() -> Path:
    return EXAMPLE_DIR


@pytest.fixture(scope="session")
def hidden_gt_dir() -> Path:
    return HIDDEN_GT_DIR


# --------------------------------------------------------------------------- #
# environment-gated fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def app_base_url() -> str:
    url = os.environ.get("APP_BASE_URL")
    if not url:
        pytest.skip("APP_BASE_URL not set — live-app tests skipped")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — DB-backed test skipped")
    return url


@pytest.fixture()
def db_conn(database_url: str):
    """Yield a live psycopg connection; imported lazily so the module loads without psycopg."""
    try:
        import psycopg  # noqa: WPS433  (lazy import is intentional)
    except ImportError:  # pragma: no cover
        pytest.skip("psycopg not installed")
    try:
        conn = psycopg.connect(database_url, connect_timeout=5)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"cannot connect to DATABASE_URL: {exc}")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def http_client(app_base_url: str):
    """Yield an httpx.Client bound to the app; imported lazily."""
    try:
        import httpx  # noqa: WPS433
    except ImportError:  # pragma: no cover
        pytest.skip("httpx not installed")
    with httpx.Client(base_url=app_base_url, timeout=10.0, follow_redirects=False) as client:
        yield client


# --------------------------------------------------------------------------- #
# shared helpers (importable by scorer.py and the test modules)
# --------------------------------------------------------------------------- #
def iter_ddl_sql() -> str:
    """Concatenate every DDL file in deterministic (sorted) order."""
    parts: list[str] = []
    for path in sorted(DDL_DIR.glob("*.sql")):
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)
