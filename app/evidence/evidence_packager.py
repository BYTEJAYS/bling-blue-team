"""
Assembles the full evidence package returned by GET /api/v1/alerts/{alert_id}.
Combines: FraudScore SHAP, Alert trail_data, STR draft.
"""
import json
from sqlalchemy.orm import Session
from app.models.database import Alert, FraudScore
from app.evidence.str_generator import generate_str_draft


def build_evidence_package(alert_id: str, db: Session) -> dict | None:
    """Returns complete evidence package or None if alert not found."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        return None

    score_row = db.query(FraudScore).filter(
        FraudScore.transaction_id == alert.transaction_id
    ).first()

    trail_data = alert.evidence_package

    shap_values = None
    if score_row and score_row.shap_values:
        try:
            shap_values = json.loads(score_row.shap_values) if isinstance(score_row.shap_values, str) else score_row.shap_values
        except (json.JSONDecodeError, TypeError):
            pass

    str_draft = None
    if alert.action in ("REVIEW", "HIGH_RISK") and score_row:
        str_draft = generate_str_draft(
            transaction_id=alert.transaction_id,
            account_id="",
            amount=float(score_row.tier3_score or score_row.score),
            channel="",
            score=float(alert.score),
            gate_fired=alert.gate,
            shap_explanation=shap_values,
            trail_data=trail_data,
        )

    return {
        "alert_id": alert_id,
        "transaction_id": alert.transaction_id,
        "score": float(alert.score),
        "gate_fired": alert.gate,
        "action": alert.action,
        "status": alert.status,
        "trail_status": alert.trail_status,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "shap_explanation": shap_values,
        "fund_trail": trail_data,
        "str_draft": str_draft,
        "feature_vector": (
            json.loads(score_row.feature_vector)
            if score_row and score_row.feature_vector and isinstance(score_row.feature_vector, str)
            else (score_row.feature_vector if score_row else None)
        ),
    }
