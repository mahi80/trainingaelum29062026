# `app/` — FastAPI + HTMX serving shell

The **provided** serving scaffold for AutoLoan-DocIntel. Auth, sessions, logging,
middleware, and HTMX wiring are **real, working code**. The places where you wire
in real model/graph output are marked `TODO(candidate)`.

## Run

```bash
# from challenge-pack/
pip install -r app/requirements.txt
# vendor the two JS files (see app/static/README.md), then:
uvicorn app.main:app --reload --port 8000
```

The app needs `src/` (graph + LLM) and the repo `db/`, `example/`, `openapi/`
alongside it — run from the `challenge-pack/` root so imports (`src.graph`,
`example/`) resolve. Configure datastores via env (see `app/config.py`); defaults
match the bundled `docker compose` stack.

## Layout

| path                              | purpose | status |
|-----------------------------------|---------|--------|
| `main.py`                         | `create_app()` factory, middleware order, RFC7807 handlers, router includes | PROVIDED |
| `config.py`                       | pydantic-settings `Settings` (DB/Redis/Ollama, session TTL, cookie) | PROVIDED |
| `db.py`                           | async SQLAlchemy engine + `get_session` dep (asyncpg), `ping()` | PROVIDED |
| `logging_conf.py`                 | structlog JSON renderer, `configure_logging()` | PROVIDED |
| `templating.py`                   | shared Jinja2 `templates` instance | PROVIDED |
| `middleware/request_context.py`   | request_id + correlation_id ContextVars, one JSON access log/request | PROVIDED |
| `auth/security.py`                | argon2id hash/verify, OAuth2 `POST /token` -> opaque session bearer | PROVIDED |
| `auth/session.py`                 | Postgres `app.session` CRUD: create/load/touch/delete | PROVIDED |
| `auth/deps.py`                    | `get_current_user` (cookie OR bearer), `require_role` | PROVIDED |
| `routers/auth_routes.py`          | `/login` (GET/POST), `/logout` | PROVIDED |
| `routers/chat_routes.py`          | `/chat` console; `POST /v1/chat` SSE transport | PROVIDED transport · **CANDIDATE** wires graph output |
| `routers/docs_routes.py`          | `/documents/{id}` viewer + image | PROVIDED · **CANDIDATE** fills overlays |
| `routers/underwriting_routes.py`  | `/underwriting/{id}` read-only over seed | PROVIDED |
| `routers/admin_routes.py`         | `/admin/catalog` tables + page index | PROVIDED |
| `routers/health.py`               | `/healthz`, `/readyz` (pg+redis+ollama) | PROVIDED |
| `templates/*.html`                | base, login, chat, document_viewer, underwriting, catalog | PROVIDED |
| `static/`                         | vendor `htmx.min.js` + `sse.js` here | see static/README.md |

## What's real vs. what you implement

**Real (don't reimplement):**
- argon2id password hashing + verify with opportunistic rehash.
- OAuth2 password grant at `POST /token` issuing an **opaque bearer that maps 1:1
  to a Postgres `app.session` row** (no JWT). The same value is the HttpOnly,
  SameSite=Lax `session` cookie.
- `get_current_user` resolving **cookie OR `Authorization: Bearer`**, sliding-safe
  `touch` that rolls `last_seen_at` and enforces absolute `expires_at`.
- structlog JSON logs with `request_id` + `correlation_id` bound per request; one
  access-log line per request (method/path/status/latency_ms) — **never bodies/PII**.
- middleware order: `RequestContextMiddleware` is outermost so it wraps every
  response, including errors.
- RFC7807 `application/problem+json` error responses.
- `/healthz` liveness and `/readyz` readiness (concurrent pg/redis/ollama probes
  with short timeouts).

**You wire (`TODO(candidate)`):**
- `POST /v1/chat`: stream real **token deltas**, `route`, generated `sql`,
  `citations`, and `decision` from your implemented LangGraph nodes. The transport
  (SSE framing, heartbeats, `NotImplementedError` -> friendly `error` event) is
  done; the page already listens for the `token`/`meta`/`citation`/`error`/`done`
  events.
- document viewer overlays: render real cell bounding boxes from your extractions.

## Auth flows

- **Browser (HTMX):** `POST /login` -> session row + `session` cookie -> pages.
- **API / OpenAPI "Authorize":** `POST /token` (password grant) -> `access_token`
  (the session id) -> send as `Authorization: Bearer <token>`.

Both hit the same `app.session` table; logout (`GET /logout`) deletes the row and
clears the cookie.
