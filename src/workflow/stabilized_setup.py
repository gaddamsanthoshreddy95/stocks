"""Detect controlled, low-extension setups before a potential upside trigger."""

from __future__ import annotations

from typing import Any

import pandas as pd


def assess_stabilized_setup(
    *,
    technical: dict[str, Any],
    levels: dict[str, Any],
    intraday: pd.DataFrame | None,
    market_alignment: str,
    sector_score: float | None,
) -> dict[str, Any]:
    current = float(technical.get("current_price") or levels.get("entry") or 0)
    atr = max(float(technical.get("atr") or 0), 1e-12)
    ema20 = float(technical.get("ema20") or current)
    extension_atr = (current - ema20) / atr
    rsi = float(technical.get("rsi") or 0)
    frame = intraday.copy() if intraday is not None else pd.DataFrame()
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame.empty or not required.issubset(frame.columns):
        return {
            "available": False, "state": "UNAVAILABLE", "score": 0,
            "reason": "Current-session 15-minute candles are unavailable.",
            "executable_now": False,
        }
    frame = frame.sort_index()
    latest_time = pd.Timestamp(frame.index[-1])
    session_date = latest_time.date()
    session = frame[
        pd.Index([pd.Timestamp(value).date() for value in frame.index]) == session_date
    ].tail(12)
    if len(session) < 5:
        return {
            "available": False, "state": "INSUFFICIENT_INTRADAY_BARS", "score": 0,
            "reason": f"Only {len(session)} current-session 15-minute bars are available.",
            "executable_now": False,
        }
    typical = (
        session["High"].astype(float)
        + session["Low"].astype(float)
        + session["Close"].astype(float)
    ) / 3
    volumes = session["Volume"].astype(float).clip(lower=0)
    cumulative_volume = float(volumes.sum())
    vwap = float((typical * volumes).sum() / cumulative_volume) if cumulative_volume > 0 else current
    previous = session.iloc[-5:-1]
    recent = session.iloc[-4:]
    trigger_candidates = [
        float(previous["High"].max()),
        float(levels.get("resistance") or 0),
    ]
    trigger_candidates = [value for value in trigger_candidates if value > current]
    trigger = min(trigger_candidates) if trigger_candidates else float(previous["High"].max())
    planned_stop = float(levels.get("stop_loss") or 0)
    structural_stop = float(recent["Low"].min())
    stop = max(planned_stop, structural_stop) if planned_stop > 0 else structural_stop
    target_candidates = [
        float(levels.get("target_1") or 0),
        float(levels.get("target_2") or 0),
    ]
    target_candidates = [value for value in target_candidates if value > trigger]
    target = min(target_candidates) if target_candidates else trigger + max(atr * 1.5, trigger - stop)
    trigger_risk = trigger - stop
    remaining_rr = (target - trigger) / trigger_risk if trigger_risk > 0 else 0
    distance_to_trigger_atr = (trigger - current) / atr
    lows = recent["Low"].astype(float).tolist()
    higher_lows = sum(lows[index] >= lows[index - 1] for index in range(1, len(lows))) >= 2
    ranges = session["High"].astype(float) - session["Low"].astype(float)
    prior_range = float(ranges.iloc[-10:-4].mean()) if len(ranges) >= 10 else float(ranges.iloc[:-4].mean())
    recent_range = float(ranges.iloc[-4:].mean())
    range_contraction = recent_range / prior_range if prior_range > 0 else 1
    prior_volume = float(volumes.iloc[-10:-4].mean()) if len(volumes) >= 10 else float(volumes.iloc[:-4].mean())
    recent_volume = float(volumes.iloc[-4:].mean())
    volume_contraction = recent_volume <= prior_volume * .90 if prior_volume > 0 else False
    volume_renewal = float(volumes.iloc[-1]) >= float(volumes.iloc[-2]) * 1.10
    above_vwap = current >= vwap
    vwap_extension_percent = (current / vwap - 1) * 100 if vwap > 0 else 0
    move_from_open = (
        (current / float(session.iloc[0]["Open"]) - 1) * 100
        if float(session.iloc[0]["Open"]) > 0 else 0
    )
    alignment_score = {
        "ALIGNED": 100, "NEUTRAL": 65, "UNCERTAIN": 55, "CONFLICT": 20,
    }.get(str(market_alignment).upper(), 50)
    if sector_score is not None:
        alignment_score = (alignment_score + max(0, min(100, float(sector_score)))) / 2
    checks = {
        "controlled_intraday_move": -.5 <= move_from_open <= 1.5,
        "normal_ema_extension": extension_atr <= .75,
        "healthy_rsi": 50 <= rsi <= 68,
        "above_vwap": above_vwap,
        "vwap_not_extended": vwap_extension_percent <= .75,
        "higher_lows": higher_lows,
        "range_contraction": range_contraction <= .85,
        "volume_contraction_or_renewal": volume_contraction or volume_renewal,
        "near_trigger": 0 <= distance_to_trigger_atr <= 1,
        "remaining_risk_reward": remaining_rr >= 1.5,
        "market_sector_support": alignment_score >= 55,
    }
    weights = {
        "range_contraction": 20, "remaining_risk_reward": 20,
        "normal_ema_extension": 10, "above_vwap": 5,
        "higher_lows": 15, "near_trigger": 10,
        "market_sector_support": 10, "volume_contraction_or_renewal": 10,
    }
    score = round(sum(weight for name, weight in weights.items() if checks[name]), 2)
    blocking = [
        name for name in (
            "controlled_intraday_move", "normal_ema_extension", "healthy_rsi",
            "above_vwap", "remaining_risk_reward",
        ) if not checks[name]
    ]
    if score >= 70 and not blocking and current < trigger:
        state, action = "READY_NEAR_TRIGGER", "WAIT_FOR_BREAKOUT"
    elif score >= 70 and not blocking and current >= trigger:
        state, action = "TRIGGER_CROSSED_CONFIRMATION_REQUIRED", "WAIT_FOR_CONFIRMATION"
    elif extension_atr > 1.5 or move_from_open > 2:
        state, action = "EXTENDED_NOT_STABILIZED", "WAIT_FOR_PULLBACK"
    elif score >= 50:
        state, action = "BUILDING_BASE", "WATCHLIST"
    else:
        state, action = "NOT_STABILIZED", "AVOID"
    return {
        "available": True, "state": state, "recommended_action": action,
        "score": score, "executable_now": False,
        "trigger_price": round(trigger, 2), "stop_loss": round(stop, 2),
        "target": round(target, 2), "remaining_risk_reward": round(remaining_rr, 2),
        "distance_to_trigger_atr": round(distance_to_trigger_atr, 2),
        "extension_atr": round(extension_atr, 2),
        "move_from_open_percent": round(move_from_open, 2),
        "vwap": round(vwap, 2),
        "vwap_extension_percent": round(vwap_extension_percent, 2),
        "range_contraction_ratio": round(range_contraction, 2),
        "volume_contraction": volume_contraction,
        "volume_renewal": volume_renewal,
        "higher_lows": higher_lows,
        "checks": checks, "blocking_checks": blocking,
        "bars_reviewed": len(session),
        "reason": (
            f"{state.replace('_', ' ').title()}: score {score:.0f}/100; "
            f"trigger {trigger:.2f}, remaining R:R {remaining_rr:.2f}."
        ),
    }
