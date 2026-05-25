"""
Cypher queries for bipartite core detection (mule network pattern).
Many senders → few receivers with high density = mule aggregator.
"""
from app.graph.neo4j_client import run_query

BIPARTITE_CHECK_QUERY = """
MATCH (sender:Account)-[:SENT]->(receiver:Account {id: $account_id})
WITH collect(distinct sender) AS senders, receiver
WHERE size(senders) >= 5
WITH senders, receiver,
     size([(s)-[:SENT]->(receiver) | s]) AS actual_edges,
     size(senders) AS sender_count
WITH senders, receiver, actual_edges, sender_count,
     toFloat(actual_edges) / (sender_count * 1) AS density
WHERE density > 0.7
RETURN [s in senders | s.id] AS sender_ids,
       receiver.id AS receiver_id,
       density,
       sender_count,
       receiver.account_type AS receiver_account_type,
       receiver.kyc_occupation AS receiver_kyc_occupation,
       receiver.is_merchant AS receiver_is_merchant
"""


def check_bipartite_core(account_id: str) -> dict | None:
    """Returns bipartite data if mule network pattern found, else None."""
    results = run_query(BIPARTITE_CHECK_QUERY, {"account_id": account_id})
    return results[0] if results else None
