"""Read-only underwriting view over seeded data.

  * ``GET /underwriting/{application_id}``  PROVIDED — renders the application, its
    borrower/party, vehicle, and the latest underwriting decision (DTI/LTV/PD/risk)
    straight from the seeded Postgres tables. Read-only: no graph involvement.

This is deliberately a plain SQL read so reviewers can sanity-check the seed data
and the candidate's decisions side-by-side. If the DB is unreachable, the page
renders an inline notice rather than 500-ing (keeps the shell demoable offline).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..db import get_session
from ..templating import templates

router = APIRouter(tags=["underwriting"])

_APPLICATION_SQL = text(
    """
    SELECT a.application_id, a.application_no, a.requested_amount,
           a.requested_term_months, a.status, a.product_code,
           a.submitted_at, a.decided_at,
           p.first_name, p.last_name, b.credit_band,
           v.vin, v.fuel_type, v.condition, v.model_year,
           br.name AS branch_name
    FROM loan.loan_application a
    JOIN loan.borrower b   ON b.borrower_id = a.borrower_id
    JOIN loan.party p      ON p.party_id    = b.party_id
    LEFT JOIN loan.vehicle v ON v.vehicle_id = a.vehicle_id
    LEFT JOIN loan.branch br ON br.branch_id = a.branch_id
    WHERE a.application_id = :app_id
    """
)

_DECISION_SQL = text(
    """
    SELECT decision, approved_amount, approved_apr, dti_ratio, ltv_ratio,
           pd_score, risk_rating, decided_at
    FROM loan.underwriting_decision
    WHERE application_id = :app_id
    ORDER BY decided_at DESC
    LIMIT 1
    """
)


@router.get(
    "/underwriting/{application_id}",
    response_class=HTMLResponse,
    name="underwriting_view",
)
async def underwriting_view(
    application_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    try:
        app_row = (
            await db.execute(_APPLICATION_SQL, {"app_id": application_id})
        ).mappings().first()
        if app_row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown application {application_id}"
            )
        decision = (
            await db.execute(_DECISION_SQL, {"app_id": application_id})
        ).mappings().first()
        db_error = None
    except HTTPException:
        raise
    except Exception as exc:  # render an inline notice instead of 500
        app_row, decision, db_error = None, None, str(exc)

    return templates.TemplateResponse(
        request,
        "underwriting.html",
        {
            "user": user,
            "application_id": application_id,
            "application": dict(app_row) if app_row else None,
            "decision": dict(decision) if decision else None,
            "db_error": db_error,
        },
    )
