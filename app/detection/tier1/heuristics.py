"""
Tier 1: Fast heuristic classification. Target: 5ms.
Only touches Redis. Never queries Neo4j or PostgreSQL.

Returns exactly one of: FAST_CLEAN | UNCERTAIN | SUSPICIOUS
Never returns a boolean or any other value.
"""
from datetime import datetime, timezone
from typing import Literal

from app.utils.redis_client import velocity_1h, velocity_24h
from app.models.schemas import TransactionScoreRequest

Tier1Result = Literal["FAST_CLEAN", "UNCERTAIN", "SUSPICIOUS"]

# Amounts just below RBI reporting thresholds
_THRESHOLD_BANDS = [
    (49_000, 50_000),
    (99_000, 1_00_000),
    (9_90_000, 10_00_000),
]

# KYC occupations that do high-frequency cash-heavy work — don't flag velocity alone
_HIGH_FREQUENCY_OCCUPATIONS = frozenset({"gig_worker", "freelancer", "delivery", "merchant", "retailer"})


def _is_night(ts: datetime) -> bool:
    """11pm–5am UTC. Adjust for IST (+5:30) in production."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    hour = ts.hour
    return hour >= 23 or hour < 5


def _near_threshold(amount: float) -> bool:
    """Amount suspiciously close to a reporting threshold."""
    for low, high in _THRESHOLD_BANDS:
        if low <= amount < high:
            return True
    return False


def tier1_classify(
    txn: TransactionScoreRequest,
    account_age_days: int,
    avg_amount_30d: float,
    payee_in_known_contacts: bool,
    payee_account_age_days: int | None,
    payee_vpa_age_days: int | None,
    kyc_occupation: str | None,
) -> tuple[Tier1Result, list[str]]:
    """
    Classify a transaction into FAST_CLEAN | UNCERTAIN | SUSPICIOUS.

    Args:
        txn: The incoming transaction request
        account_age_days: Sender account age in days
        avg_amount_30d: Sender's 30-day average transaction amount
        payee_in_known_contacts: Whether payee is in sender's known contacts
        payee_account_age_days: Age of payee account (None if unknown)
        payee_vpa_age_days: Age of payee VPA in days (None if no VPA)
        kyc_occupation: Sender's KYC occupation (None if unknown)

    Returns:
        (result, flags) where flags lists triggered signals
    """
    amount = float(txn.amount)
    flags: list[str] = []

    # ── Hard suspicious signals — any one makes this SUSPICIOUS ──────────────

    if payee_vpa_age_days is not None and payee_vpa_age_days < 7:
        flags.append("new_vpa")

    if _is_night(txn.timestamp):
        flags.append("night")

    vel_1h = velocity_1h(txn.account_id)
    if vel_1h > 5 and kyc_occupation not in _HIGH_FREQUENCY_OCCUPATIONS:
        flags.append("velocity_spike")

    if _near_threshold(amount):
        flags.append("threshold_proximity")

    if payee_account_age_days is not None and payee_account_age_days < 14:
        flags.append("new_payee_account")

    if avg_amount_30d > 0 and amount > avg_amount_30d * 5:
        flags.append("amount_spike")

    if flags:
        return "SUSPICIOUS", flags

    # ── Clearly clean — all conditions must hold ──────────────────────────────
    vel_24h = velocity_24h(txn.account_id)

    if (
        account_age_days > 365
        and avg_amount_30d > 0
        and amount < avg_amount_30d * 2
        and payee_in_known_contacts
        and not _is_night(txn.timestamp)
        and vel_24h < 10
    ):
        return "FAST_CLEAN", []

    # ── Everything else: not suspicious, not clearly clean ───────────────────
    # First-time payees, moderate amounts, new accounts → graph gate review
    return "UNCERTAIN", []
