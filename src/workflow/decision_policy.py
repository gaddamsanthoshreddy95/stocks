"""Final recommendation policy shared by the daily workflow and tests."""

from __future__ import annotations


def normalize_market_regime(regime: str, confidence: float, low_confidence: float = 50,
                            confirmed_confidence: float = 65) -> str:
    regime = (regime or "UNAVAILABLE").upper()
    if confidence < low_confidence:
        if "BEARISH" in regime:
            return "UNCERTAIN_BEARISH"
        if "BULLISH" in regime:
            return "UNCERTAIN_BULLISH"
        return "UNCERTAIN"
    if confidence < confirmed_confidence and regime == "STRONG_BEARISH":
        return "BEARISH"
    if confidence < confirmed_confidence and regime == "STRONG_BULLISH":
        return "BULLISH"
    return regime


def market_alignment(regime: str, confidence: float, direction: str,
                     low_confidence: float = 50) -> dict:
    if confidence < low_confidence or regime.startswith("UNCERTAIN") or regime == "UNAVAILABLE":
        return {"status": "UNCERTAIN", "score": 50, "penalty": 0}
    bullish = regime in {"BULLISH", "STRONG_BULLISH"}
    bearish = regime in {"BEARISH", "STRONG_BEARISH"}
    if not bullish and not bearish:
        return {"status": "NEUTRAL", "score": 50, "penalty": 0}
    aligned = (bullish and direction == "BULLISH") or (bearish and direction == "BEARISH")
    return ({"status": "ALIGNED", "score": 85, "penalty": 0} if aligned
            else {"status": "CONFLICT", "score": 25, "penalty": 12})


def pcr_adjustment(pcr: float | None, direction: str) -> float:
    if pcr is None:
        return 0
    if direction == "BULLISH":
        return 6 if pcr >= 1 else 2 if pcr >= .8 else -3 if pcr >= .6 else -7
    if direction == "BEARISH":
        return 6 if pcr <= .7 else 2 if pcr <= .9 else -3 if pcr <= 1.2 else -7
    return 0


def option_confidence_status(confidence: float | None) -> str:
    if confidence is None:
        return "UNAVAILABLE"
    return ("CONFIRMED" if confidence >= 70 else "NEUTRAL" if confidence >= 50
            else "CONFLICT" if confidence >= 35 else "UNRELIABLE")


def market_risk_scale(confidence: float, available: bool = True,
                      alignment_status: str | None = None, low_confidence: float = 50,
                      confirmed_confidence: float = 65) -> float:
    if not available:
        return 1.0
    confidence_scale = .5 if confidence < low_confidence else .75 if confidence < confirmed_confidence else 1.0
    alignment_scale = .5 if alignment_status == "CONFLICT" else 1.0
    return min(confidence_scale, alignment_scale)


def risk_reward_tier(score: float, risk_reward: float, a_minimum: float = 1.5,
                     b_minimum: float = 1.3, c_minimum: float = 1.2) -> dict:
    """Grade R:R in the context of setup confidence instead of one hard gate."""
    if score >= 80:
        grade, minimum = "A", a_minimum
    elif score >= 72:
        grade, minimum = "B", b_minimum
    else:
        grade, minimum = "C", c_minimum
    return {"grade": grade, "minimum": minimum,
            "approved": risk_reward >= minimum,
            "watchlist_eligible": risk_reward >= c_minimum}


def adaptive_market_policy(regime: str) -> dict:
    """Return transparent execution thresholds for the current market regime."""
    regime = (regime or "UNAVAILABLE").upper()
    if regime in {"BULLISH", "STRONG_BULLISH"}:
        return {"profile": "AGGRESSIVE_TREND", "trade_score_minimum": 70,
                "readiness_minimum": 75, "confirmation_required": False,
                "preferred_strategies": ["TREND_FOLLOWING", "BREAKOUT", "PULLBACK"]}
    if regime in {"BEARISH", "STRONG_BEARISH"}:
        return {"profile": "DEFENSIVE", "trade_score_minimum": 78,
                "readiness_minimum": 90, "confirmation_required": True,
                "preferred_strategies": ["CONFIRMED_BREAKOUT", "DEFINED_RISK_OPTIONS"]}
    if regime in {"SIDEWAYS", "RANGE_BOUND", "NEUTRAL"}:
        return {"profile": "RANGE", "trade_score_minimum": 72,
                "readiness_minimum": 75, "confirmation_required": True,
                "preferred_strategies": ["MEAN_REVERSION", "SHORT_PUT", "CREDIT_SPREAD"]}
    return {"profile": "CAUTIOUS", "trade_score_minimum": 75,
            "readiness_minimum": 85, "confirmation_required": True,
            "preferred_strategies": ["CONFIRMED_ENTRY", "DEFINED_RISK_OPTIONS"]}


def expected_value(win_probability: float, expected_reward: float, risk: float) -> dict:
    """Calculate currency EV and the equivalent multiple of initial risk."""
    probability = min(100.0, max(0.0, float(win_probability))) / 100
    value = probability * expected_reward - (1 - probability) * risk
    return {"amount": round(value, 2),
            "risk_multiple": round(value / risk, 3) if risk > 0 else 0.0,
            "win_probability": round(probability * 100, 2)}


def combine_strategy_eligibility(entry_confirmed: bool, equity_risk_reward: float,
                                 minimum_equity_risk_reward: float,
                                 short_put_approved: bool) -> dict:
    """Keep equity and short-Put decisions independent, then combine them."""
    equity_approved = entry_confirmed and equity_risk_reward >= minimum_equity_risk_reward
    return {
        "equity_approved": equity_approved,
        "short_put_approved": short_put_approved,
        "any_approved": equity_approved or short_put_approved,
    }


def classify_setup(trend: str, momentum: str) -> str:
    trend, momentum = (trend or "").upper(), (momentum or "").upper()
    if "BULLISH" in trend and "BEARISH" in momentum:
        return "BULLISH_PULLBACK"
    if "BEARISH" in trend and "BULLISH" in momentum:
        return "BEARISH_BOUNCE"
    if "BULLISH" in trend and "BULLISH" in momentum:
        return "BULLISH_CONTINUATION"
    if "BEARISH" in trend and "BEARISH" in momentum:
        return "BEARISH_CONTINUATION"
    return "MIXED"
