"""
app/detection/novelty/isolation_forest.py

Structural novelty detector — wraps the trained Isolation Forest model.

THIS MODULE DOES NOT AFFECT FRAUD SCORES.
Scores a transaction's graph feature vector and returns an anomaly score.
Callers decide what to do with that score; this module only computes it.

Loaded once at startup as a module-level singleton. Thread-safe.
"""

from pathlib import Path
from typing import Optional

import numpy as np
import structlog

log = structlog.get_logger()

# More negative = more anomalous.
# At -0.20 with contamination=0.001: ~1-3 flags per 1000 transactions.
# Never lower than -0.15 (floods developer queue).
# Never higher than -0.25 (misses genuine novel patterns).
NOVELTY_THRESHOLD = -0.20

MODEL_PATH = "ml/models/isolation_forest_v1.joblib"


class NoveltyDetector:
    """
    Wraps the trained Isolation Forest model.
    Loaded once at startup via load(). Thread-safe for concurrent requests.
    Runs in degraded mode (novelty detection disabled) if model file not found —
    the rest of the system is unaffected.
    """

    def __init__(self) -> None:
        self._model = None
        self._scaler = None
        self._features: list[str] = []
        self._available = False

    def load(self, model_path: str = MODEL_PATH) -> bool:
        """
        Load the trained model from disk.
        Returns True on success, False if file not found or corrupt.
        """
        path = Path(model_path)
        if not path.exists():
            log.warning(
                "novelty_model_not_found",
                path=str(path),
                action="novelty_detection_disabled",
                fix="Run: python ml/train_isolation_forest.py",
            )
            self._available = False
            return False

        try:
            import joblib

            artifact = joblib.load(path)
            self._model = artifact["model"]
            self._scaler = artifact["scaler"]
            self._features = artifact["features"]
            self._available = True
            log.info(
                "novelty_model_loaded",
                path=str(path),
                n_features=len(self._features),
                trained_on=artifact.get("trained_on_samples", "unknown"),
                version=artifact.get("version", "unknown"),
            )
            return True
        except Exception as e:
            log.error("novelty_model_load_failed", error=str(e))
            self._available = False
            return False

    @property
    def available(self) -> bool:
        return self._available

    def score(self, graph_features: dict) -> Optional[float]:
        """
        Score a transaction's graph features for structural novelty.

        Args:
            graph_features: Dict of feature name → float value.
                            Same dict used by XGBoost feature builder.

        Returns:
            Anomaly score in roughly [-0.5, 0.5]. More negative = more anomalous.
            None if model not available or scoring fails.

        IMPORTANT: This score is NEVER used for fraud classification.
        It only determines whether to route to the developer novelty queue.
        """
        if not self._available:
            return None

        try:
            feature_vector = np.array(
                [float(graph_features.get(f) or 0.0) for f in self._features],
                dtype=np.float32,
            ).reshape(1, -1)

            # Sanitize any NaN/Inf that slipped through
            feature_vector = np.nan_to_num(
                feature_vector, nan=0.0, posinf=1.0, neginf=-1.0
            )

            feature_vector_scaled = self._scaler.transform(feature_vector)
            anomaly_score = float(
                self._model.decision_function(feature_vector_scaled)[0]
            )
            return anomaly_score

        except Exception as e:
            log.error("novelty_score_failed", error=str(e))
            return None

    def is_novel(self, anomaly_score: Optional[float]) -> bool:
        """True if score crosses the novelty threshold. Safe with None input."""
        if anomaly_score is None:
            return False
        return anomaly_score < NOVELTY_THRESHOLD


# Singleton — loaded once at application startup via novelty_detector.load()
novelty_detector = NoveltyDetector()
