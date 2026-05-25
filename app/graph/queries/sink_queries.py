"""
Cypher queries for abandoned sink detection.
Uses pre-computed nightly attributes — no traversal at query time.
"""
from app.graph.neo4j_client import run_query

SINK_CHECK_QUERY = """
MATCH (a:Account {id: $account_id})
WHERE a.inflow_last_30d > 50000
  AND a.retention_ratio > 0.80
  AND a.days_since_last_send > 30
  AND a.account_age_days < 180
RETURN a.id AS id,
       a.inflow_last_30d AS inflow_last_30d,
       a.retention_ratio AS retention_ratio,
       a.days_since_last_send AS days_since_last_send,
       a.account_age_days AS account_age_days,
       a.account_type AS account_type,
       a.kyc_occupation AS kyc_occupation
"""


def check_abandoned_sink(account_id: str) -> dict | None:
    """Returns account data if sink pattern matches, else None."""
    results = run_query(SINK_CHECK_QUERY, {"account_id": account_id})
    return results[0] if results else None
