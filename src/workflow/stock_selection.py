"""User-facing entry timing classification for shortlisted stocks."""

from __future__ import annotations

from typing import Any


def classify_entry_timing(*, current_price: float, levels: dict[str, Any],
                          setup_category: str, breakout_confirmed: bool,
                          entry_confirmed: bool, direction: str = "BULLISH",
                          ema20: float | None = None, atr: float | None = None,
                          maximum_atr_extension: float = 2.0,
                          entry_zone_below_atr: float = 0.25,
                          entry_zone_above_atr: float = 0.50) -> dict[str, Any]:
    """Classify whether a qualified setup is sensibly enterable right now."""
    direction = str(direction or "BULLISH").upper()
    setup_category = str(setup_category or "").upper()
    sign = -1 if direction == "BEARISH" else 1
    try:
        current = float(current_price)
        entry = float(levels.get("entry"))
        stop = float(levels.get("stop_loss"))
        target = float(levels.get("target_1"))
    except (TypeError, ValueError):
        return {"status": "AVOID", "trigger_price": None,
                "reason": "A valid entry, stop-loss, and first target are required."}
    if min(current, entry, stop, target) <= 0:
        return {"status": "AVOID", "trigger_price": None,
                "reason": "Trade levels contain an invalid non-positive price."}

    stop_crossed = (current - stop) * sign <= 0
    target_reached = (target - current) * sign <= 0
    if stop_crossed:
        return {"status": "AVOID", "trigger_price": None,
                "reason": f"Current price {current:.2f} has invalidated stop-loss {stop:.2f}."}
    if target_reached:
        return {"status": "TOO LATE", "trigger_price": None,
                "reason": f"Current price {current:.2f} has already reached target {target:.2f}."}

    try:
        atr_value = float(atr)
    except (TypeError, ValueError):
        atr_value = 0.0
    try:
        extension = (current - float(ema20)) * sign
    except (TypeError, ValueError):
        extension = 0.0
    if atr_value > 0 and extension > maximum_atr_extension * atr_value:
        return {"status": "TOO LATE", "trigger_price": None,
                "extension_atr": round(extension / atr_value, 2),
                "reason": (f"Price is {extension / atr_value:.2f} ATR beyond EMA20; "
                           "the entry is overextended.")}

    moved_beyond_entry = (current - entry) * sign > 0
    remaining_reward = (target - current) * sign
    live_risk = (current - stop) * sign
    remaining_rr = remaining_reward / live_risk if live_risk > 0 else 0
    if moved_beyond_entry and remaining_rr < 1.0:
        return {"status": "TOO LATE", "trigger_price": None,
                "remaining_risk_reward": round(remaining_rr, 2),
                "reason": "Price has moved too far from entry; remaining reward/risk is below 1:1."}

    if entry_confirmed:
        if atr_value > 0:
            signed_distance = (current - entry) * sign
            if signed_distance < -entry_zone_below_atr * atr_value:
                return {"status": "WAIT FOR BREAKOUT", "trigger_price": entry,
                        "entry_distance_atr": round(signed_distance / atr_value, 2),
                        "reason": f"Price is outside the entry zone; wait for confirmation near {entry:.2f}."}
            if signed_distance > entry_zone_above_atr * atr_value:
                return {"status": "WAIT FOR PULLBACK", "trigger_price": entry,
                        "entry_distance_atr": round(signed_distance / atr_value, 2),
                        "reason": f"Price is above the valid entry zone; wait for a pullback near {entry:.2f}."}
        return {"status": "BUY NOW", "trigger_price": entry,
                "remaining_risk_reward": round(remaining_rr, 2),
                "reason": "Entry is confirmed and price remains inside the valid trade range."}
    if "PULLBACK" in setup_category:
        return {"status": "WAIT FOR PULLBACK", "trigger_price": entry,
                "reason": f"Wait for price to return to the planned entry zone near {entry:.2f}."}
    trigger = float(levels.get("resistance") or entry)
    if "BREAKOUT" in setup_category or not breakout_confirmed:
        return {"status": "WAIT FOR BREAKOUT", "trigger_price": trigger,
                "reason": f"Wait for a confirmed breakout above {trigger:.2f}."}
    return {"status": "AVOID", "trigger_price": None,
            "reason": "No confirmed breakout or pullback entry is currently available."}
