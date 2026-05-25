"""
Gate 5: Merchant Terminal Anomaly Gate
Detects fake merchants, POS cash-out channels, and terminal aggregation fraud.
Three independent patterns — any one fires the gate.

This gate has no Neo4j dependency — all data is in PostgreSQL.
"""
import structlog
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = structlog.get_logger()

UNIFORMITY_THRESHOLD = 0.05   # All amounts within 5% of mean = suspicious
MIN_RECEIPTS_FOR_UNIFORMITY = 5
OVERSIZED_COUNT_THRESHOLD = 3
VELOCITY_RATIO_THRESHOLD = 50.0

# Expected maximum transaction amount per MCC code
MCC_EXPECTED_MAX = {
    "5411": 5_000,    # Grocery
    "5812": 3_000,    # Restaurant
    "5912": 2_000,    # Pharmacy
    "7011": 10_000,   # Hotel
    "5999": 5_000,    # Miscellaneous retail
    "5411": 5_000,    # Supermarket
}
DEFAULT_EXPECTED_MAX = 50_000


def run(terminal_id: str | None, db: Session) -> dict:
    """
    Returns:
        {'fired': False} if no terminal anomaly
        {'fired': True, 'gate': 'merchant_terminal_*', 'evidence': {...}} if anomaly
    """
    if not terminal_id:
        return {"fired": False}

    terminal = db.execute(
        text("SELECT mcc_code FROM transactions WHERE merchant_terminal_id = :tid LIMIT 1"),
        {"tid": terminal_id},
    ).fetchone()

    if not terminal:
        return {"fired": False}

    mcc_code = terminal.mcc_code

    receipts_rows = db.execute(
        text("""
            SELECT amount FROM transactions
            WHERE merchant_terminal_id = :tid
              AND timestamp > NOW() - INTERVAL '24 hours'
            ORDER BY timestamp DESC
        """),
        {"tid": terminal_id},
    ).fetchall()

    if not receipts_rows:
        return {"fired": False}

    amounts = [float(r.amount) for r in receipts_rows]

    # Pattern A: Amount uniformity — all receipts suspiciously similar
    if len(amounts) >= MIN_RECEIPTS_FOR_UNIFORMITY:
        mean_amount = sum(amounts) / len(amounts)
        if mean_amount > 0:
            uniformity = sum(abs(a - mean_amount) for a in amounts) / (mean_amount * len(amounts))
            if uniformity < UNIFORMITY_THRESHOLD:
                logger.warning("Merchant terminal gate fired: amount uniformity",
                               terminal_id=terminal_id, uniformity=round(uniformity, 4))
                return {
                    "fired": True,
                    "gate": "merchant_terminal_amount_uniformity",
                    "evidence": {
                        "terminal_id": terminal_id,
                        "mcc": mcc_code,
                        "receipt_count_24h": len(amounts),
                        "mean_amount": mean_amount,
                        "uniformity_score": round(uniformity, 4),
                    },
                }

    # Pattern B: MCC mismatch — receiving far above expected for this category
    expected_max = MCC_EXPECTED_MAX.get(mcc_code or "", DEFAULT_EXPECTED_MAX)
    oversized = [a for a in amounts if a > expected_max * 3]
    if len(oversized) >= OVERSIZED_COUNT_THRESHOLD:
        logger.warning("Merchant terminal gate fired: MCC mismatch",
                       terminal_id=terminal_id, mcc=mcc_code, oversized_count=len(oversized))
        return {
            "fired": True,
            "gate": "merchant_terminal_mcc_mismatch",
            "evidence": {
                "terminal_id": terminal_id,
                "mcc": mcc_code,
                "expected_max_amount": expected_max,
                "oversized_transaction_count": len(oversized),
            },
        }

    # Pattern C: Velocity spike — 50× normal daily volume
    normal_daily = db.execute(
        text("""
            SELECT COALESCE(AVG(daily_count), 0) AS avg_daily FROM (
                SELECT DATE(timestamp) AS day, COUNT(*) AS daily_count
                FROM transactions
                WHERE merchant_terminal_id = :tid
                  AND timestamp > NOW() - INTERVAL '30 days'
                GROUP BY DATE(timestamp)
            ) daily_counts
        """),
        {"tid": terminal_id},
    ).scalar()

    normal_daily = float(normal_daily or 0)
    if normal_daily > 0:
        velocity_ratio = len(amounts) / normal_daily
        if velocity_ratio > VELOCITY_RATIO_THRESHOLD:
            logger.warning("Merchant terminal gate fired: velocity spike",
                           terminal_id=terminal_id, ratio=round(velocity_ratio, 1))
            return {
                "fired": True,
                "gate": "merchant_terminal_velocity_spike",
                "evidence": {
                    "terminal_id": terminal_id,
                    "receipts_today": len(amounts),
                    "normal_daily_avg": normal_daily,
                    "velocity_ratio": round(velocity_ratio, 1),
                },
            }

    return {"fired": False}
