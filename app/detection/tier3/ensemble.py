"""
Tier 3: XGBoost ensemble scorer.
Loads model from ml/models/xgboost_v1.json.
Returns raw score 0.0-1.0. Indian context adjustment and thresholds applied downstream.

CRITICAL: scale_pos_weight=99 — never train without it (1% fraud dataset).
CRITICAL: eval_metric='aucpr' — not 'auc' (imbalanced data).
CRITICAL: warm start only for online updates — never full retrain.
"""
import os
import numpy as np
import structlog

logger = structlog.get_logger()

_model = None
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "ml", "models", "xgboost_v1.json")


def _load_model():
    global _model
    if _model is not None:
        return _model
    try:
        import xgboost as xgb
        model = xgb.Booster()
        model.load_model(os.path.abspath(_MODEL_PATH))
        _model = model
        logger.info("XGBoost model loaded", path=_MODEL_PATH)
    except Exception as exc:
        logger.warning("XGBoost model not found, using fallback scorer", error=str(exc))
        _model = None
    return _model


def score(features: dict[str, float]) -> tuple[float, list[dict]]:
    """
    Score transaction using XGBoost ensemble.

    Returns:
        (raw_score, shap_top_features)
        raw_score: 0.0-1.0
        shap_top_features: list of {feature, contribution} sorted by |contribution| desc
    """
    model = _load_model()

    feature_names = sorted(features.keys())
    feature_values = np.array([
        features.get(k, float("nan")) for k in feature_names
    ], dtype=np.float32).reshape(1, -1)

    if model is None:
        # Fallback heuristic scorer when model file not yet trained
        raw_score = _heuristic_fallback(features)
        shap_features = _mock_shap(features)
        return raw_score, shap_features

    try:
        import xgboost as xgb
        dmatrix = xgb.DMatrix(feature_values, feature_names=feature_names, missing=float("nan"))
        raw_score = float(model.predict(dmatrix)[0])

        # SHAP explanation (pred_contribs on Booster directly)
        shap_values = model.predict(dmatrix, pred_contribs=True)[0]
        shap_features = sorted(
            [{"feature": k, "contribution": float(shap_values[i])}
             for i, k in enumerate(feature_names)],
            key=lambda x: abs(x["contribution"]),
            reverse=True,
        )[:10]

        return raw_score, shap_features

    except Exception as exc:
        logger.error("XGBoost inference failed", error=str(exc))
        raw_score = _heuristic_fallback(features)
        return raw_score, _mock_shap(features)


def _heuristic_fallback(features: dict[str, float]) -> float:
    """Simple weighted heuristic when model file is not yet available."""
    score = 0.0
    if not np.isnan(features.get("payee_vpa_age_days", float("nan"))):
        if features["payee_vpa_age_days"] < 7:
            score += 0.25
    if features.get("is_night", 0) == 1.0:
        score += 0.15
    if not np.isnan(features.get("txn_count_last_1h", float("nan"))):
        if features["txn_count_last_1h"] > 5:
            score += 0.20
    if not np.isnan(features.get("pagerank_fraud_seeded", float("nan"))):
        score += features["pagerank_fraud_seeded"] * 0.30
    if not np.isnan(features.get("cycle_membership", float("nan"))):
        if features["cycle_membership"] == 1.0:
            score += 0.40
    return min(score, 1.0)


def _mock_shap(features: dict[str, float]) -> list[dict]:
    """Return top contributing features by raw value when SHAP is unavailable."""
    ranked = sorted(
        [(k, abs(v)) for k, v in features.items() if not np.isnan(v)],
        key=lambda x: x[1],
        reverse=True,
    )[:5]
    return [{"feature": k, "contribution": v} for k, v in ranked]
