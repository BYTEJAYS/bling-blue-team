"""
Immutable audit logger. Every scoring decision must be recorded here.
If audit write fails, the caller should propagate the error — no silent skips.
RBI PMLA Section 12 compliance.
"""
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.database import ModelAudit
from app.core.config import settings
from app.core.exceptions import AuditWriteError


def log_score_event(
    db: Session,
    transaction_id: str,
    event_data: dict,
) -> None:
    try:
        audit_row = ModelAudit(
            event_type="SCORE",
            transaction_id=transaction_id,
            model_version=settings.model_version,
            event_data=event_data,
            event_timestamp=datetime.now(timezone.utc),
        )
        db.add(audit_row)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise AuditWriteError(f"Audit write failed for txn {transaction_id}: {exc}") from exc


def log_feedback_event(
    db: Session,
    alert_id: str,
    transaction_id: str,
    event_data: dict,
) -> None:
    try:
        audit_row = ModelAudit(
            event_type="FEEDBACK",
            transaction_id=transaction_id,
            model_version=settings.model_version,
            event_data={"alert_id": alert_id, **event_data},
            event_timestamp=datetime.now(timezone.utc),
        )
        db.add(audit_row)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise AuditWriteError(f"Audit write failed for alert {alert_id}: {exc}") from exc
