"""Async SQLAlchemy engine + session dependency (asyncpg).

PROVIDED — real, working code. The engine is created lazily on first use so that
importing this module never requires a live database (keeps tests/CI importable
without Postgres). ``get_session`` is a FastAPI dependency yielding an
``AsyncSession``; ``ping`` is used by the readiness probe.
"""
from __future__ import annotations

from typing import AsyncIterator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """Lazily construct (once) and return the process-wide async engine."""
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
        _sessionmaker = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a transactional async session.

    Usage::

        @router.get("/x")
        async def handler(db: AsyncSession = Depends(get_session)): ...
    """
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping() -> bool:
    """Return True if a trivial ``SELECT 1`` round-trips. Used by /readyz."""
    try:
        maker = get_sessionmaker()
        async with maker() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engine() -> None:
    """Dispose the engine on shutdown (called from the app lifespan)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
