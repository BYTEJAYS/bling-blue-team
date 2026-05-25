"""
Celery async task — fund trail reconstruction.
NEVER call this synchronously — trail takes 5-15 minutes on large graphs.
Always queue via .delay() and return alert_id to the caller immediately.
"""
import structlog
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.graph.queries.trail_queries import trace_forward, trace_backward

logger = structlog.get_logger()


@celery_app.task(
    name="app.evidence.trail_builder.reconstruct_fund_trail",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=900,
    time_limit=1200,
)
def reconstruct_fund_trail(self, transaction_id: str, alert_id: str) -> dict:
    """
    Reconstruct forward + backward fund trail for an alert.
    Updates Alert.trail_status on completion.
    """
    logger.info("Trail reconstruction started", alert_id=alert_id, txn_id=transaction_id)

    try:
        forward_nodes, forward_edges = trace_forward(transaction_id)
        backward_nodes, backward_edges = trace_backward(transaction_id)

        trail = {
            "alert_id": alert_id,
            "transaction_id": transaction_id,
            "forward_hops": len(forward_edges),
            "backward_hops": len(backward_edges),
            "forward_nodes": forward_nodes,
            "forward_edges": forward_edges,
            "backward_nodes": backward_nodes,
            "backward_edges": backward_edges,
            "reconstructed_at": datetime.now(timezone.utc).isoformat(),
        }

        _persist_trail(alert_id, trail)
        logger.info("Trail reconstruction complete", alert_id=alert_id,
                    forward=len(forward_edges), backward=len(backward_edges))
        return trail

    except Exception as exc:
        logger.error("Trail reconstruction failed", alert_id=alert_id, error=str(exc))
        raise self.retry(exc=exc)


def _persist_trail(alert_id: str, trail: dict) -> None:
    """Persist trail to Alert row — separate DB session to avoid main-thread session conflict."""
    try:
        from app.utils.postgres_client import SessionLocal
        from app.models.database import Alert
        import json

        db: Session = SessionLocal()
        try:
            alert = db.query(Alert).filter(Alert.id == alert_id).first()
            if alert:
                alert.evidence_package = trail
                alert.trail_status = "COMPLETE"
                db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.error("Failed to persist trail", alert_id=alert_id, error=str(exc))
