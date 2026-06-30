"""Liveness + readiness probes.

PROVIDED — real, working code (matches openapi/contract.yaml).

  * ``GET /healthz``  -> always 200 if the process is up (liveness).
  * ``GET /readyz``   -> 200 only when Postgres + Redis + Ollama are reachable
                         within a short timeout; otherwise 503 with a per-dependency
                         status map. Probes run concurrently and never raise.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..db import ping as pg_ping

router = APIRouter(tags=["ops"])


@router.get("/healthz", name="healthz")
async def healthz():
    return {"status": "ok"}


async def _check_redis(url: str, timeout: float) -> bool:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(url, socket_connect_timeout=timeout)
        try:
            return bool(await asyncio.wait_for(client.ping(), timeout=timeout))
        finally:
            await client.aclose()
    except Exception:
        return False


async def _check_pg(timeout: float) -> bool:
    try:
        return await asyncio.wait_for(pg_ping(), timeout=timeout)
    except Exception:
        return False


async def _check_ollama(host: str, timeout: float) -> bool:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{host.rstrip('/')}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


@router.get("/readyz", name="readyz")
async def readyz():
    settings = get_settings()
    t = settings.healthcheck_timeout_s

    pg_ok, redis_ok, ollama_ok = await asyncio.gather(
        _check_pg(t),
        _check_redis(settings.redis_url, t),
        _check_ollama(settings.ollama_host, t),
    )

    checks = {"postgres": pg_ok, "redis": redis_ok, "ollama": ollama_ok}
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
