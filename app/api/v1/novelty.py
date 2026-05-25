"""
app/api/v1/novelty.py

Developer-facing endpoints for the Isolation Forest novelty queue.
NOT exposed to investigators or Graph Engine.
Auth: any valid API key (require_any_key) — internal team only.

Endpoints:
  GET  /api/v1/novelty/queue          — list novelty findings for developer review
  GET  /api/v1/novelty/stats          — queue summary stats
  PATCH /api/v1/novelty/{id}/review   — mark a finding as reviewed
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.api.deps import get_db
from app.core.security import require_any_key

router = APIRouter(prefix="/api/v1/novelty", dependencies=[Depends(require_any_key)])

_VALID_STATUSES = {"PENDING_REVIEW", "REVIEWED_NORMAL", "REVIEWED_NEW_FRAUD", "NEW_GATE_ADDED"}


class ReviewUpdate(BaseModel):
    status: str
    developer_notes: str = ""


@router.get("/queue")
def get_novelty_queue(
    status: str = Query(default="PENDING_REVIEW"),
    escalation_only: bool = Query(default=False),
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    """
    Developer review queue of structurally novel transactions.

    Filter by status (default: PENDING_REVIEW) or escalation flag.
    Escalated rows (same fingerprint seen 10+ times) appear first.
    """
    conditions = ["status = :status"]
    params: dict = {"status": status, "limit": limit}

    if escalation_only:
        conditions.append("requires_escalation = TRUE")

    where = " AND ".join(conditions)

    rows = db.execute(
        text(f"""
            SELECT id, transaction_id, account_id, anomaly_score, fraud_score,
                   fraud_action, gate_fired, novelty_fingerprint,
                   fingerprint_occurrences, requires_escalation, status,
                   created_at, reviewed_at
            FROM novelty_queue
            WHERE {where}
            ORDER BY requires_escalation DESC, created_at DESC
            LIMIT :limit
        """),
        params,
    ).fetchall()

    return {
        "count": len(rows),
        "status_filter": status,
        "escalation_only": escalation_only,
        "items": [dict(r._mapping) for r in rows],
    }


@router.get("/stats")
def get_novelty_stats(db: Session = Depends(get_db)):
    """Queue summary statistics. Use for Grafana dashboard or quick health check."""
    row = db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'PENDING_REVIEW') AS pending,
                COUNT(*) FILTER (WHERE requires_escalation = TRUE) AS escalated,
                COUNT(*) FILTER (WHERE status = 'REVIEWED_NEW_FRAUD') AS confirmed_new_patterns,
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') AS last_24h,
                ROUND(AVG(anomaly_score)::numeric, 4) AS avg_anomaly_score,
                COUNT(*) AS total
            FROM novelty_queue
        """)
    ).fetchone()

    return dict(row._mapping) if row else {}


@router.patch("/{novelty_id}/review")
def update_review(
    novelty_id: int,
    body: ReviewUpdate,
    db: Session = Depends(get_db),
):
    """
    Developer marks a novelty finding as reviewed.

    Status must be one of: REVIEWED_NORMAL | REVIEWED_NEW_FRAUD | NEW_GATE_ADDED

    When status=REVIEWED_NEW_FRAUD, a new Tier 2 detection gate should be written
    to catch this structural pattern going forward.
    """
    if body.status not in _VALID_STATUSES - {"PENDING_REVIEW"}:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {sorted(_VALID_STATUSES - {'PENDING_REVIEW'})}",
        )

    result = db.execute(
        text("""
            UPDATE novelty_queue
            SET status = :status,
                developer_notes = :notes,
                reviewed_at = :reviewed_at
            WHERE id = :id
        """),
        {
            "status": body.status,
            "notes": body.developer_notes,
            "reviewed_at": datetime.now(timezone.utc),
            "id": novelty_id,
        },
    )
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Novelty finding {novelty_id} not found")

    return {"novelty_id": novelty_id, "status": body.status, "updated": True}
