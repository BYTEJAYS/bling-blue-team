"""
Tier 3 feature builder.
Assembles up to 87 features from Redis (pre-computed) + PostgreSQL (real-time).
Returns a dict of feature_name -> float. Never makes scoring decisions.
Missing features return float('nan') — XGBoost handles NaN natively.
"""
import math
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.utils.redis_client import get_graph_features
from app.models.schemas import TransactionScoreRequest


def build_features(txn: TransactionScoreRequest, db: Session) -> dict[str, float]:
    features: dict[str, float] = {}

    # ── 35 pre-computed graph features from Redis ─────────────────────────────
    cached = get_graph_features(txn.account_id)
    graph_feature_names = [
        "degree_centrality", "betweenness_centrality", "clustering_coefficient",
        "pagerank_fraud_seeded", "community_id", "community_fraud_ratio",
        "shortest_path_to_fraud", "cycle_membership", "sink_score",
        "bipartite_score", "fan_out_ratio", "temporal_acceleration",
        "cash_mule_sink_score", "bridge_node_probability", "dormancy_reactivation_flag",
        "account_age_days", "kyc_completeness_score",
        "txn_count_30d", "txn_count_90d", "txn_count_all",
        "avg_txn_amount_30d", "distinct_counterparties_30d", "channel_entropy",
        "night_txn_ratio", "weekend_txn_ratio", "return_ratio",
        "amount_zscore", "counterparty_novelty", "hour_deviation",
        "channel_switch", "amount_series_score", "burst_score",
        "velocity_ratio", "dormancy_break", "geography_switch",
    ]
    for name in graph_feature_names:
        val = cached.get(name)
        features[name] = float(val) if val is not None else float("nan")

    # ── 52 real-time tabular features from PostgreSQL ─────────────────────────

    amount = float(txn.amount)
    ts = txn.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    # Transaction features
    features["txn_amount"] = amount
    features["txn_amount_log"] = math.log1p(amount)
    features["txn_amount_rounded"] = 1.0 if amount == round(amount, -3) else 0.0
    features["channel_upi"] = 1.0 if txn.channel == "UPI" else 0.0
    features["channel_imps"] = 1.0 if txn.channel == "IMPS" else 0.0
    features["channel_rtgs"] = 1.0 if txn.channel == "RTGS" else 0.0
    features["channel_neft"] = 1.0 if txn.channel == "NEFT" else 0.0
    features["hour_of_day"] = float(ts.hour)
    features["day_of_week"] = float(ts.weekday())
    features["is_weekend"] = 1.0 if ts.weekday() >= 5 else 0.0
    features["is_night"] = 1.0 if (ts.hour >= 23 or ts.hour < 5) else 0.0

    # Festival period: Oct 1 – Nov 15 (Navratri + Diwali window)
    features["is_festival_period"] = 1.0 if (
        (ts.month == 10) or (ts.month == 11 and ts.day <= 15)
    ) else 0.0

    # Threshold proximity features
    for threshold in [50_000, 1_00_000, 10_00_000]:
        features[f"amount_vs_threshold_{threshold}"] = amount / threshold

    # Payee age features
    payee_vpa_age = float("nan")
    if txn.payee_vpa_created_at:
        vpa_ts = txn.payee_vpa_created_at
        if vpa_ts.tzinfo is None:
            vpa_ts = vpa_ts.replace(tzinfo=timezone.utc)
        payee_vpa_age = max(0.0, (ts - vpa_ts).days)
    features["payee_vpa_age_days"] = payee_vpa_age

    # Velocity features from PostgreSQL (last 1h, 24h, 7d)
    try:
        vel_rows = db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE timestamp > NOW() - INTERVAL '1 hour') AS count_1h,
                    COUNT(*) FILTER (WHERE timestamp > NOW() - INTERVAL '24 hours') AS count_24h,
                    COUNT(*) FILTER (WHERE timestamp > NOW() - INTERVAL '7 days') AS count_7d,
                    SUM(amount) FILTER (WHERE timestamp > NOW() - INTERVAL '1 hour') AS vol_1h,
                    SUM(amount) FILTER (WHERE timestamp > NOW() - INTERVAL '24 hours') AS vol_24h,
                    COUNT(DISTINCT payee_account_id) FILTER (WHERE timestamp > NOW() - INTERVAL '24 hours') AS distinct_payees_24h
                FROM transactions
                WHERE account_id = :account_id
            """),
            {"account_id": txn.account_id},
        ).fetchone()

        if vel_rows:
            features["txn_count_last_1h"] = float(vel_rows.count_1h or 0)
            features["txn_count_last_24h"] = float(vel_rows.count_24h or 0)
            features["txn_count_last_7d"] = float(vel_rows.count_7d or 0)
            features["txn_volume_last_1h"] = float(vel_rows.vol_1h or 0)
            features["txn_volume_last_24h"] = float(vel_rows.vol_24h or 0)
            features["distinct_payees_24h"] = float(vel_rows.distinct_payees_24h or 0)
    except Exception:
        for k in ["txn_count_last_1h", "txn_count_last_24h", "txn_count_last_7d",
                  "txn_volume_last_1h", "txn_volume_last_24h", "distinct_payees_24h"]:
            features[k] = float("nan")

    # Payee alert history
    try:
        payee_alert_count = db.execute(
            text("""
                SELECT COUNT(*) FROM alerts a
                JOIN transactions t ON t.id = a.transaction_id
                WHERE t.account_id = :payee_id
            """),
            {"payee_id": txn.payee_account_id or ""},
        ).scalar()
        features["payee_in_alert_log"] = 1.0 if (payee_alert_count or 0) > 0 else 0.0
        features["payee_shared_alert_count"] = float(payee_alert_count or 0)
    except Exception:
        features["payee_in_alert_log"] = float("nan")
        features["payee_shared_alert_count"] = float("nan")

    return features
