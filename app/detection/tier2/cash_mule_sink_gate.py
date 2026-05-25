"""
Gate 4: Cash Mule Sink Detector
Pattern: young account receives large inflow → withdraws >80% via ATM → digital silence.

Uses PostgreSQL ONLY. ATM transactions have no UPI device fingerprint —
ghost node device-matching across ATMs is architecturally impossible.
See agent_docs/gotchas.md: 'ATM transactions have no UPI device fingerprint'.
"""
import structlog
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.security import pseudonymize

logger = structlog.get_logger()

MIN_INFLOW = 50_000        # ₹50K minimum to be worth flagging
CASH_RATIO_THRESHOLD = 0.80  # Must have withdrawn >80% as cash
MAX_DIGITAL_SENDS_AFTER = 2  # Allow up to 2 sends (some mules do small test sends)
MAX_ACCOUNT_AGE_DAYS = 180  # Mule accounts are new

# Account types where cash-in/cash-out is expected
_LEGIT_CASH_HEAVY_TYPES = frozenset({"JAN_DHAN"})
_LEGIT_CASH_HEAVY_OCCUPATIONS = frozenset({
    "vegetable_vendor", "daily_wage", "agricultural_worker", "street_vendor"
})


def run(account_id: str, db: Session) -> dict:
    """
    Returns:
        {'fired': False} if pattern not matched
        {'fired': True, 'gate': 'cash_mule_sink', 'evidence': {...}} if matched
    """
    # Check account age
    account_row = db.execute(
        text("SELECT account_age_days, account_type, kyc_occupation FROM accounts WHERE id = :id"),
        {"id": account_id},
    ).fetchone()

    if not account_row:
        return {"fired": False}

    account_age_days, account_type, kyc_occupation = (
        account_row.account_age_days,
        account_row.account_type,
        account_row.kyc_occupation,
    )

    # Condition 1: Account is young
    if account_age_days is None or account_age_days > MAX_ACCOUNT_AGE_DAYS:
        return {"fired": False}

    # Condition 2: Large inflow in last 7 days
    inflow_7d = db.execute(
        text("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE payee_account_id = :account_id
              AND timestamp > NOW() - INTERVAL '7 days'
        """),
        {"account_id": account_id},
    ).scalar()

    if not inflow_7d or float(inflow_7d) < MIN_INFLOW:
        return {"fired": False}

    inflow_7d = float(inflow_7d)

    # Condition 3: High cash withdrawal ratio within 48h of inflow
    cash_withdrawn_48h = db.execute(
        text("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE account_id = :account_id
              AND channel = 'ATM'
              AND timestamp > NOW() - INTERVAL '48 hours'
        """),
        {"account_id": account_id},
    ).scalar()

    cash_withdrawn_48h = float(cash_withdrawn_48h or 0)
    cash_ratio = cash_withdrawn_48h / inflow_7d if inflow_7d > 0 else 0

    if cash_ratio < CASH_RATIO_THRESHOLD:
        return {"fired": False}

    # Condition 4: Digital silence after withdrawal
    digital_sends_after = db.execute(
        text("""
            SELECT COUNT(*) AS cnt
            FROM transactions
            WHERE account_id = :account_id
              AND channel != 'ATM'
              AND timestamp > (
                  SELECT MAX(timestamp) FROM transactions
                  WHERE account_id = :account_id AND channel = 'ATM'
              )
        """),
        {"account_id": account_id},
    ).scalar()

    digital_sends_after = int(digital_sends_after or 0)

    if digital_sends_after > MAX_DIGITAL_SENDS_AFTER:
        return {"fired": False}

    # Legitimacy check: Jan Dhan accounts and known cash-heavy occupations
    if (account_type in _LEGIT_CASH_HEAVY_TYPES
            or kyc_occupation in _LEGIT_CASH_HEAVY_OCCUPATIONS):
        logger.info("Cash mule gate: legitimate cash-heavy account",
                    account=pseudonymize(account_id), account_type=account_type)
        return {"fired": False}

    logger.warning(
        "Cash mule sink gate fired",
        account=pseudonymize(account_id),
        account_age_days=account_age_days,
        inflow_7d=inflow_7d,
        cash_ratio=round(cash_ratio, 3),
        digital_sends_after=digital_sends_after,
    )
    return {
        "fired": True,
        "gate": "cash_mule_sink",
        "evidence": {
            "account_age_days": account_age_days,
            "inflow_7d": inflow_7d,
            "cash_withdrawn_48h": cash_withdrawn_48h,
            "cash_ratio": round(cash_ratio, 3),
            "digital_sends_after_withdrawal": digital_sends_after,
        },
    }
