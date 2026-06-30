"""Live auth + Postgres-session conformance tests.

Skipped entirely unless APP_BASE_URL is set. Several assertions additionally need
DATABASE_URL (to inspect app.session and app.user_account); those skip individually
if it is absent.

Verifies the firm's auth posture:
  * a protected route rejects the anonymous caller (401 or auth redirect)
  * POST /login sets an HttpOnly session cookie AND creates exactly one app.session row
  * logout (GET/POST /logout) deletes that session row and clears the cookie
  * stored password is argon2 (hash starts with $argon2)
  * OAuth2 password flow: POST /token issues a bearer usable on a protected route

Credentials come from env (defaults match the reference seed):
    GRADER_USERNAME (default: grader)
    GRADER_PASSWORD (default: grader-password)

Run:  APP_BASE_URL=http://localhost:8000 pytest grading-kit/harness/test_auth_session.py -v
"""
from __future__ import annotations

import os
import uuid

import pytest

USERNAME = os.environ.get("GRADER_USERNAME", "grader")
PASSWORD = os.environ.get("GRADER_PASSWORD", "grader-password")

# candidate apps differ slightly in routing; probe a small set and use what answers.
PROTECTED_PATHS = ("/v1/chat", "/app", "/", "/dashboard", "/me")
LOGIN_PATHS = ("/login", "/auth/login")
LOGOUT_PATHS = ("/logout", "/auth/logout")
TOKEN_PATHS = ("/token", "/auth/token", "/oauth/token")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _session_cookie_name(client) -> str:
    """Best-effort: the contract names the cookie 'session'; allow common variants."""
    for name in ("session", "session_id", "sid"):
        if name in client.cookies:
            return name
    # fall back to whatever single cookie the server set
    names = list(client.cookies.keys())
    return names[0] if names else "session"


def _count_sessions(database_url: str, user_id=None) -> int:
    import psycopg

    sql = "SELECT count(*) FROM app.session"
    params: tuple = ()
    if user_id is not None:
        sql += " WHERE user_id = %s"
        params = (user_id,)
    with psycopg.connect(database_url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


def _user_id_for(database_url: str, username: str):
    import psycopg

    with psycopg.connect(database_url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id FROM app.user_account WHERE username = %s", (username,))
        row = cur.fetchone()
        return row[0] if row else None


def _do_login(client):
    """Try form login at the known login paths; return the first non-404 response."""
    last = None
    for path in LOGIN_PATHS:
        resp = client.post(
            path,
            data={"username": USERNAME, "password": PASSWORD},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        last = resp
        if resp.status_code != 404:
            return path, resp
    return (LOGIN_PATHS[0], last)


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_protected_route_requires_auth(http_client):
    """An anonymous request to a protected route must not return 200 with content."""
    saw_protected = False
    for path in PROTECTED_PATHS:
        if path == "/v1/chat":
            resp = http_client.post(path, json={"message": "hi", "stream": False})
        else:
            resp = http_client.get(path)
        if resp.status_code == 404:
            continue
        saw_protected = True
        assert resp.status_code in (401, 403, 302, 303, 307, 308), (
            f"{path} returned {resp.status_code} to an anonymous caller "
            f"(expected 401/403 or an auth redirect)"
        )
        # a redirect should point at a login screen
        if resp.status_code in (302, 303, 307, 308):
            loc = resp.headers.get("location", "").lower()
            assert "login" in loc or "auth" in loc, f"redirect to non-login location: {loc}"
        break
    if not saw_protected:
        pytest.skip("no recognised protected route answered (non-404)")


def test_login_sets_httponly_cookie_and_one_session_row(http_client, database_url):
    """POST /login -> HttpOnly cookie + exactly one app.session row for the user."""
    user_id = _user_id_for(database_url, USERNAME)
    if user_id is None:
        pytest.skip(f"seed user '{USERNAME}' not found in app.user_account")

    before = _count_sessions(database_url, user_id)

    path, resp = _do_login(http_client)
    assert resp is not None and resp.status_code in (200, 302, 303, 204), (
        f"login at {path} failed: {resp.status_code if resp else 'no response'}"
    )

    # cookie present + HttpOnly
    set_cookie = " ".join(resp.headers.get_list("set-cookie")) if hasattr(
        resp.headers, "get_list"
    ) else resp.headers.get("set-cookie", "")
    assert set_cookie, "login did not emit a Set-Cookie header"
    assert "httponly" in set_cookie.lower(), "session cookie is not HttpOnly"

    # exactly one new session row for this user
    after = _count_sessions(database_url, user_id)
    assert after == before + 1, (
        f"expected exactly one new app.session row (before={before}, after={after})"
    )


def test_logout_deletes_session_row(http_client, database_url):
    user_id = _user_id_for(database_url, USERNAME)
    if user_id is None:
        pytest.skip(f"seed user '{USERNAME}' not found")

    _do_login(http_client)
    mid = _count_sessions(database_url, user_id)
    assert mid >= 1, "no session row after login; cannot test logout"

    logged_out = False
    for path in LOGOUT_PATHS:
        resp = http_client.post(path)
        if resp.status_code == 404:
            resp = http_client.get(path)
        if resp.status_code != 404:
            logged_out = True
            break
    if not logged_out:
        pytest.skip("no logout route answered (non-404)")

    after = _count_sessions(database_url, user_id)
    assert after < mid, f"logout did not delete the session row (mid={mid}, after={after})"


def test_password_stored_is_argon2(database_url):
    """The seeded user's password_hash must be an argon2 PHC string."""
    import psycopg

    with psycopg.connect(database_url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT password_hash FROM app.user_account WHERE username = %s", (USERNAME,)
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip(f"seed user '{USERNAME}' not found")
    pw_hash = row[0] or ""
    assert pw_hash.startswith("$argon2"), (
        f"password_hash is not argon2 (starts with {pw_hash[:12]!r})"
    )


def test_oauth2_token_flow_issues_usable_bearer(http_client):
    """POST /token (OAuth2 password grant) returns a bearer that authorises a protected call."""
    token = None
    token_path = None
    for path in TOKEN_PATHS:
        resp = http_client.post(
            path,
            data={"grant_type": "password", "username": USERNAME, "password": PASSWORD},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code == 404:
            continue
        token_path = path
        if resp.status_code == 200:
            try:
                token = resp.json().get("access_token")
            except Exception:
                token = None
        break
    if token_path is None:
        pytest.skip("no /token endpoint answered (non-404)")
    assert token, f"{token_path} did not return an access_token (status check the body)"

    # the bearer should authorise a protected call on a fresh, cookieless client
    import httpx

    base = str(http_client.base_url)
    corr = str(uuid.uuid4())
    with httpx.Client(base_url=base, timeout=10.0) as bare:
        resp = bare.post(
            "/v1/chat",
            json={"message": "ping", "stream": False},
            headers={"Authorization": f"Bearer {token}", "X-Correlation-ID": corr},
        )
    assert resp.status_code != 401, "bearer token was rejected on a protected route"
    assert resp.status_code != 404, "/v1/chat not implemented; cannot validate bearer"
