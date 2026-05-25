"""
Gate 3: Bipartite Core Gate
Many senders → few receivers with high density (>0.7) = mule aggregator network.
Classic smurfing pattern: victims send to one aggregator account.
"""
import structlog
from app.graph.queries.bipartite_queries import check_bipartite_core
from app.core.security import pseudonymize

logger = structlog.get_logger()

# Accounts that legitimately receive from many senders
_LEGIT_AGGREGATOR_OCCUPATIONS = frozenset({
    "salary_processor", "payroll_processor", "insurance_company",
    "tax_collector", "utility_provider",
})


def run(account_id: str) -> dict:
    """
    Returns:
        {'fired': False} if no bipartite pattern
        {'fired': True, 'gate': 'bipartite_core', 'evidence': {...}} if detected
    """
    bipartite_data = check_bipartite_core(account_id)
    if not bipartite_data:
        return {"fired": False}

    kyc_occupation = bipartite_data.get("receiver_kyc_occupation", "")
    is_merchant = bipartite_data.get("receiver_is_merchant", False)

    # Legitimate aggregators: payroll processors, insurance companies, etc.
    if kyc_occupation in _LEGIT_AGGREGATOR_OCCUPATIONS:
        logger.info("Bipartite gate: legitimacy filter explained",
                    account=pseudonymize(account_id), reason="legitimate_aggregator")
        return {"fired": False}

    # Diverse merchant with many customers is legitimate
    if is_merchant and bipartite_data.get("density", 1.0) < 0.85:
        logger.info("Bipartite gate: legitimate merchant density",
                    account=pseudonymize(account_id), density=bipartite_data.get("density"))
        return {"fired": False}

    logger.warning(
        "Bipartite gate fired",
        account=pseudonymize(account_id),
        sender_count=bipartite_data.get("sender_count"),
        density=bipartite_data.get("density"),
    )
    return {
        "fired": True,
        "gate": "bipartite_core",
        "evidence": {
            "sender_count": bipartite_data.get("sender_count"),
            "density": bipartite_data.get("density"),
            "sender_ids": bipartite_data.get("sender_ids", [])[:10],  # cap for evidence package size
        },
    }
