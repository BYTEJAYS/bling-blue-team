"""
Score-to-action threshold mapping.
Thresholds are configurable via env vars — not hardcoded here.
"""
from typing import Literal
from app.core.config import settings

Action = Literal["PASS", "LOG", "REVIEW", "HIGH_RISK"]


def score_to_action(score: float) -> Action:
    if score >= settings.threshold_high_risk:
        return "HIGH_RISK"
    if score >= settings.threshold_review:
        return "REVIEW"
    if score >= settings.threshold_log:
        return "LOG"
    return "PASS"
