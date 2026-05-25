"""
Nightly APScheduler job — computes 35 graph features per account and writes to Redis.
Runs at 01:00 IST (19:30 UTC previous day) to be ready for morning transactions.

Pre-computation is mandatory — full Neo4j traversal at query time times out at 3+ hops
on 10M+ transactions. This batch decouples graph computation from the scoring hot path.
"""
import structlog
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler

from app.graph.neo4j_client import run_query
from app.utils.redis_client import set_graph_features

logger = structlog.get_logger()

BATCH_QUERY = """
MATCH (a:Account)
WHERE a.active = true
OPTIONAL MATCH (a)-[s:SENT]->(r:Account)
OPTIONAL MATCH (b:Account)-[s2:SENT]->(a)
WITH a,
     count(distinct r) AS out_degree,
     count(distinct b) AS in_degree,
     coalesce(sum(s.amount), 0) AS total_out_volume,
     coalesce(sum(s2.amount), 0) AS total_in_volume,
     count(distinct s) AS out_edge_count,
     count(distinct s2) AS in_edge_count
OPTIONAL MATCH (a)-[:SENT*2..4]->(a)
WITH a, out_degree, in_degree, total_out_volume, total_in_volume,
     out_edge_count, in_edge_count,
     count(*) AS cycle_path_count
RETURN a.id AS account_id,
       out_degree,
       in_degree,
       total_out_volume,
       total_in_volume,
       out_edge_count,
       in_edge_count,
       cycle_path_count,
       a.inflow_last_30d AS inflow_last_30d,
       a.retention_ratio AS retention_ratio,
       a.days_since_last_send AS days_since_last_send,
       a.account_age_days AS account_age_days,
       a.is_merchant AS is_merchant,
       a.kyc_occupation AS kyc_occupation,
       a.account_type AS account_type
LIMIT 100000
"""


def run_nightly_feature_computation() -> None:
    """Main nightly job — query Neo4j, write 35 features per account to Redis."""
    started_at = datetime.now(timezone.utc)
    logger.info("Nightly graph feature computation started")

    try:
        rows = run_query(BATCH_QUERY, {})
    except Exception as exc:
        logger.error("Nightly batch Neo4j query failed", error=str(exc))
        return

    success = 0
    failed = 0
    for row in rows:
        account_id = row.get("account_id")
        if not account_id:
            continue
        features = _row_to_features(row)
        try:
            set_graph_features(account_id, features)
            success += 1
        except Exception as exc:
            logger.warning("Failed to cache features", account_id=account_id, error=str(exc))
            failed += 1

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    logger.info("Nightly batch complete",
                accounts_processed=success,
                failed=failed,
                elapsed_s=round(elapsed, 1))


def _row_to_features(row: dict) -> dict:
    """Map Neo4j row to the 35-feature dict stored in Redis."""
    total_in = float(row.get("total_in_volume") or 0)
    total_out = float(row.get("total_out_volume") or 0)

    return {
        "out_degree": int(row.get("out_degree") or 0),
        "in_degree": int(row.get("in_degree") or 0),
        "total_out_volume": total_out,
        "total_in_volume": total_in,
        "out_edge_count": int(row.get("out_edge_count") or 0),
        "in_edge_count": int(row.get("in_edge_count") or 0),
        "cycle_path_count": int(row.get("cycle_path_count") or 0),
        "in_out_ratio": (total_in / total_out) if total_out > 0 else 0.0,
        "retention_ratio": float(row.get("retention_ratio") or 0),
        "inflow_last_30d": float(row.get("inflow_last_30d") or 0),
        "days_since_last_send": int(row.get("days_since_last_send") or 999),
        "account_age_days": int(row.get("account_age_days") or 0),
        "is_merchant": bool(row.get("is_merchant")),
        "kyc_occupation": row.get("kyc_occupation"),
        "account_type": row.get("account_type"),
        # Placeholder slots for 35 total — filled by Tier 3 feature builder at score time
        "hub_score": 0.0,
        "authority_score": 0.0,
        "pagerank": 0.0,
        "clustering_coefficient": 0.0,
        "betweenness_centrality": 0.0,
        "degree_centrality": 0.0,
        "closeness_centrality": 0.0,
        "eigenvector_centrality": 0.0,
        "neighbor_avg_amount": 0.0,
        "neighbor_avg_age": 0.0,
        "unique_payees_30d": 0,
        "unique_payers_30d": 0,
        "max_single_transfer_in": 0.0,
        "max_single_transfer_out": 0.0,
        "std_transfer_in": 0.0,
        "std_transfer_out": 0.0,
        "bipartite_density": 0.0,
        "sink_score": 0.0,
        "mule_score": 0.0,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(
    run_nightly_feature_computation,
    trigger="cron",
    hour=19,
    minute=30,
    id="nightly_graph_features",
    replace_existing=True,
)


def start_scheduler() -> None:
    """Call from app lifespan startup."""
    if not scheduler.running:
        scheduler.start()
        logger.info("Nightly batch scheduler started (01:00 IST / 19:30 UTC)")


def stop_scheduler() -> None:
    """Call from app lifespan shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
