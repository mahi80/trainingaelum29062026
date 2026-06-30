"""Request-context middleware: request_id + correlation_id + access logging.

PROVIDED — real, working code.

For every request this middleware:
  * mints a ``request_id`` (uuid4) unique to this request,
  * reads ``X-Correlation-ID`` from the client or mints one if absent,
  * binds both into structlog's contextvars so *every* log line in the request
    automatically carries them,
  * stores both in ``request.state`` and echoes them back as response headers,
  * emits exactly one structured JSON access log per request containing
    method / path / status / latency_ms (never bodies or PII).

Header constants and ContextVars are exported for use by handlers/SSE.
"""
from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

CORRELATION_HEADER = "X-Correlation-ID"
REQUEST_ID_HEADER = "X-Request-ID"

# ContextVars are readable anywhere in the async call stack of a request.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")

_log = structlog.get_logger("access")


def get_request_id() -> str:
    return request_id_ctx.get()


def get_correlation_id() -> str:
    return correlation_id_ctx.get()


def _new_id() -> str:
    return uuid.uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind request/correlation ids and emit one access-log event per request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        request_id = _new_id()
        correlation_id = request.headers.get(CORRELATION_HEADER) or _new_id()

        # bind for the lifetime of this request (cleared in finally)
        request_id_ctx.set(request_id)
        correlation_id_ctx.set(correlation_id)
        structlog.contextvars.bind_contextvars(
            request_id=request_id, correlation_id=correlation_id
        )

        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        start = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            response.headers[CORRELATION_HEADER] = correlation_id
            return response
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            # Path only (no query string) and no bodies -> avoids logging PII.
            _log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status_code,
                latency_ms=latency_ms,
                client=request.client.host if request.client else None,
            )
            structlog.contextvars.clear_contextvars()
