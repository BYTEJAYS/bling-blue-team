"""
Red Team notification client.
Fires on confirmed_fraud=True feedback so Red Team can add the pattern to attack corpus.
Best-effort — failure does NOT block the feedback response.
"""
import structlog
from app.core.config import settings

logger = structlog.get_logger()


def notify_confirmed_fraud(
    alert_id: str,
    fraud_type: str | None,
    transaction_id: str,
) -> bool:
    """
    Notify Red Team of a confirmed fraud case.
    Returns True on success, False on any failure.
    """
    if not settings.red_team_endpoint:
        logger.warning("Red Team endpoint not configured — skipping notification")
        return False

    try:
        import httpx
        response = httpx.post(
            settings.red_team_endpoint,
            json={
                "alert_id": alert_id,
                "transaction_id": transaction_id,
                "fraud_type": fraud_type,
                "source": "blue_team_feedback",
            },
            headers={"X-API-Key": settings.red_team_api_key or ""},
            timeout=5.0,
        )
        response.raise_for_status()
        logger.info("Red Team notified", alert_id=alert_id, fraud_type=fraud_type)
        return True
    except Exception as exc:
        logger.error("Red Team notification failed", alert_id=alert_id, error=str(exc))
        return False


def notify_novelty_pattern(novelty_dna: dict) -> bool:
    """
    Send a structural novelty finding to the Red Team sandbox.

    Called when the same novel structural fingerprint appears 10+ times in 7 days,
    indicating systematic use of a new evasion pattern. Separate from notify_confirmed_fraud
    — novelty is unconfirmed; Red Team generates attack variations for developer review.

    Returns True if delivered, False on any failure. Never raises.
    """
    if not settings.red_team_endpoint:
        logger.warning("Red Team endpoint not configured — skipping novelty notification")
        return False

    try:
        import httpx
        response = httpx.post(
            f"{settings.red_team_endpoint.rstrip('/')}/novelty-dna",
            json=novelty_dna,
            headers={"X-API-Key": settings.red_team_api_key or ""},
            timeout=5.0,
        )
        response.raise_for_status()
        logger.info(
            "novelty_dna_sent",
            fingerprint=novelty_dna.get("novelty_fingerprint"),
            occurrences=novelty_dna.get("occurrences_in_7d"),
        )
        return True
    except Exception as exc:
        logger.error(
            "novelty_dna_send_failed",
            fingerprint=novelty_dna.get("novelty_fingerprint"),
            error=str(exc),
        )
        return False
