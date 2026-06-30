"""Login / logout HTMX routes.

PROVIDED — real, working code.

  * ``GET  /login``   -> renders the login form.
  * ``POST /login``   -> verifies credentials, creates a Postgres session, sets the
                         HttpOnly ``session`` cookie, redirects to /chat. On failure
                         re-renders the form with an error (HTMX-friendly 200).
  * ``GET  /logout``  -> deletes the session row + clears the cookie.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..auth.security import authenticate_user
from ..auth.session import create_session, delete_session
from ..templating import templates

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse, name="login_form")
async def login_form(request: Request):
    return templates.TemplateResponse(
        request, "login.html", {"error": None}
    )


@router.post("/login", response_class=HTMLResponse, name="login_submit")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    user = await authenticate_user(db, username, password)
    if user is None:
        # 200 so HTMX swaps the form back in with the error visible.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password."},
            status_code=status.HTTP_200_OK,
        )

    settings = get_settings()
    session_id = await create_session(
        db,
        user_id=user["user_id"],
        ip_addr=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # HX-Redirect lets HTMX perform a full client navigation post-login.
    redirect = RedirectResponse(url="/chat", status_code=status.HTTP_303_SEE_OTHER)
    if request.headers.get("HX-Request"):
        redirect = Response(status_code=status.HTTP_204_NO_CONTENT)
        redirect.headers["HX-Redirect"] = "/chat"
    redirect.set_cookie(
        key=settings.cookie_name,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    return redirect


@router.get("/logout", name="logout")
async def logout(request: Request, db: AsyncSession = Depends(get_session)):
    settings = get_settings()
    session_id = request.cookies.get(settings.cookie_name)
    if session_id:
        await delete_session(db, session_id)
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.cookie_name, path="/")
    return redirect
