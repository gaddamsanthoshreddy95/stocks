"""
Signal Based Score Engine
"""

from typing import List

from src.config.trading_config import (
    BUY,
    NEUTRAL,
    SIGNAL_WEIGHTS,
    PRICE_ACTION_SCORE_WEIGHT,
    STRONG_BUY,
    WATCHLIST,
)

from src.signals.base_signal import Signal


class ScoreEngine:

    @staticmethod
    def recommendation(score: float) -> str:
        if score >= STRONG_BUY:
            return "STRONG BUY"
        if score >= BUY:
            return "BUY"
        if score >= WATCHLIST:
            return "WATCHLIST"
        if score >= NEUTRAL:
            return "NEUTRAL"
        return "AVOID"

    @classmethod
    def integrate_setup_score(cls, base_score: float, setup: dict) -> dict:
        """Blend the new component score into the established final score."""
        if setup.get("status") != "OK" or not setup.get("direction"):
            score = round(base_score)
        else:
            setup_score = float((setup.get("score") or {}).get("score", 0))
            score = round(base_score * (1 - PRICE_ACTION_SCORE_WEIGHT)
                          + setup_score * PRICE_ACTION_SCORE_WEIGHT)
        return {"score": score, "max_score": 100,
                "recommendation": cls.recommendation(score)}

    @staticmethod
    def calculate(signals: List[Signal]):

        if not signals:

            return {
                "score": 0,
                "max_score": 100,
                "recommendation": "NO SIGNAL"
            }

        weighted_score = 0
        total_weight = 0

        for signal in signals:

            weight = SIGNAL_WEIGHTS.get(signal.name, 0)

            weighted_score += signal.strength * weight
            total_weight += weight

        score = round(weighted_score / total_weight)

        recommendation = ScoreEngine.recommendation(score)

        return {

            "score": score,

            "max_score": 100,

            "recommendation": recommendation

        }
