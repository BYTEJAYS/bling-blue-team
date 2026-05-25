"""
Legitimacy filters for the cycle gate.

After a cycle is detected, run these 5 filters IN ORDER.
If any filter explains the cycle → LOG with named reason.
If none explain → ESCALATE with score=1.0.

NEVER skip a filter. NEVER reorder. NEVER suppress silently.
"""
import structlog
from app.graph.queries.cycle_queries import check_kyc_relationship, get_cycle_node_details

logger = structlog.get_logger()

# Account types that create cycles by design
_INTERNAL_ACCOUNT_TYPES = frozenset({"INTERNAL", "TREASURY", "NOSTRO", "VOSTRO"})

# KYC occupations that legitimately issue salary/payroll cycles
_PAYROLL_OCCUPATIONS = frozenset({"employer", "corporate", "payroll_processor"})

# Amount reduction threshold: legitimate partial returns return <70% of sent
_LEGIT_RETURN_RATIO = 0.70


def check_legitimacy_filters(
    origin_id: str,
    terminus_id: str,
    origin_account_type: str,
    terminus_account_type: str,
    origin_kyc_occupation: str | None,
    entry_amount: float,
    exit_amount: float,
    cycle_duration_days: int,
    cycle_node_ids: list[str],
) -> dict:
    """
    Run all 5 legitimacy filters in order.

    Returns:
        {'explained': True, 'reason': 'named_reason'} if cycle is legitimate
        {'explained': False, 'reason': None} if cycle is suspicious
    """

    # Filter 1: Internal / Treasury account
    # Branch GLs, treasury, nostro/vostro accounts create cycles by design
    if (origin_account_type in _INTERNAL_ACCOUNT_TYPES
            or terminus_account_type in _INTERNAL_ACCOUNT_TYPES):
        logger.info("Cycle explained by internal account", reason="internal_transfer",
                    origin=origin_id[:8], terminus=terminus_id[:8])
        return {"explained": True, "reason": "internal_transfer"}

    # Filter 2: KYC-verified relationship between origin and terminus
    # Covers joint accounts, declared family transfers, employer-employee pairs
    if check_kyc_relationship(origin_id, terminus_id):
        logger.info("Cycle explained by KYC relationship", reason="known_relationship",
                    origin=origin_id[:8], terminus=terminus_id[:8])
        return {"explained": True, "reason": "known_relationship"}

    # Filter 3: Salary advance return
    # Corporate/payroll origin, closes within 30 days, return ≤ sent amount
    if (origin_kyc_occupation in _PAYROLL_OCCUPATIONS
            and cycle_duration_days <= 30
            and exit_amount <= entry_amount):
        logger.info("Cycle explained by salary advance", reason="salary_advance_return",
                    duration_days=cycle_duration_days)
        return {"explained": True, "reason": "salary_advance_return"}

    # Filter 4: All-merchant settlement cycle
    # B2B payment chains — all intermediate nodes must be merchants
    if cycle_node_ids:
        node_details = get_cycle_node_details(cycle_node_ids)
        if node_details and all(n.get("is_merchant") for n in node_details):
            logger.info("Cycle explained by merchant settlement", reason="merchant_settlement_cycle",
                        node_count=len(node_details))
            return {"explained": True, "reason": "merchant_settlement_cycle"}

    # Filter 5: Significant amount reduction
    # Legitimate cycles (fees, partial repayments) return <70% of sent amount
    # Money launderers try to return ~100% — this catches them
    if entry_amount > 0 and (exit_amount / entry_amount) < _LEGIT_RETURN_RATIO:
        logger.info("Cycle explained by amount reduction", reason="fee_or_partial_repayment",
                    ratio=round(exit_amount / entry_amount, 3))
        return {"explained": True, "reason": "fee_or_partial_repayment"}

    # No explanation found — escalate
    return {"explained": False, "reason": None}
