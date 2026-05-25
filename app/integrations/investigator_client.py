"""
Investigator Dashboard notification client.
Pushes new HIGH_RISK alerts to the investigator's inbox via webhook.
Best-effort — failure does NOT block the /score response.
"""
import structlog
from app.core.config import settings

logger = structlog.get_logger()


def push_alert_notification(
    alert_id: str,
    transaction_id: str,
    score: float,
    action: str,
    gate_fired: str | None,
) -> bool:
    """
    Push alert to Investigator Dashboard webhook.
    Returns True on success, False on any failure.
    """
    if not settings.investigator_webhook_url:
        return False

    try:
        import httpx
        response = httpx.post(
            settings.investigator_webhook_url,
            json={
                "alert_id": alert_id,
                "transaction_id": transaction_id,
                "score": score,
                "action": action,
                "gate_fired": gate_fired,
            },
            headers={"X-API-Key": settings.investigator_webhook_key or ""},
            timeout=3.0,
        )
        response.raise_for_status()
        logger.info("Alert pushed to investigator", alert_id=alert_id)
        return True
    except Exception as exc:
        logger.warning("Investigator push failed", alert_id=alert_id, error=str(exc))
        return False
