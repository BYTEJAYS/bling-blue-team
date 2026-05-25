"""
Gate 1: Cycle Gate
Detects round-trip paths (Account A → ... → Account A) in the transaction graph.
Any unexplained cycle = score 1.0, action REVIEW.
"""
import structlog
from app.graph.queries.cycle_queries import find_cycles, get_account_type
from app.detection.tier2.legitimacy_filter import check_legitimacy_filters
from app.core.security import pseudonymize

logger = structlog.get_logger()


def run(account_id: str) -> dict:
    """
    Returns:
        {'fired': False} if no cycle or cycle is explained
        {'fired': True, 'gate': 'confirmed_cycle', 'evidence': {...}} if unexplained cycle
    """
    cycles = find_cycles(account_id)
    if not cycles:
        return {"fired": False}

    for cycle in cycles:
        node_ids: list[str] = cycle.get("node_ids", [])
        amounts: list[float] = cycle.get("amounts", [])
        timestamps: list[str] = cycle.get("timestamps", [])
        hops: int = cycle.get("hops", 0)

        if not node_ids or len(amounts) < 2:
            continue

        origin_id = node_ids[0]
        terminus_id = node_ids[-1]
        entry_amount = float(amounts[0]) if amounts else 0
        exit_amount = float(amounts[-1]) if amounts else 0

        # Calculate cycle duration in days
        cycle_duration_days = 0
        if len(timestamps) >= 2:
            from datetime import datetime
            try:
                t_start = datetime.fromisoformat(str(timestamps[0]).replace("Z", "+00:00"))
                t_end = datetime.fromisoformat(str(timestamps[-1]).replace("Z", "+00:00"))
                cycle_duration_days = abs((t_end - t_start).days)
            except (ValueError, TypeError):
                cycle_duration_days = 0

        origin_info = get_account_type(origin_id)
        terminus_info = get_account_type(terminus_id) if origin_id != terminus_id else origin_info

        legitimacy = check_legitimacy_filters(
            origin_id=origin_id,
            terminus_id=terminus_id,
            origin_account_type=origin_info.get("account_type", "SAVINGS"),
            terminus_account_type=terminus_info.get("account_type", "SAVINGS"),
            origin_kyc_occupation=origin_info.get("kyc_occupation"),
            entry_amount=entry_amount,
            exit_amount=exit_amount,
            cycle_duration_days=cycle_duration_days,
            cycle_node_ids=node_ids,
        )

        if not legitimacy["explained"]:
            logger.warning(
                "Cycle gate fired",
                account=pseudonymize(account_id),
                hops=hops,
                entry_amount=entry_amount,
                exit_amount=exit_amount,
            )
            return {
                "fired": True,
                "gate": "confirmed_cycle",
                "evidence": {
                    "hops": hops,
                    "node_ids": node_ids,
                    "amounts": amounts,
                    "entry_amount": entry_amount,
                    "exit_amount": exit_amount,
                    "cycle_duration_days": cycle_duration_days,
                    "legitimacy_checked": True,
                    "legitimacy_reason": None,
                },
            }
        else:
            logger.info(
                "Cycle gate: legitimacy filter explained",
                account=pseudonymize(account_id),
                reason=legitimacy["reason"],
            )

    return {"fired": False}
