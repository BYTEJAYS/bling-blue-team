"""
POST /api/v1/feedback
Investigator submits verdict → triggers online learning + blockchain seal + red team notification.
Auth: INVESTIGATOR_API_KEY only.
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import json

from app.api.deps import get_db
from app.core.security import require_investigator_key
from app.core.exceptions import AuditWriteError
from app.models.schemas import InvestigatorFeedbackRequest, FeedbackResponse
from app.models.database import Alert, FraudScore, FeedbackLog
from app.utils.audit_logger import log_feedback_event
from app.utils.metrics import feedback_received_total

logger = structlog.get_logger()
router = APIRouter(dependencies=[Depends(require_investigator_key)])


@router.post("/api/v1/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    payload: InvestigatorFeedbackRequest,
    db: Session = Depends(get_db),
):
    alert = db.query(Alert).filter(Alert.id == payload.alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    score_row = db.query(FraudScore).filter(
        FraudScore.transaction_id == alert.transaction_id
    ).first()

    # 1. Online learning warm-start update
    model_updated = False
    if score_row and score_row.feature_vector:
        try:
            from app.detection.tier3.online_learning import update_model
            fv = (
                json.loads(score_row.feature_vector)
                if isinstance(score_row.feature_vector, str)
                else score_row.feature_vector
            )
            model_updated = update_model(
                feature_vector=fv,
                confirmed_fraud=payload.confirmed_fraud,
                alert_id=payload.alert_id,
            )
        except Exception as exc:
            logger.error("Online learning update failed", alert_id=payload.alert_id, error=str(exc))

    # 2. Blockchain seal (async, best-effort)
    blockchain_sealed = False
    try:
        from app.integrations.blockchain_client import seal_evidence
        blockchain_sealed = seal_evidence(
            alert_id=payload.alert_id,
            transaction_id=alert.transaction_id,
            confirmed_fraud=payload.confirmed_fraud,
        )
    except Exception as exc:
        logger.warning("Blockchain seal failed", alert_id=payload.alert_id, error=str(exc))

    # 3. Red team notification (async, best-effort)
    red_team_notified = False
    if payload.confirmed_fraud:
        try:
            from app.integrations.red_team_client import notify_confirmed_fraud
            red_team_notified = notify_confirmed_fraud(
                alert_id=payload.alert_id,
                fraud_type=payload.fraud_type,
                transaction_id=alert.transaction_id,
            )
        except Exception as exc:
            logger.warning("Red team notification failed", alert_id=payload.alert_id, error=str(exc))

    # 4. Update alert status
    alert.status = "CONFIRMED_FRAUD" if payload.confirmed_fraud else "FALSE_POSITIVE"
    db.add(FeedbackLog(
        alert_id=payload.alert_id,
        transaction_id=alert.transaction_id,
        label=1 if payload.confirmed_fraud else 0,
    ))

    # 5. Audit — must succeed before returning
    log_feedback_event(db, payload.alert_id, alert.transaction_id, {
        "confirmed_fraud": payload.confirmed_fraud,
        "fraud_type": payload.fraud_type,
        "model_updated": model_updated,
        "blockchain_sealed": blockchain_sealed,
        "red_team_notified": red_team_notified,
    })

    db.commit()

    feedback_received_total.labels(
        confirmed=str(payload.confirmed_fraud)
    ).inc()

    logger.info("Feedback processed",
                alert_id=payload.alert_id,
                confirmed=payload.confirmed_fraud,
                model_updated=model_updated,
                blockchain_sealed=blockchain_sealed,
                red_team=red_team_notified)

    return FeedbackResponse(
        alert_id=payload.alert_id,
        model_updated=model_updated,
        blockchain_sealed=blockchain_sealed,
        red_team_notified=red_team_notified,
    )
