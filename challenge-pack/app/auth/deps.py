"""Auth dependencies: resolve a request to the current user.

PROVIDED — real, working code.

``get_current_user`` accepts EITHER:
  * the ``session`` HttpOnly cookie (HTMX browser flow), OR
  * an ``Authorization: Bearer <session_id>`` header (API/OAuth2 flow).

Both carry the same opaque session id. The session is validated and touched
(``last_seen_at`` rolled, expiry enforced). On failure it raises 401.

``get_optional_user`` is the non-raising variant for pages that render a public
view when logged out (e.g. the login page / nav bar).
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from .security import oauth2_scheme
from .session import touch_session


def _extract_session_id(request: Request, bearer: Optional[str]) -> Optional[str]:
    """Prefer the bearer token; fall back to the session cookie."""
    if bearer:
        return bearer
    cookie_name = get_settings().cookie_name
    return request.cookies.get(cookie_name)


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_session),
    bearer: Optional[str] = Depends(oauth2_scheme),
) -> Optional[dict]:
    """Return the user dict if authenticated, else None (never raises)."""
    session_id = _extract_session_id(request, bearer)
    if not session_id:
        return None
    session = await touch_session(db, session_id)
    if session is None or not session.get("is_active"):
        return None
    # expose for downstream handlers/templates
    request.state.session = session
    return {
        "user_id": session["user_id"],
        "username": session["username"],
        "email": session["email"],
        "role": session["role"],
        "session_id": str(session["session_id"]),
    }


async def get_current_user(
    user: Optional[dict] = Depends(get_optional_user),
) -> dict:
    """Require an authenticated user or raise 401."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(*roles: str):
    """Dependency factory enforcing that the current user has one of ``roles``."""

    async def _checker(user: dict = Depends(get_current_user)) -> dict:
        if roles and user.get("role") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role"
            )
        return user

    return _checker
