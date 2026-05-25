"""
FINnet 2.0 STR (Suspicious Transaction Report) draft generator.
Produces machine-readable + human-readable STR structure for investigators.
RBI PMLA Section 12 mandates STR within 7 days of suspicion.
"""
from datetime import datetime, timezone
from app.core.config import settings


def generate_str_draft(
    transaction_id: str,
    account_id: str,
    amount: float,
    channel: str,
    score: float,
    gate_fired: str | None,
    shap_explanation: dict | None,
    trail_data: dict | None,
) -> dict:
    """
    Returns a FINnet-2.0-compatible STR draft dict.
    Investigator reviews and approves before actual FINnet submission.
    """
    top_features = _extract_top_shap_features(shap_explanation, n=5)
    reason_narrative = _build_narrative(gate_fired, top_features, score)

    return {
        "report_type": "STR",
        "standard": "FINnet_2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": settings.model_version,
        "status": "DRAFT",
        "transaction": {
            "id": transaction_id,
            "amount_inr": amount,
            "channel": channel,
        },
        "risk_assessment": {
            "fraud_score": round(score, 4),
            "gate_fired": gate_fired,
            "top_contributing_features": top_features,
        },
        "narrative": reason_narrative,
        "fund_trail_summary": _summarize_trail(trail_data),
        "instructions": (
            "Review the narrative and fund trail. "
            "Confirm or reject via POST /api/v1/feedback before FINnet submission."
        ),
    }


def _extract_top_shap_features(shap: dict | None, n: int = 5) -> list[dict]:
    if not shap:
        return []
    features = shap.get("top_features", [])
    return features[:n]


def _build_narrative(gate_fired: str | None, top_features: list, score: float) -> str:
    parts = [
        f"System fraud score: {score:.2%}.",
    ]
    if gate_fired:
        gate_desc = {
            "confirmed_cycle": "A confirmed circular fund transfer cycle was detected.",
            "abandoned_sink": "Funds flowed into an account showing abandoned sink behavior (high inflow, no outflow, new account).",
            "bipartite_core": "Account sits at the center of a many-to-one aggregation network consistent with mule coordination.",
            "cash_mule_sink": "Account received high-value transfers and converted them to cash with no subsequent digital activity.",
            "merchant_terminal": "Merchant terminal shows transaction patterns inconsistent with declared business category.",
        }.get(gate_fired, f"Hard gate fired: {gate_fired}.")
        parts.append(gate_desc)

    if top_features:
        feature_names = [f["feature"] for f in top_features[:3]]
        parts.append(f"Top contributing factors: {', '.join(feature_names)}.")

    parts.append("Recommend STR filing under PMLA Section 12 within 7 days.")
    return " ".join(parts)


def _summarize_trail(trail_data: dict | None) -> dict:
    if not trail_data:
        return {"status": "PENDING", "detail": "Trail reconstruction in progress."}
    return {
        "status": "AVAILABLE",
        "forward_hops": trail_data.get("forward_hops", 0),
        "backward_hops": trail_data.get("backward_hops", 0),
        "reconstructed_at": trail_data.get("reconstructed_at"),
    }
