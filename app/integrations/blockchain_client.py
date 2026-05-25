"""
Blockchain evidence sealing client.
Posts immutable evidence hash to the configured chain endpoint.
Best-effort — failure does NOT block the feedback response.
"""
import hashlib
import json
import structlog
from datetime import datetime, timezone

from app.core.config import settings

logger = structlog.get_logger()


def seal_evidence(
    alert_id: str,
    transaction_id: str,
    confirmed_fraud: bool,
) -> bool:
    """
    Seal alert evidence on the blockchain.
    Returns True on success, False on any failure.
    """
    if not settings.blockchain_endpoint:
        logger.warning("Blockchain endpoint not configured — skipping seal")
        return False

    payload = {
        "alert_id": alert_id,
        "transaction_id": transaction_id,
        "confirmed_fraud": confirmed_fraud,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
    }
    evidence_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()

    try:
        import httpx
        response = httpx.post(
            settings.blockchain_endpoint,
            json={"hash": evidence_hash, "metadata": payload},
            headers={"X-API-Key": settings.blockchain_api_key or ""},
            timeout=10.0,
        )
        response.raise_for_status()
        logger.info("Evidence sealed on blockchain", alert_id=alert_id, hash=evidence_hash[:16])
        return True
    except Exception as exc:
        logger.error("Blockchain seal failed", alert_id=alert_id, error=str(exc))
        return False
