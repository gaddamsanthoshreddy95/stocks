"""Assess whether a strong live mover still offers a sensible new entry."""

from __future__ import annotations

from typing import Any


def _score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def assess_continuation(
    *,
    technical: dict[str, Any],
    levels: dict[str, Any],
    setup_evaluation: dict[str, Any],
    breakout_confirmed: bool,
    entry_confirmed: bool,
    alignment_status: str,
    sector_score: float | None,
    day_open: float | None = None,
    day_high: float | None = None,
    day_low: float | None = None,
) -> dict[str, Any]:
    """Return entry-location, continuation, and exhaustion evidence.

    This deliberately scores the opportunity *from the current price*. A high
    technical score can discover a mover, but cannot erase a poor live entry.
    """
    current = float(technical.get("current_price") or levels.get("entry") or 0)
    ema20 = float(technical.get("ema20") or current)
    atr = max(float(technical.get("atr") or 0), 1e-12)
    stop = float(levels.get("stop_loss") or 0)
    target = float(levels.get("target_1") or 0)
    extension_atr = (current - ema20) / atr
    live_risk = current - stop
    remaining_reward = target - current
    remaining_rr = remaining_reward / live_risk if live_risk > 0 else 0
    range_atr = (
        (float(day_high) - float(day_low)) / atr
        if day_high is not None and day_low is not None else None
    )
    close_location = (
        (current - float(day_low)) / max(float(day_high) - float(day_low), 1e-12)
        if day_high is not None and day_low is not None else None
    )
    move_from_open = (
        (current / float(day_open) - 1) * 100
        if day_open is not None and float(day_open) > 0 else None
    )
    evidence = (setup_evaluation.get("stage_1") or {}).get("evidence") or {}
    retest_holds = bool(
        evidence.get("breakout_retest_holds")
        or evidence.get("breakout_consolidation_holds")
    )
    rvol = float(technical.get("relative_volume") or 0)
    volume_state = str(technical.get("volume_state") or technical.get("volume_signal") or "")
    momentum_stable = (
        float(technical.get("macd_histogram") or 0) > 0
        and float(technical.get("macd") or 0)
        > float(technical.get("macd_signal_line") or 0)
    )
    rr_score = _score(remaining_rr / 2 * 100)
    breakout_score = 90 if breakout_confirmed and retest_holds else 70 if breakout_confirmed else 35
    location_score = (
        100 if extension_atr <= .75 else 75 if extension_atr <= 1.25
        else 45 if extension_atr <= 1.5 else 15
    )
    structure_score = _score((close_location if close_location is not None else .5) * 100)
    volume_score = 55 if volume_state == "PENDING" else _score(rvol / 1.2 * 100)
    alignment_score = {
        "ALIGNED": 90, "NEUTRAL": 60, "UNCERTAIN": 55, "CONFLICT": 20,
    }.get(str(alignment_status).upper(), 50)
    if sector_score is not None:
        alignment_score = (alignment_score + _score(sector_score)) / 2
    momentum_score = 80 if momentum_stable else 35
    raw_score = (
        rr_score * .25 + breakout_score * .20 + location_score * .15
        + structure_score * .15 + volume_score * .10
        + alignment_score * .10 + momentum_score * .05
    )
    penalties: list[dict[str, Any]] = []
    if extension_atr > 1.5:
        penalties.append({"code": "NO_CHASE_EXTENSION", "points": 25,
                          "detail": f"Price is {extension_atr:.2f} ATR above EMA20."})
    elif extension_atr > 1.25:
        penalties.append({"code": "ELEVATED_EXTENSION", "points": 10,
                          "detail": f"Price is {extension_atr:.2f} ATR above EMA20."})
    if range_atr is not None and range_atr > 1:
        penalties.append({"code": "DAILY_RANGE_CONSUMED", "points": 10,
                          "detail": f"Today has already travelled {range_atr:.2f} ATR."})
    if close_location is not None and close_location < .55 and (move_from_open or 0) > 2:
        penalties.append({"code": "UPPER_RANGE_REJECTION", "points": 15,
                          "detail": "A strong move is no longer holding near the session high."})
    if move_from_open is not None and move_from_open > 2:
        penalties.append({
            "code": "INTRADAY_MOVE_ALREADY_EXTENDED", "points": 35,
            "detail": (
                f"Price is already {move_from_open:.2f}% above the session open; "
                "do not initiate a new intraday trade."
            ),
        })
    if float(technical.get("rsi") or 0) >= 78:
        penalties.append({"code": "STRETCHED_RSI", "points": 10,
                          "detail": f"RSI is stretched at {float(technical['rsi']):.1f}."})
    if not momentum_stable:
        penalties.append({"code": "MOMENTUM_NOT_CONFIRMED", "points": 15,
                          "detail": "MACD continuation momentum is not confirmed."})
    final_score = round(_score(raw_score - sum(item["points"] for item in penalties)), 2)

    if move_from_open is not None and move_from_open > 2:
        state, action = "EXTENDED_DO_NOT_CHASE", "WAIT_FOR_NEW_BASE"
    elif extension_atr > 1.5 and not retest_holds:
        state = "EXTENDED_DO_NOT_CHASE"
        action = "WAIT_FOR_RETEST" if breakout_confirmed else "WAIT_FOR_PULLBACK"
    elif remaining_rr < 1:
        state, action = "AVOID_EXHAUSTED", "AVOID"
    elif entry_confirmed and final_score >= 75:
        state = action = "BUY_NOW"
    elif breakout_confirmed and final_score >= 55:
        state = action = "WAIT_FOR_RETEST"
    elif extension_atr > 1.0 or final_score >= 45:
        state = action = "WAIT_FOR_PULLBACK"
    else:
        state, action = "WAIT_FOR_CONFIRMATION", "WAIT_FOR_CONFIRMATION"

    return {
        "state": state,
        "recommended_action": action,
        "score": final_score,
        "raw_score": round(raw_score, 2),
        "extension_atr": round(extension_atr, 2),
        "remaining_risk_reward": round(remaining_rr, 2),
        "remaining_reward": round(remaining_reward, 2),
        "live_risk": round(live_risk, 2),
        "range_consumed_atr": round(range_atr, 2) if range_atr is not None else None,
        "close_location": round(close_location, 3) if close_location is not None else None,
        "move_from_open_percent": round(move_from_open, 2) if move_from_open is not None else None,
        "retest_or_consolidation_holds": retest_holds,
        "momentum_stable": momentum_stable,
        "components": {
            "remaining_reward_risk": round(rr_score, 2),
            "breakout_retest": breakout_score,
            "entry_location": location_score,
            "intraday_structure": round(structure_score, 2),
            "volume": round(volume_score, 2),
            "market_sector_alignment": round(alignment_score, 2),
            "momentum": momentum_score,
        },
        "penalties": penalties,
        "executable_now": state == "BUY_NOW",
    }
