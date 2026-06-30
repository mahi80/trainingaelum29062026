-- 30_app.sql — application auth + server-side sessions (sessions live in Postgres)
CREATE TABLE app.role (
  code  TEXT PRIMARY KEY,
  label TEXT NOT NULL
);
CREATE TABLE app.user_account (
  user_id       BIGSERIAL PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  email         TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,                 -- argon2id
  role          TEXT NOT NULL REFERENCES app.role(code),
  is_active     BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE app.session (
  session_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id       BIGINT NOT NULL REFERENCES app.user_account(user_id) ON DELETE CASCADE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL,
  ip_addr       INET,
  user_agent    TEXT,
  data          JSONB NOT NULL DEFAULT '{}'   -- CSRF token, flash, light prefs
);
CREATE INDEX ix_session_user ON app.session(user_id);
CREATE INDEX ix_session_expires ON app.session(expires_at);
