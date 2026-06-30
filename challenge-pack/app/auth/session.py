"""Postgres-backed server-side sessions (table ``app.session``).

PROVIDED — real, working code. Sessions live in Postgres (NOT Redis) per the firm
stack. The session_id (UUID) doubles as the opaque bearer / cookie value.

  * ``create_session``  -> INSERT a row, return str(session_id).
  * ``load_session``    -> SELECT a live (non-expired) row joined to its user.
  * ``touch_session``   -> roll ``last_seen_at``; enforce ``expires_at`` (delete &
                           return None if past expiry).
  * ``delete_session``  -> DELETE on logout.

All functions accept an ``AsyncSession`` so they participate in the request's
transaction. Expiry is computed in SQL with ``now() + interval`` to avoid client
clock skew.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings


def _coerce_uuid(value: str) -> Optional[UUID]:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def create_session(
    db: AsyncSession,
    *,
    user_id: int,
    ip_addr: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create a session row and return its id (str UUID)."""
    ttl_hours = get_settings().session_ttl_hours
    row = (
        await db.execute(
            text(
                """
                INSERT INTO app.session (user_id, expires_at, ip_addr, user_agent)
                VALUES (
                    :user_id,
                    now() + make_interval(hours => :ttl),
                    CAST(:ip AS inet),
                    :ua
                )
                RETURNING session_id
                """
            ),
            {"user_id": user_id, "ttl": ttl_hours, "ip": ip_addr, "ua": user_agent},
        )
    ).first()
    await db.commit()
    return str(row[0])


async def load_session(db: AsyncSession, session_id: str) -> Optional[dict]:
    """Return ``{session..., user...}`` for a live session, else None.

    A session is "live" iff it exists and ``expires_at > now()``.
    """
    sid = _coerce_uuid(session_id)
    if sid is None:
        return None
    row = (
        await db.execute(
            text(
                """
                SELECT s.session_id, s.user_id, s.created_at, s.last_seen_at,
                       s.expires_at, s.data,
                       u.username, u.email, u.role, u.is_active
                FROM app.session s
                JOIN app.user_account u ON u.user_id = s.user_id
                WHERE s.session_id = :sid
                  AND s.expires_at > now()
                """
            ),
            {"sid": str(sid)},
        )
    ).mappings().first()
    return dict(row) if row else None


async def touch_session(db: AsyncSession, session_id: str) -> Optional[dict]:
    """Roll ``last_seen_at`` and re-validate expiry.

    Returns the refreshed session/user dict, or None if expired/absent (in which
    case any stale row is removed). Sliding expiry is intentionally *not* applied
    here — ``expires_at`` is an absolute deadline set at creation.
    """
    sid = _coerce_uuid(session_id)
    if sid is None:
        return None
    updated = (
        await db.execute(
            text(
                """
                UPDATE app.session
                SET last_seen_at = now()
                WHERE session_id = :sid AND expires_at > now()
                RETURNING session_id
                """
            ),
            {"sid": str(sid)},
        )
    ).first()
    if updated is None:
        # Expired or unknown -> clean up any leftover expired row.
        await db.execute(
            text("DELETE FROM app.session WHERE session_id = :sid"),
            {"sid": str(sid)},
        )
        await db.commit()
        return None
    await db.commit()
    return await load_session(db, session_id)


async def delete_session(db: AsyncSession, session_id: str) -> None:
    """Delete a session (logout). No-op if id is malformed/absent."""
    sid = _coerce_uuid(session_id)
    if sid is None:
        return
    await db.execute(
        text("DELETE FROM app.session WHERE session_id = :sid"),
        {"sid": str(sid)},
    )
    await db.commit()
