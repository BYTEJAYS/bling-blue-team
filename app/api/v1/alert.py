"""
GET /api/v1/alerts/{alert_id}
Returns the full evidence package for a specific alert.
Auth: INVESTIGATOR_API_KEY only.
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import require_investigator_key
from app.evidence.evidence_packager import build_evidence_package

logger = structlog.get_logger()
router = APIRouter(dependencies=[Depends(require_investigator_key)])


@router.get("/api/v1/alerts/{alert_id}")
async def get_alert(
    alert_id: str,
    db: Session = Depends(get_db),
):
    package = build_evidence_package(alert_id, db)
    if package is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    logger.info("Evidence package served", alert_id=alert_id)
    return {"data": package, "error": None}
