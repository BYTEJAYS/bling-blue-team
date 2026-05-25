"""
Indian Context Adjuster.
Applied AFTER raw Tier 3 score, BEFORE threshold comparison.
Multiplies raw score by a context factor. Cap at 1.0.
Logs all applied adjustments for audit trail.
"""
from datetime import datetime, timezone


def apply_indian_context(
    raw_score: float,
    txn_amount: float,
    txn_timestamp: datetime,
    txn_channel: str,
    payee_vpa_age_days: float | None,
    account_type: str | None,
    kyc_age: int | None,
    kyc_occupation: str | None,
    has_festival_gifting_history: bool = False,
    daily_txn_count: int = 0,
) -> tuple[float, dict]:
    """
    Returns:
        (adjusted_score, applied_adjustments)
        applied_adjustments: dict of segment -> factor applied
    """
    ts = txn_timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    is_night = ts.hour >= 23 or ts.hour < 5
    is_festival = (ts.month == 10) or (ts.month == 11 and ts.day <= 15)
    is_daytime = 11 <= ts.hour <= 23

    adjustments: dict[str, float] = {}
    score = raw_score

    # Festival season: Oct 1 – Nov 15 (Navratri + Diwali)
    # Small gifts to new payees are normal during this period
    if (is_festival
            and txn_amount < 5_000
            and has_festival_gifting_history
            and payee_vpa_age_days is not None
            and payee_vpa_age_days < 30):
        adjustments["festival_gifting"] = 0.70
        score *= 0.70

    # Gig workers: high-frequency same-channel daytime transactions are normal
    if (daily_txn_count > 10
            and txn_channel == "UPI"
            and kyc_occupation in ("gig_worker", "freelancer", "delivery")
            and is_daytime):
        adjustments["gig_worker_velocity"] = 0.85
        score *= 0.85

    # Jan Dhan: cash-in/cash-out is expected — suppress KYC mismatch signals
    if account_type == "JAN_DHAN":
        adjustments["jan_dhan_cash_pattern"] = 0.75
        score *= 0.75

    # Merchant: round amounts and evening batch settlements are normal
    if (kyc_occupation in ("merchant", "retailer", "shopkeeper")
            and txn_amount == round(txn_amount, -2)  # round to nearest 100
            and 18 <= ts.hour <= 23):
        adjustments["merchant_batch_settlement"] = 0.80
        score *= 0.80

    # Senior citizen amplification — they are primary digital arrest targets
    if kyc_age is not None and kyc_age > 60:
        if is_night:
            adjustments["senior_night_amplification"] = 1.50
            score *= 1.50
        if payee_vpa_age_days is not None and payee_vpa_age_days < 7:
            adjustments["senior_new_vpa_amplification"] = 1.30
            score *= 1.30

    adjusted_score = min(score, 1.0)
    return adjusted_score, adjustments
