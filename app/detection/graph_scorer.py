"""
Standalone graph-level fraud scorer for the /analyze-graph endpoint.

No PostgreSQL, Redis, or Neo4j required. Runs pure NetworkX topology
analysis on a graph snapshot and returns a per-graph verdict.
"""

import logging
from collections import Counter
from datetime import datetime, timezone

import networkx as nx

log = logging.getLogger(__name__)

_THRESHOLD_LOG       = 0.38
_THRESHOLD_REVIEW    = 0.62
_THRESHOLD_HIGH_RISK = 0.83


def _ts(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return datetime.now(timezone.utc)


def score_graph_snapshot(snapshot: dict) -> dict:
    """
    Pure graph topology fraud analysis.

    Accepts: { nodes: [...], edges: [...] }
    Returns: { verdict, fraud_type, score, flagged, flagged_nodes,
               accounts_involved, evidence_available, transactions_scored }
    """
    edges: list[dict] = snapshot.get("edges", [])

    if not edges:
        return _result("CLEAN", "none", 0.0, [], [], 0)

    G: nx.DiGraph = nx.DiGraph()
    amounts: list[float] = []
    timestamps: list[datetime] = []

    for e in edges:
        src = str(e.get("source", ""))
        tgt = str(e.get("target", ""))
        amt = float(e.get("amount", 0))
        if not src or not tgt or amt <= 0:
            continue
        G.add_edge(src, tgt, amount=amt, timestamp=e.get("timestamp", ""))
        amounts.append(amt)
        timestamps.append(_ts(e.get("timestamp", "")))

    if G.number_of_edges() == 0:
        return _result("CLEAN", "none", 0.0, [], [], 0)

    signals: list[str] = []
    fraud_types: list[str] = []
    flagged: set[str] = set()
    score = 0.0

    # Gate 1: Circular path
    try:
        cycles = [c for c in nx.simple_cycles(G) if len(c) >= 2]
        if cycles:
            signals.append("circular_path_detected")
            fraud_types.append("circular_transfer_fraud")
            for cyc in cycles[:5]:
                flagged.update(cyc)
            score = max(score, 0.97)
    except Exception:
        pass

    # Gate 2: Fan-out — one account → ≥4 unique recipients
    for node in list(G.nodes()):
        out_nbrs = list(G.successors(node))
        if len(set(out_nbrs)) >= 4:
            signals.append("fan_out_detected")
            fraud_types.append("smurfing_fan_out")
            flagged.add(node)
            flagged.update(out_nbrs[:6])
            score = max(score, 0.85)
            break

    # Gate 3: Bipartite mule sink — ≥4 senders → one account
    for node in list(G.nodes()):
        in_nbrs = list(G.predecessors(node))
        if len(set(in_nbrs)) >= 4:
            signals.append("bipartite_mule_sink")
            fraud_types.append("mule_network_aggregation")
            flagged.add(node)
            flagged.update(in_nbrs[:6])
            score = max(score, 0.82)
            break

    # Gate 4: Forwarding chain depth > 3
    try:
        if nx.is_directed_acyclic_graph(G):
            depth = nx.dag_longest_path_length(G)
            if depth > 3:
                signals.append("chain_depth_exceeded")
                fraud_types.append("layering_chain")
                path = nx.dag_longest_path(G)
                flagged.update(path[:6])
                score = max(score, 0.75 + min(0.10, (depth - 3) * 0.04))
        elif G.number_of_nodes() > 4:
            score = max(score, 0.78)
    except Exception:
        pass

    # Gate 5: Repeated-amount structuring
    if amounts:
        for amt, cnt in Counter(round(a, 2) for a in amounts).items():
            if cnt >= 3 and amt > 1000:
                signals.append("repeated_amount_structuring")
                fraud_types.append("structuring_below_threshold")
                for e in edges:
                    if abs(float(e.get("amount", 0)) - amt) < 0.01:
                        flagged.add(str(e.get("source", "")))
                        flagged.add(str(e.get("target", "")))
                score = max(score, 0.72)
                break

    # Velocity heuristic
    if len(timestamps) >= 2:
        span_s = (max(timestamps) - min(timestamps)).total_seconds()
        if span_s < 600 and sum(amounts) > 200_000:
            signals.append("high_velocity_transfer")
            fraud_types.append("rapid_transfer_burst")
            score = max(score, 0.60)

    # Large single transfer
    if amounts and max(amounts) >= 500_000:
        score = max(score, 0.45)
        if not signals:
            signals.append("large_amount_threshold")
            fraud_types.append("large_value_transfer")

    if score >= _THRESHOLD_HIGH_RISK:
        verdict = "FRAUD"
    elif score >= _THRESHOLD_REVIEW:
        verdict = "SUSPICIOUS"
    elif score >= _THRESHOLD_LOG:
        verdict = "LOGGED"
    else:
        verdict = "CLEAN"

    flagged.discard("")
    all_accounts = list({str(e.get("source", "")) for e in edges}
                        | {str(e.get("target", "")) for e in edges}
                        if e.get("source") and e.get("target") else set())
    top_fraud_type = fraud_types[0] if fraud_types else ("none" if verdict == "CLEAN" else "unknown_pattern")

    log.info("graph_scorer verdict=%s score=%.3f signals=%s", verdict, score, signals)
    return _result(
        verdict=verdict,
        fraud_type=top_fraud_type,
        score=round(score, 4),
        flagged_nodes=list(flagged)[:20],
        accounts_involved=list(flagged) if flagged else all_accounts[:20],
        transactions_scored=G.number_of_edges(),
    )


def _result(
    verdict: str,
    fraud_type: str,
    score: float,
    flagged_nodes: list[str],
    accounts_involved: list[str],
    transactions_scored: int,
) -> dict:
    return {
        "verdict": verdict,
        "fraud_type": fraud_type,
        "score": score,
        "flagged": verdict in ("FRAUD", "SUSPICIOUS"),
        "flagged_nodes": flagged_nodes,
        "accounts_involved": accounts_involved,
        "evidence_available": verdict in ("FRAUD", "SUSPICIOUS"),
        "transactions_scored": transactions_scored,
    }
