"""
app/detection/novelty/novelty_router.py

Routes structural novelty findings to:
  1. novelty_queue table in PostgreSQL (developer review queue)
  2. Red Team service (immediate escalation when same pattern seen 10+ times)

Called from app/api/v1/score.py AFTER the full scoring pipeline completes.
NEVER modifies fraud_score. NEVER creates investigator alerts.
Creates its own DB session — safe to run in a thread pool after the request session closes.
All exceptions are caught — failure here must never affect scoring responses.
"""

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy import text
from app.utils.postgres_client import SessionLocal
from app.utils.redis_client import get_redis

log = structlog.get_logger()

# Same fingerprint seen 10+ times in 7 days → escalate immediately to Red Team.
ESCALATION_THRESHOLD = 10

_SALT = "BLING_NOVELTY_SALT"


def route_novelty(
    transaction_id: str,
    account_id: str,
    anomaly_score: float,
    fraud_score: float,
    fraud_action: str,
    gate_fired: Optional[str],
    graph_features: dict,
) -> None:
    """
    Route a structurally novel transaction to the developer review queue.

    Creates its own SQLAlchemy session — safe to call from asyncio.create_task
    via run_in_executor since the request session will already be closed.

    All errors are caught and logged — never propagated.
    """
    try:
        # Build novelty fingerprint from top 5 features by absolute magnitude
        top_features = sorted(
            graph_features.items(),
            key=lambda x: abs(float(x[1] or 0)),
            reverse=True,
        )[:5]
        fingerprint_input = "|".join(
            f"{k}:{round(float(v or 0), 1)}" for k, v in top_features
        )
        novelty_fingerprint = hashlib.sha256(fingerprint_input.encode()).hexdigest()[:16]

        # Track occurrences via Redis (7-day window, for escalation logic)
        r = get_redis()
        fp_key = f"novelty:fp:{novelty_fingerprint}"
        current_count = int(r.incr(fp_key))
        r.expire(fp_key, 86400 * 7)

        requires_escalation = current_count >= ESCALATION_THRESHOLD

        with SessionLocal() as db:
            db.execute(
                text("""
                    INSERT INTO novelty_queue (
                        transaction_id, account_id, anomaly_score, fraud_score,
                        fraud_action, gate_fired, novelty_fingerprint,
                        fingerprint_occurrences, graph_features_snapshot,
                        requires_escalation, status, created_at
                    ) VALUES (
                        :txn_id, :acct_id, :anomaly_score, :fraud_score,
                        :fraud_action, :gate_fired, :fingerprint,
                        :fp_count, CAST(:features_json AS jsonb),
                        :escalate, 'PENDING_REVIEW', :created_at
                    )
                    ON CONFLICT (transaction_id) DO NOTHING
                """),
                {
                    "txn_id": transaction_id,
                    "acct_id": account_id,
                    "anomaly_score": anomaly_score,
                    "fraud_score": fraud_score,
                    "fraud_action": fraud_action,
                    "gate_fired": gate_fired,
                    "fingerprint": novelty_fingerprint,
                    "fp_count": current_count,
                    "features_json": json.dumps(_sanitize(graph_features)),
                    "escalate": requires_escalation,
                    "created_at": datetime.now(timezone.utc),
                },
            )
            db.commit()

        # Prometheus counter
        try:
            from app.utils.metrics import novelty_flags_total
            novelty_flags_total.inc()
        except Exception:
            pass

        account_pseudo = hashlib.sha256(
            f"{_SALT}{account_id}".encode()
        ).hexdigest()[:12]

        log.info(
            "novelty_flagged",
            txn_id=transaction_id,
            account_pseudo=account_pseudo,
            anomaly_score=round(anomaly_score, 4),
            fraud_score=round(fraud_score, 4),
            fraud_action=fraud_action,
            fingerprint=novelty_fingerprint,
            occurrences=current_count,
            escalation=requires_escalation,
        )

        if requires_escalation:
            _escalate_to_red_team(
                transaction_id=transaction_id,
                anomaly_score=anomaly_score,
                fraud_score=fraud_score,
                fingerprint=novelty_fingerprint,
                fp_count=current_count,
                graph_features=graph_features,
            )

    except Exception as e:
        # Must NEVER propagate — scoring response already returned to caller.
        log.error(
            "novelty_routing_failed",
            txn_id=transaction_id,
            error=str(e),
            error_type=type(e).__name__,
        )


def _escalate_to_red_team(
    transaction_id: str,
    anomaly_score: float,
    fraud_score: float,
    fingerprint: str,
    fp_count: int,
    graph_features: dict,
) -> None:
    """Send escalated novelty pattern to Red Team. Only called when fingerprint seen 10+ times."""
    try:
        from app.integrations.red_team_client import notify_novelty_pattern

        novelty_dna = {
            "pattern_type": "structural_novelty",
            "novelty_fingerprint": fingerprint,
            "occurrences_in_7d": fp_count,
            "sample_transaction_id": transaction_id,
            "anomaly_score": round(anomaly_score, 4),
            "fraud_score_at_detection": round(fraud_score, 4),
            "evaded_detection": fraud_score < 0.62,
            "structural_profile": {
                k: round(float(v or 0), 4)
                for k, v in graph_features.items()
                if k in {
                    "pagerank_fraud_seeded", "betweenness_centrality",
                    "sink_score", "bipartite_score", "fan_out_ratio",
                    "temporal_acceleration", "burst_score",
                }
            },
            "source": "isolation_forest_novelty_detector",
            "requires_developer_review": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        sent = notify_novelty_pattern(novelty_dna)

        try:
            from app.utils.metrics import novelty_escalations_total
            novelty_escalations_total.inc()
        except Exception:
            pass

        log.info(
            "novelty_escalated_to_red_team",
            fingerprint=fingerprint,
            occurrences=fp_count,
            evaded_detection=fraud_score < 0.62,
            delivered=sent,
        )

    except Exception as e:
        log.error("red_team_escalation_failed", error=str(e))


def _sanitize(features: dict) -> dict:
    """Replace NaN/Inf with None so JSONB accepts the value."""
    result = {}
    for k, v in features.items():
        if v is None:
            result[k] = None
        elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            result[k] = None
        else:
            result[k] = v
    return result
