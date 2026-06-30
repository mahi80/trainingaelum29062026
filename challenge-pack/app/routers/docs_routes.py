"""Document viewer.

  * ``GET /documents/{doc_id}``        PROVIDED — viewer page rendering an example
                                        scan with an (empty) overlay layer.
  * ``GET /documents/{doc_id}/image``  PROVIDED — serves the underlying page image
                                        from the bundled ``example/`` set.

The viewer reads the bundled manifest to map ``doc_id`` -> its first page image so
the page renders with no database required. Bounding-box overlays are a placeholder
the candidate fills from real extractions (doc.extraction_cell / GT cells.json).

TODO(candidate): fetch persisted cell bboxes for this document/page and emit them
as overlay rectangles (the template already has an absolutely-positioned overlay
div keyed to the image's natural size).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from ..auth.deps import get_current_user
from ..config import get_settings
from ..templating import templates

router = APIRouter(tags=["documents"])


def _example_root() -> Path:
    """Resolve the bundled example dir (settings path or repo-relative)."""
    p = Path(get_settings().example_dir)
    if not p.is_absolute():
        # app/ -> challenge-pack/ -> example/
        p = Path(__file__).resolve().parents[2] / p
    return p


@lru_cache(maxsize=1)
def _manifest() -> list[dict]:
    path = _example_root() / "manifest.jsonl"
    rows: list[dict] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _first_page_for_doc(doc_id: str) -> Optional[dict]:
    pages = [r for r in _manifest() if r.get("doc_id") == doc_id]
    if not pages:
        return None
    return sorted(pages, key=lambda r: r.get("page_in_doc", 0))[0]


@router.get("/documents/{doc_id}", response_class=HTMLResponse, name="document_viewer")
async def document_viewer(
    doc_id: str, request: Request, user: dict = Depends(get_current_user)
):
    page = _first_page_for_doc(doc_id)
    if page is None:
        raise HTTPException(status_code=404, detail=f"Unknown doc_id {doc_id}")
    return templates.TemplateResponse(
        request,
        "document_viewer.html",
        {
            "user": user,
            "doc_id": doc_id,
            "page": page,
            "image_url": f"/documents/{doc_id}/image",
            # TODO(candidate): pass real overlay cells here.
            "overlays": [],
        },
    )


@router.get("/documents/{doc_id}/image", name="document_image")
async def document_image(doc_id: str, user: dict = Depends(get_current_user)):
    page = _first_page_for_doc(doc_id)
    if page is None:
        raise HTTPException(status_code=404, detail=f"Unknown doc_id {doc_id}")
    img = _example_root() / "images" / page["image"]
    if not img.exists():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(str(img), media_type="image/jpeg")
