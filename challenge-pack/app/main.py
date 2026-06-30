"""FastAPI application factory.

PROVIDED — real, working code.

``create_app()``:
  * configures structlog JSON logging,
  * builds the FastAPI app with the OpenAPI title from settings (MUST match
    openapi/contract.yaml: "AutoLoan-DocIntel API"),
  * mounts ``/static`` and registers the shared Jinja2 templates,
  * installs middleware in the correct order (request-context outermost so it wraps
    everything, including error responses),
  * registers RFC7807 ``application/problem+json`` error handlers,
  * includes the auth + page routers and the ops health router,
  * disposes the DB engine on shutdown.

Run with: ``uvicorn app.main:app --reload`` (``app`` is created at import time).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth.security import router as token_router
from .config import get_settings
from .db import dispose_engine
from .logging_conf import configure_logging, get_logger
from .middleware.request_context import (
    RequestContextMiddleware,
    get_correlation_id,
    get_request_id,
)
from .routers import (
    admin_routes,
    auth_routes,
    chat_routes,
    docs_routes,
    health,
    underwriting_routes,
)
from .templating import STATIC_DIR, templates

log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", app=app.title)
    try:
        yield
    finally:
        await dispose_engine()
        log.info("shutdown")


def _problem(status_code: int, title: str, detail: str | None, instance: str):
    """Build an RFC7807 application/problem+json response."""
    body = {
        "type": "about:blank",
        "title": title,
        "status": status_code,
        "instance": instance,
    }
    if detail:
        body["detail"] = detail
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type="application/problem+json",
        headers={
            "X-Request-ID": get_request_id(),
            "X-Correlation-ID": get_correlation_id(),
        },
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level="DEBUG" if settings.debug else "INFO")

    app = FastAPI(
        title=settings.app_name,  # MUST match openapi/contract.yaml info.title
        version=settings.app_version,
        description="AutoLoan-DocIntel serving shell (FastAPI + HTMX).",
        lifespan=lifespan,
    )

    # --- middleware (added last == outermost). RequestContext must wrap all. ---
    app.add_middleware(RequestContextMiddleware)

    # --- static + templates ---
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # expose current_user helper to all templates (non-raising)
    templates.env.globals.setdefault("app_name", settings.app_name)

    # --- error handlers -> RFC7807 ---
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        # HTML pages that require auth should bounce to /login, not JSON-401.
        if exc.status_code == 401 and _wants_html(request):
            return RedirectResponse(url="/login", status_code=303)
        return _problem(
            exc.status_code,
            title=str(exc.detail) if exc.detail else "HTTP error",
            detail=None,
            instance=request.url.path,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        return _problem(
            422,
            title="Validation error",
            detail=str(exc.errors()),
            instance=request.url.path,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error("unhandled_exception", error=str(exc), path=request.url.path)
        return _problem(
            500, title="Internal Server Error", detail=None, instance=request.url.path
        )

    # --- routers ---
    app.include_router(health.router)            # /healthz /readyz
    app.include_router(token_router)             # /token (OAuth2 password grant)
    app.include_router(auth_routes.router)       # /login /logout
    app.include_router(chat_routes.router)       # /chat, /v1/chat
    app.include_router(docs_routes.router)       # /documents/{id}
    app.include_router(underwriting_routes.router)  # /underwriting/{id}
    app.include_router(admin_routes.router)      # /admin/catalog

    @app.get("/", include_in_schema=False)
    async def _root():
        # Land on the console; the auth dependency there bounces anon users to /login.
        return RedirectResponse(url="/chat", status_code=307)

    return app


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept


# ASGI entrypoint for `uvicorn app.main:app`
app = create_app()
