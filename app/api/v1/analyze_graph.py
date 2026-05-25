"""
POST /api/v1/analyze-graph — standalone graph fraud analysis endpoint.

No PostgreSQL, Redis, or Neo4j required. Accepts a graph snapshot from the
Transaction Graph Engine and returns a per-graph fraud verdict.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.security import require_graph_engine_key
from app.detection.graph_scorer import score_graph_snapshot

log = logging.getLogger(__name__)

router = APIRouter()


# ─── Request / Response ──────────────────────────────────────────────────────

class GraphEdgeInput(BaseModel):
    source: str
    target: str
    amount: float = Field(..., gt=0)
    timestamp: str = ""
    payment_rail: Optional[str] = None
    transaction_id: Optional[str] = None


class GraphNodeInput(BaseModel):
    id: str
    account_type: Optional[str] = None
    risk_score: Optional[float] = None
    is_flagged: Optional[bool] = False


class AnalyzeGraphRequest(BaseModel):
    graph_id: str = Field(..., min_length=1, max_length=100)
    nodes: list[GraphNodeInput] = []
    edges: list[GraphEdgeInput]
    transaction_metadata: Optional[dict[str, Any]] = None


class AnalyzeGraphResponse(BaseModel):
    graph_id: str
    fraud_score: float
    flagged: bool
    fraud_type: str
    accounts_involved: list[str]
    flagged_nodes: list[str]
    evidence_available: bool
    transactions_scored: int
    verdict: str


# ─── Endpoint ────────────────────────────────────────────────────────────────

@router.post(
    "/api/v1/analyze-graph",
    response_model=AnalyzeGraphResponse,
    summary="Analyze a transaction graph snapshot for fraud",
)
async def analyze_graph(
    payload: AnalyzeGraphRequest,
    _: str = Depends(require_graph_engine_key),
) -> AnalyzeGraphResponse:
    snapshot = {
        "nodes": [n.model_dump() for n in payload.nodes],
        "edges": [e.model_dump() for e in payload.edges],
    }

    try:
        result = score_graph_snapshot(snapshot)
    except Exception as exc:
        log.exception("graph_scorer failed graph_id=%s", payload.graph_id)
        raise HTTPException(status_code=500, detail="Graph analysis failed") from exc

    log.info(
        "analyze_graph graph_id=%s verdict=%s score=%.3f",
        payload.graph_id,
        result["verdict"],
        result["score"],
    )

    return AnalyzeGraphResponse(
        graph_id=payload.graph_id,
        fraud_score=result["score"],
        flagged=result["flagged"],
        fraud_type=result["fraud_type"],
        accounts_involved=result["accounts_involved"],
        flagged_nodes=result["flagged_nodes"],
        evidence_available=result["evidence_available"],
        transactions_scored=result["transactions_scored"],
        verdict=result["verdict"],
    )
