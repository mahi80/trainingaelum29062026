"""Password hashing (argon2id) + OAuth2 password flow issuing opaque bearers.

PROVIDED — real, working code.

  * ``hash_password`` / ``verify_password`` wrap argon2-cffi (argon2id).
  * ``oauth2_scheme`` is the FastAPI ``OAuth2PasswordBearer`` for ``/token``.
  * ``POST /token`` validates username/password against ``app.user_account`` and
    creates a Postgres session row (see app/auth/session.py). The *opaque bearer*
    returned IS the session_id — there is no JWT; the bearer maps 1:1 to a row in
    ``app.session``. The same value is also set as the HttpOnly ``session`` cookie.

The argon2 ``PasswordHasher`` is configured with sensible interactive params and
transparently rehashes legacy hashes on successful verify (``check_needs_rehash``).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from .session import create_session

# tokenUrl must match the mounted path so the OpenAPI "Authorize" button works.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

router = APIRouter(tags=["auth"])

# --- lazy argon2 hasher (import cost only on first use) -----------------------
_hasher = None


def _get_hasher():
    global _hasher
    if _hasher is None:
        from argon2 import PasswordHasher

        # Interactive-tier params: argon2id, 64 MiB, t=3, p=4.
        _hasher = PasswordHasher(
            time_cost=3, memory_cost=64 * 1024, parallelism=4, hash_len=32, salt_len=16
        )
    return _hasher


def hash_password(password: str) -> str:
    """Return an argon2id PHC-string hash for ``password``."""
    return _get_hasher().hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Constant-time verify of ``password`` against ``stored_hash``.

    Returns False on any mismatch/format error instead of raising.
    """
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

    try:
        return _get_hasher().verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHash, Exception):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _get_hasher().check_needs_rehash(stored_hash)
    except Exception:
        return False


async def authenticate_user(
    db: AsyncSession, username: str, password: str
) -> dict | None:
    """Return the user row dict if credentials are valid & active, else None."""
    row = (
        await db.execute(
            text(
                "SELECT user_id, username, email, password_hash, role, is_active "
                "FROM app.user_account WHERE username = :u"
            ),
            {"u": username},
        )
    ).mappings().first()
    if row is None or not row["is_active"]:
        return None
    if not verify_password(row["password_hash"], password):
        return None
    # Opportunistic rehash if argon2 params have since strengthened.
    if needs_rehash(row["password_hash"]):
        await db.execute(
            text(
                "UPDATE app.user_account SET password_hash = :h WHERE user_id = :id"
            ),
            {"h": hash_password(password), "id": row["user_id"]},
        )
        await db.commit()
    return dict(row)


@router.post("/token", summary="OAuth2 password grant -> opaque session bearer")
async def issue_token(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_session),
):
    """OAuth2 password flow. PROVIDED.

    On success returns ``{access_token, token_type}`` where ``access_token`` is the
    opaque session id, and also sets the HttpOnly ``session`` cookie so HTMX pages
    are authenticated without handling the token client-side.
    """
    from fastapi import HTTPException, status

    user = await authenticate_user(db, form.username, form.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()
    session_id = await create_session(
        db,
        user_id=user["user_id"],
        ip_addr=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response.set_cookie(
        key=settings.cookie_name,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    return {"access_token": session_id, "token_type": "bearer"}
