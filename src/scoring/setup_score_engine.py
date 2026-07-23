"""Correlated, configurable setup scoring integrated as a component score."""

from __future__ import annotations

from typing import Any

from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig


class SetupScoreEngine:
    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config = config

    def score(self, components: dict[str, float | None],
              rejection_reasons: list[str] | None = None) -> dict[str, Any]:
        values = {name: max(0.0, min(100.0, float(components.get(name) or 0)))
                  for name in self.config.score_weights}
        # Related price-derived pairs share a capped contribution.
        pattern_confirmation = min(values["candlestick_pattern"], values.get("retest", 0))
        bos_breakout = min(values["market_structure"], values["breakout"])
        correlation_penalty = pattern_confirmation * .03 + bos_breakout * .02
        weight_total = sum(self.config.score_weights.values()) or 1
        score = sum(values[k] * w for k, w in self.config.score_weights.items()) / weight_total
        score = max(0.0, min(100.0, score - correlation_penalty))
        thresholds = self.config.classification_thresholds
        category = ("REJECT" if score < thresholds["reject"] else
                    "LOW_CONFIDENCE" if score < thresholds["low_confidence"] else
                    "WATCHLIST" if score < thresholds["watchlist"] else
                    "VALID_SETUP" if score < thresholds["valid_setup"] else "HIGH_CONFIDENCE")
        return {"score": round(score, 2), "category": category, "component_scores": values,
                "weights": self.config.score_weights, "correlation_penalty": round(correlation_penalty, 2),
                "rejection_reasons": rejection_reasons or []}
