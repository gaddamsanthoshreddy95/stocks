"""UI/report-friendly projection of rich price-action results."""

from __future__ import annotations

from typing import Any


def build_price_action_report(result: dict[str, Any]) -> dict[str, Any]:
    zones = result.get("zones", [])
    return {
        "Market Structure": result.get("market_structure", {}),
        "Nearest Support and Resistance": {
            "support": result.get("support"), "resistance": result.get("resistance"),
            "zones": zones,
        },
        "Active Candlestick Patterns": result.get("patterns", []),
        "Breakout/Breakdown Status": result.get("breakout"),
        "Retest Status": result.get("retest"),
        "Reversal Setup": result.get("reversals", []),
        "Entry Alternatives": result.get("entries", []),
        "Stop-Loss and Targets": [{"stop": e.get("stop_loss"), "targets": e.get("targets")}
                                  for e in result.get("entries", [])],
        "Risk-Reward": [e.get("risk_reward") for e in result.get("entries", [])],
        "Confidence Breakdown": result.get("score", {}),
        "Option-Selling Impact": result.get("option_selling", {}),
        "Rejection Reasons": result.get("rejection_reasons", []),
        "chart_overlays": {
            "zones": [{"lower": z["lower"], "upper": z["upper"], "type": z["type"]} for z in zones],
            "swings": result.get("market_structure", {}).get("confirmed_pivots", []),
            "breakout": result.get("breakout", {}).get("breakout_candle") if result.get("breakout") else None,
            "retest": result.get("retest", {}).get("retest_timestamp") if result.get("retest") else None,
            "entries": result.get("entries", []),
        },
    }
