"""
River FTRL online learning — warm-start incremental weight updates from investigator feedback.
NEVER retrain from scratch on each feedback event; warm start only.
"""
import json
import os
import structlog
from pathlib import Path

logger = structlog.get_logger()

_ONLINE_MODEL_PATH = Path(os.getenv("ONLINE_MODEL_PATH", "ml/river_ftrl.json"))


def _get_model():
    """Lazy-load or create River FTRL model."""
    try:
        from river import linear_model, optim, preprocessing
    except ImportError:
        logger.warning("River not installed — online learning disabled")
        return None

    try:
        if _ONLINE_MODEL_PATH.exists():
            from river.base import Base
            with open(_ONLINE_MODEL_PATH) as f:
                state = json.load(f)
            model = linear_model.FTRLProximal(
                alpha=state.get("alpha", 0.5),
                beta=state.get("beta", 1.0),
                l1=state.get("l1", 0.0),
                l2=state.get("l2", 1.0),
            )
            return model
    except Exception as exc:
        logger.warning("Could not load online model state, creating fresh", error=str(exc))

    from river import linear_model
    return linear_model.FTRLProximal(alpha=0.5, beta=1.0, l1=0.0, l2=1.0)


def update_model(
    feature_vector: dict,
    confirmed_fraud: bool,
    alert_id: str,
) -> bool:
    """
    Warm-start update River FTRL with one investigator-confirmed example.
    Returns True if update succeeded, False if River not available.
    """
    model = _get_model()
    if model is None:
        return False

    try:
        label = 1 if confirmed_fraud else 0
        model.learn_one(feature_vector, label)
        _persist_model(model)
        logger.info("Online model updated", alert_id=alert_id, label=label)
        return True
    except Exception as exc:
        logger.error("Online model update failed", alert_id=alert_id, error=str(exc))
        return False


def _persist_model(model) -> None:
    """Persist model alpha/beta/l1/l2 config. River state is in the object weights."""
    _ONLINE_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "alpha": model.alpha,
        "beta": model.beta,
        "l1": model.l1,
        "l2": model.l2,
    }
    with open(_ONLINE_MODEL_PATH, "w") as f:
        json.dump(state, f)
