"""
Gate 2: Abandoned Sink Gate
Account received burst of funds, retained >80%, then went dormant 30+ days.
Classic mule account signature: receive → forward some → sit → go quiet.
Uses pre-computed nightly Neo4j attributes — no traversal at query time.
"""
import structlog
from app.graph.queries.sink_queries import check_abandoned_sink
from app.core.security import pseudonymize

logger = structlog.get_logger()

# Account types / occupations where cash retention is normal (legit)
_LEGIT_RETENTION_OCCUPATIONS = frozenset({"merchant", "retailer", "shopkeeper"})
_LEGIT_RETENTION_TYPES = frozenset({"JAN_DHAN"})


def run(account_id: str) -> dict:
    """
    Returns:
        {'fired': False} if no sink pattern
        {'fired': True, 'gate': 'abandoned_sink', 'evidence': {...}} if sink detected
    """
    sink_data = check_abandoned_sink(account_id)
    if not sink_data:
        return {"fired": False}

    # Legitimacy check: Jan Dhan accounts and cash-heavy businesses retain legitimately
    account_type = sink_data.get("account_type", "")
    kyc_occupation = sink_data.get("kyc_occupation", "")

    if (account_type in _LEGIT_RETENTION_TYPES
            or kyc_occupation in _LEGIT_RETENTION_OCCUPATIONS):
        logger.info("Sink gate: legitimacy filter explained", account=pseudonymize(account_id),
                    reason="legitimate_cash_retention", account_type=account_type)
        return {"fired": False}

    logger.warning(
        "Sink gate fired",
        account=pseudonymize(account_id),
        inflow=sink_data.get("inflow_last_30d"),
        retention=sink_data.get("retention_ratio"),
        dormant_days=sink_data.get("days_since_last_send"),
    )
    return {
        "fired": True,
        "gate": "abandoned_sink",
        "evidence": {
            "inflow_last_30d": sink_data.get("inflow_last_30d"),
            "retention_ratio": sink_data.get("retention_ratio"),
            "days_since_last_send": sink_data.get("days_since_last_send"),
            "account_age_days": sink_data.get("account_age_days"),
        },
    }
