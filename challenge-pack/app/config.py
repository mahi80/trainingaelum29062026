"""Application settings (pydantic-settings).

All values are overridable via environment variables (and a local ``.env``).
These defaults assume the bundled ``docker compose`` stack (Postgres 16 +
pgvector, Redis 7, Ollama). PROVIDED — do not change keys; grading depends on
the contract title/host. The candidate may extend with new optional fields.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- service identity (OpenAPI title MUST match openapi/contract.yaml) ---
    app_name: str = "AutoLoan-DocIntel API"
    app_version: str = "1.0.0"
    environment: str = Field(default="local")
    debug: bool = Field(default=False)

    # --- datastores ---
    database_url: str = Field(
        default="postgresql+asyncpg://aelum:aelum@localhost:5432/autoloan",
        description="Async SQLAlchemy DSN (asyncpg driver).",
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    ollama_host: str = Field(default="http://localhost:11434")

    # --- sessions / auth ---
    secret_key: str = Field(
        default="dev-only-change-me",
        description="Signing/entropy material. MUST be overridden in prod.",
    )
    session_ttl_hours: int = Field(default=12)
    cookie_name: str = Field(default="session")
    cookie_secure: bool = Field(
        default=False, description="Set true behind HTTPS/TLS in prod."
    )

    # --- assets / templates ---
    example_dir: str = Field(
        default="example",
        description="Path (relative to repo root or absolute) to the bundled scans.",
    )

    # --- health probe timeouts (seconds) ---
    healthcheck_timeout_s: float = Field(default=1.5)

    @property
    def session_ttl_seconds(self) -> int:
        return self.session_ttl_hours * 3600


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton accessor used as a FastAPI dependency."""
    return Settings()
