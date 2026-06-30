"""Admin catalog: list schemas/tables + the bundled page index.

  * ``GET /admin/catalog``  PROVIDED — lists tables (from information_schema) grouped
    by schema with approximate row counts, plus the page index parsed from the
    bundled ``example/manifest.jsonl``. Useful for reviewers to eyeball the data
    model and the scan set. Restricted to the ``admin`` role.

Read-only and resilient: if Postgres is unreachable the table list shows an inline
error but the page-index section (file-backed) still renders.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import require_role
from ..db import get_session
from ..templating import templates
from .docs_routes import _manifest

router = APIRouter(tags=["admin"])

_TABLES_SQL = text(
    """
    SELECT t.table_schema, t.table_name,
           COALESCE(c.reltuples::bigint, 0) AS approx_rows
    FROM information_schema.tables t
    LEFT JOIN pg_class c
      ON c.relname = t.table_name
    LEFT JOIN pg_namespace n
      ON n.oid = c.relnamespace AND n.nspname = t.table_schema
    WHERE t.table_schema IN ('ref','loan','doc','app')
      AND t.table_type = 'BASE TABLE'
    ORDER BY t.table_schema, t.table_name
    """
)


@router.get("/admin/catalog", response_class=HTMLResponse, name="admin_catalog")
async def admin_catalog(
    request: Request,
    user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_session),
):
    try:
        rows = (await db.execute(_TABLES_SQL)).mappings().all()
        by_schema: dict[str, list[dict]] = {}
        for r in rows:
            by_schema.setdefault(r["table_schema"], []).append(dict(r))
        db_error = None
    except Exception as exc:
        by_schema, db_error = {}, str(exc)

    manifest = _manifest()
    # Compact page index: one entry per page with its doc + class.
    page_index = [
        {
            "page_no": m.get("page_no"),
            "image": m.get("image"),
            "doc_id": m.get("doc_id"),
            "doc_class": m.get("doc_class"),
            "split": m.get("split"),
        }
        for m in manifest
    ]

    return templates.TemplateResponse(
        request,
        "catalog.html",
        {
            "user": user,
            "by_schema": by_schema,
            "db_error": db_error,
            "page_index": page_index,
            "page_count": len(page_index),
        },
    )
