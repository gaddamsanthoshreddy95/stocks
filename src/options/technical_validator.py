"""Technical zone/path-risk overlay for existing option strike selection."""

from __future__ import annotations

from typing import Any


class TechnicalOptionValidator:
    @staticmethod
    def validate(direction: str, strike: float, spot: float, technical: dict[str, Any],
                 event_risk: bool = False) -> dict[str, Any]:
        zones = technical.get("zones", [])
        support = max((z for z in zones if z["type"] == "SUPPORT" and z["midpoint"] < spot),
                      key=lambda z: z["midpoint"], default=None)
        resistance = min((z for z in zones if z["type"] == "RESISTANCE" and z["midpoint"] > spot),
                         key=lambda z: z["midpoint"], default=None)
        breakout = technical.get("breakout") or {}
        retest = technical.get("retest") or {}
        warnings, approved = [], True
        invalidation = technical.get("invalidation")
        if direction == "BULLISH" and support and strike >= support["lower"]:
            warnings.append("SHORT_PUT_STRIKE_NOT_BELOW_SUPPORT_ZONE")
        if direction == "BEARISH" and resistance and strike <= resistance["upper"]:
            warnings.append("SHORT_CALL_STRIKE_NOT_ABOVE_RESISTANCE_ZONE")
        strike_zone = next((z for z in zones if z["lower"] <= strike <= z["upper"]), None)
        if strike_zone:
            warnings.append("STRIKE_INSIDE_TECHNICAL_ZONE")
            if strike_zone.get("classification") == "WEAK":
                warnings.append("STRIKE_INSIDE_WEAK_ZONE")
        if breakout.get("quality") in ("WEAK", "FALSE_BREAKOUT", "EXTENDED") and not retest:
            warnings.append("UNRESOLVED_BREAKOUT_RISK")
            approved = False
        if event_risk:
            warnings.append("EVENT_RISK_REDUCES_TECHNICAL_RELIABILITY")
        path_distance = ((spot - support["lower"]) / spot * 100 if direction == "BULLISH" and support
                         else (resistance["upper"] - spot) / spot * 100 if resistance else None)
        strike_safety = (abs(strike - invalidation) if invalidation is not None else None)
        if invalidation is not None and strike_safety / max(spot, 1e-12) * 100 < .5:
            warnings.append("STRIKE_TOO_CLOSE_TO_TECHNICAL_INVALIDATION")
        return {"approved": approved, "nearest_support": support["midpoint"] if support else None,
                "spot_price": spot, "suggested_strike": strike,
                "technical_invalidation": invalidation,
                "nearest_resistance": resistance["midpoint"] if resistance else None,
                "support_strength": support["strength_score"] if support else None,
                "resistance_strength": resistance["strength_score"] if resistance else None,
                "breakout_status": breakout.get("quality", "NONE"),
                "retest_status": retest.get("retest_quality", "NONE"),
                "reversal_risk": technical.get("reversal_risk", 0),
                "downside_path_risk": path_distance if direction == "BULLISH" else None,
                "upside_path_risk": path_distance if direction == "BEARISH" else None,
                "technical_strike_safety": strike_safety,
                "distance_strike_to_invalidation": strike_safety, "warnings": warnings}
