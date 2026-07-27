"""Strictly lightweight, full-universe stock screening.

This module intentionally has no imports from price action, market structure,
options, news, events, or advanced support/resistance packages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from src.decision.decision_engine import DecisionEngine
from src.decision.setup_entry_evaluator import SetupEntryEvaluator
from src.indicators.pipeline import IndicatorPipeline
from src.scoring.score_engine import ScoreEngine


REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
SCORE_WEIGHTS = {
    "trend": .30,
    "momentum": .20,
    "volume": .15,
    "volatility": .15,
    "support": .20,
}


@dataclass
class StockAnalysisContext:
    """Reusable per-symbol data shared by lightweight and advanced stages."""

    symbol: str
    timeframe: str
    historical_data: pd.DataFrame
    live_candle: dict[str, Any] | None
    latest_completed_candle_time: datetime | pd.Timestamp | None
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    weekly_data: pd.DataFrame | None = None
    intraday_data: pd.DataFrame | None = None
    benchmark_symbol: str = "^NSEI"
    benchmark_data: pd.DataFrame | None = None
    sector_benchmark_symbol: str | None = None
    sector_benchmark_data: pd.DataFrame | None = None
    indicators: dict[str, Any] = field(default_factory=dict)
    candle_metrics: dict[str, Any] = field(default_factory=dict)
    lightweight_support: dict[str, Any] | None = None
    lightweight_breakout: dict[str, Any] | None = None
    lightweight_risk_reward: dict[str, Any] | None = None
    advanced_support_resistance: Any | None = None
    pivots: Any | None = None
    market_structure: Any | None = None
    price_action: Any | None = None
    relative_strength: dict[str, Any] | None = None
    sector_strength: dict[str, Any] | None = None
    market_regime: dict[str, Any] | None = None
    fundamentals: dict[str, Any] | None = None
    events: dict[str, Any] | None = None
    news: dict[str, Any] | None = None
    option_chain: dict[str, Any] | None = None
    scores: dict[str, float] = field(default_factory=dict)
    gates: dict[str, bool] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LightweightScreenResult:
    """Structured Stage 1 result plus compatibility payload for later stages."""

    symbol: str
    technical_score: int
    technical_recommendation: str
    trend_score: int
    momentum_score: int
    volume_score: int
    volatility_score: int
    support_location_score: int
    setup_category: str
    decision_action: str
    liquidity_score: float
    trust_score: float
    basic_risk_reward: float | None
    entry_confirmation: dict[str, Any]
    lightweight_support: dict[str, Any]
    reason_codes: list[str]
    warnings: list[str]
    timings: dict[str, float]
    context: StockAnalysisContext
    candidate: dict[str, Any]


class LightweightDataError(ValueError):
    """A structured, per-symbol Stage 1 validation failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def support_compatibility_report(
    legacy: dict[str, Any], lightweight: dict[str, Any],
    legacy_technical_score: int | None = None,
    lightweight_technical_score: int | None = None,
    legacy_action: str | None = None,
    lightweight_action: str | None = None,
) -> dict[str, Any]:
    """Describe observable differences during temporary legacy-path comparison."""
    return {
        "legacy_support": legacy.get("support"),
        "lightweight_support": lightweight.get("support"),
        "legacy_support_score": legacy.get("score"),
        "lightweight_support_score": lightweight.get("score"),
        "legacy_technical_score": legacy_technical_score,
        "lightweight_technical_score": lightweight_technical_score,
        "technical_score_difference": (
            lightweight_technical_score - legacy_technical_score
            if legacy_technical_score is not None and lightweight_technical_score is not None
            else None
        ),
        "legacy_action": legacy_action,
        "lightweight_action": lightweight_action,
        "action_changed": (
            legacy_action != lightweight_action
            if legacy_action is not None and lightweight_action is not None else None
        ),
    }


def _normalise_frame(data: pd.DataFrame, minimum_rows: int) -> pd.DataFrame:
    if data is None or data.empty:
        raise LightweightDataError("INSUFFICIENT_HISTORY", "No daily history is available")
    missing = [name for name in REQUIRED_COLUMNS if name not in data.columns]
    if missing:
        raise LightweightDataError(
            "MISSING_REQUIRED_COLUMN", f"Missing required columns: {', '.join(missing)}"
        )
    if data.index.has_duplicates:
        raise LightweightDataError("DUPLICATE_TIMESTAMP", "Daily history contains duplicate timestamps")
    if not data.index.is_monotonic_increasing:
        raise LightweightDataError("NON_MONOTONIC_TIMESTAMP", "Daily timestamps are not sorted")
    if len(data) < minimum_rows:
        raise LightweightDataError(
            "INSUFFICIENT_HISTORY", f"Need at least {minimum_rows} daily rows; received {len(data)}"
        )
    frame = data.copy()
    for column in REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if not np.isfinite(frame[list(REQUIRED_COLUMNS)].to_numpy(dtype=float)).all():
        raise LightweightDataError("INVALID_NUMERIC_VALUE", "OHLCV contains non-finite values")
    invalid = (
        (frame["High"] < frame[["Open", "Close"]].max(axis=1))
        | (frame["Low"] > frame[["Open", "Close"]].min(axis=1))
        | (frame["High"] < frame["Low"])
    )
    if bool(invalid.any()):
        raise LightweightDataError("INVALID_OHLC", "One or more candles violate OHLC bounds")
    return frame


def _validate_live_candle(live_candle: dict[str, Any] | None, maximum_age_seconds: float) -> None:
    if not live_candle:
        raise LightweightDataError("MISSING_LIVE_CANDLE", "A current live candle is required")
    for name in REQUIRED_COLUMNS:
        value = live_candle.get(name)
        if value is None or not np.isfinite(float(value)):
            raise LightweightDataError("INVALID_NUMERIC_VALUE", f"Live candle {name} is invalid")
    high, low = float(live_candle["High"]), float(live_candle["Low"])
    open_, close = float(live_candle["Open"]), float(live_candle["Close"])
    if high < max(open_, close) or low > min(open_, close) or high < low:
        raise LightweightDataError("INVALID_OHLC", "Live candle violates OHLC bounds")
    timestamp = live_candle.get("timestamp") or live_candle.get("Timestamp")
    if timestamp is not None:
        parsed = pd.Timestamp(timestamp)
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize("UTC")
        age = (pd.Timestamp.now(tz="UTC") - parsed.tz_convert("UTC")).total_seconds()
        if age > maximum_age_seconds:
            raise LightweightDataError("STALE_LIVE_CANDLE", f"Live candle is {age:.0f}s old")


def calculate_lightweight_support(
    prepared: pd.DataFrame, current_price: float, atr: float, lookback: int,
    distance_percent: float,
) -> dict[str, Any]:
    """Return rolling support/resistance without importing an advanced engine."""
    completed = prepared.iloc[:-1].tail(lookback)
    if completed.empty or atr <= 0 or not np.isfinite(atr):
        return {
            "support": None, "resistance": None,
            "distance_from_support_pct": None, "distance_to_resistance_pct": None,
            "distance_from_support_atr": None, "distance_to_resistance_atr": None,
            "score": 40, "method": f"ROLLING_{lookback}_DAY_EXTREMES",
            "reason_codes": ["LIGHTWEIGHT_LEVELS_UNAVAILABLE"],
        }
    support = float(completed["Low"].min())
    resistance = float(completed["High"].max())
    support_valid = support <= current_price
    resistance_valid = resistance >= current_price
    support_pct = max(0.0, (current_price - support) * 100 / current_price) if support_valid else None
    resistance_pct = max(0.0, (resistance - current_price) * 100 / current_price) if resistance_valid else None
    if support_valid and support_pct is not None and support_pct <= distance_percent:
        score, reasons = 90, ["NEAR_ROLLING_SUPPORT"]
    elif resistance_valid and resistance_pct is not None and resistance_pct <= distance_percent:
        score, reasons = 20, ["NEAR_ROLLING_RESISTANCE"]
    elif support_valid and resistance_valid:
        score, reasons = 60, ["BETWEEN_ROLLING_LEVELS"]
    else:
        score, reasons = 40, ["LIGHTWEIGHT_LEVELS_UNAVAILABLE"]
    return {
        "support": support if support_valid else None,
        "resistance": resistance if resistance_valid else None,
        "distance_from_support_pct": round(support_pct, 3) if support_pct is not None else None,
        "distance_to_resistance_pct": round(resistance_pct, 3) if resistance_pct is not None else None,
        "distance_from_support_atr": round((current_price - support) / atr, 3) if support_valid else None,
        "distance_to_resistance_atr": round((resistance - current_price) / atr, 3) if resistance_valid else None,
        "score": score, "method": f"ROLLING_{lookback}_DAY_EXTREMES",
        "reason_codes": reasons,
    }


def _basic_candlestick(prepared: pd.DataFrame) -> dict[str, Any]:
    current, previous = prepared.iloc[-1], prepared.iloc[-2]
    bullish = (
        float(previous["Close"]) < float(previous["Open"])
        and float(current["Close"]) > float(current["Open"])
        and float(current["Open"]) <= float(previous["Close"])
        and float(current["Close"]) >= float(previous["Open"])
    )
    bearish = (
        float(previous["Close"]) > float(previous["Open"])
        and float(current["Close"]) < float(current["Open"])
        and float(current["Open"]) >= float(previous["Close"])
        and float(current["Close"]) <= float(previous["Open"])
    )
    return {
        "pattern": "BULLISH ENGULFING" if bullish else "BEARISH ENGULFING" if bearish else "NONE",
        "signal": "BUY" if bullish else "SELL" if bearish else "NONE",
        "strength": 45 if bullish or bearish else 0,
        "method": "LIGHTWEIGHT_TWO_CANDLE",
    }


def _scores(latest: pd.Series, support_score: int) -> tuple[dict[str, int], int]:
    close = float(latest["Close"])
    trend = (
        (20 if close > float(latest["EMA20"]) else 0)
        + (30 if close > float(latest["EMA50"]) else 0)
        + (50 if close > float(latest["EMA200"]) else 0)
    )
    rsi = float(latest["RSI"])
    rsi_points = 50 if 55 <= rsi <= 70 else 35 if 45 <= rsi < 55 else 20 if 30 <= rsi < 45 else 40 if rsi < 30 else 10
    momentum = rsi_points + (50 if float(latest["MACD"]) > float(latest["MACD_SIGNAL"]) else 0)
    rvol = float(latest["RVOL"])
    volume = 100 if rvol >= 2 else 85 if rvol >= 1.5 else 65 if rvol >= 1 else 40 if rvol >= .75 else 15
    atr_percent = float(latest["ATR"]) * 100 / max(close, 1e-12)
    volatility = 90 if atr_percent <= 1.5 else 80 if atr_percent <= 3 else 50 if atr_percent <= 5 else 20
    components = {
        "trend": trend, "momentum": momentum, "volume": volume,
        "volatility": volatility, "support": support_score,
    }
    score = round(sum(components[name] * SCORE_WEIGHTS[name] for name in SCORE_WEIGHTS))
    return components, score


def _market_quality(prepared: pd.DataFrame) -> dict[str, Any]:
    closes = prepared["Close"].astype(float)
    returns = closes.pct_change().dropna()
    gaps = (prepared["Open"].astype(float) / closes.shift(1) - 1).abs().dropna()
    return {
        "history_days": len(prepared),
        "large_return_days": int((returns.abs() >= .10).sum()),
        "large_gap_days": int((gaps >= .07).sum()),
        "realized_volatility_percent": round(float(returns.std() * np.sqrt(252) * 100), 2)
        if not returns.empty else 0,
    }


def _liquidity_trust(latest: pd.Series, quality: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    close, average_volume = float(latest["Close"]), float(latest["AVG_VOLUME"])
    turnover_crore = close * average_volume / 10_000_000
    if turnover_crore >= 100:
        base, status = 100, "EXCELLENT"
    elif turnover_crore >= 50:
        base, status = 85, "HIGH"
    elif turnover_crore >= 20:
        base, status = 70, "GOOD"
    elif turnover_crore >= 5:
        base, status = 50, "MODERATE"
    else:
        base, status = 25, "LOW"
    rvol = float(latest["RVOL"])
    liquidity_score = max(0, min(100, base + (10 if rvol >= 1 else 0 if rvol >= .75 else -10)))
    liquidity = {
        "score": liquidity_score, "status": status,
        "average_daily_turnover_crore": round(turnover_crore, 2), "relative_volume": round(rvol, 2),
    }
    atr_percent = float(latest["ATR"]) * 100 / max(close, 1e-12)
    trust_score = liquidity_score * .5
    trust_score += 20 if quality["history_days"] >= 200 else 5
    trust_score += 15 if atr_percent <= 5 else 5 if atr_percent <= 8 else 0
    trust_score += 10 if quality["large_return_days"] <= 2 and quality["large_gap_days"] <= 2 else 0
    trust_score = round(min(100, trust_score), 2)
    trust = {
        "score": trust_score,
        "status": "TRUSTED" if trust_score >= 70 else "CAUTION" if trust_score >= 55 else "EXCLUDE",
        "atr_percent": round(atr_percent, 2), "flags": [],
    }
    return liquidity, trust


def run_lightweight_screen(
    symbol: str, historical_data: pd.DataFrame, live_candle: dict[str, Any] | None,
    settings: Any,
) -> LightweightScreenResult:
    """Execute the exact Stage 1 contract without any advanced dependency."""
    total_started = perf_counter()
    minimum_rows = max(30, int(getattr(settings, "lightweight_support_lookback", 20)) + 2)
    validated = _normalise_frame(historical_data, minimum_rows)
    _validate_live_candle(live_candle, float(getattr(settings, "lightweight_live_max_age_seconds", 120)))
    indicator_started = perf_counter()
    try:
        prepared = IndicatorPipeline.run(validated)
    except (KeyError, TypeError, ValueError, FloatingPointError) as exc:
        raise LightweightDataError("INDICATOR_CALCULATION_FAILED", str(exc)) from exc
    latest = prepared.iloc[-1]
    required_indicators = ("EMA20", "EMA50", "EMA200", "RSI", "MACD", "MACD_SIGNAL",
                           "MACD_HISTOGRAM", "ATR", "AVG_VOLUME", "RVOL")
    if not all(np.isfinite(float(latest[name])) for name in required_indicators):
        raise LightweightDataError("INVALID_NUMERIC_VALUE", "Latest indicator values are non-finite")
    indicator_seconds = perf_counter() - indicator_started

    close, open_, high, low = (float(latest[name]) for name in ("Close", "Open", "High", "Low"))
    candle_range = max(high - low, 1e-12)
    candle_metrics = {
        "daily_return": round(prepared["Close"].pct_change().iloc[-1] * 100, 4),
        "opening_gap_percent": round((open_ / float(prepared.iloc[-2]["Close"]) - 1) * 100, 4),
        "body_size": abs(close - open_), "range": high - low,
        "close_location": (close - low) / candle_range,
        "extension_atr": max(0.0, (close - float(latest["EMA20"])) / max(float(latest["ATR"]), 1e-12)),
    }
    recent_ranges = (prepared["High"] - prepared["Low"]).iloc[:-1]
    recent_three_range = float(recent_ranges.tail(3).mean()) if len(recent_ranges) >= 3 else 0
    prior_ten_range = float(recent_ranges.iloc[:-3].tail(10).mean()) if len(recent_ranges) >= 6 else 0
    daily_range_contraction = (
        recent_three_range / prior_ten_range if prior_ten_range > 0 else None
    )
    context = StockAnalysisContext(
        symbol=symbol, timeframe="1d", historical_data=prepared,
        live_candle=live_candle, latest_completed_candle_time=prepared.index[-2],
        indicators={name: float(latest[name]) for name in required_indicators},
        candle_metrics=candle_metrics,
        metadata={"live_row_index": prepared.index[-1]},
    )

    support_started = perf_counter()
    support = calculate_lightweight_support(
        prepared, close, float(latest["ATR"]),
        int(getattr(settings, "lightweight_support_lookback", 20)),
        float(getattr(settings, "lightweight_support_distance_pct", 2.0)),
    )
    context.lightweight_support = support
    breakout_window = prepared.iloc[:-1].tail(
        int(getattr(settings, "lightweight_breakout_lookback", 20)))
    previous_resistance = (
        float(breakout_window["High"].max()) if not breakout_window.empty else None)
    breakout = {
        "confirmed": bool(previous_resistance is not None and close > float(previous_resistance)),
        "broken_resistance": previous_resistance if previous_resistance is not None and close > float(previous_resistance) else None,
        "resistance": previous_resistance, "conditions": {},
        "method": "LIGHTWEIGHT_ROLLING_BREAKOUT",
    }
    context.lightweight_breakout = breakout
    support_seconds = perf_counter() - support_started

    components, technical_score = _scores(latest, support["score"])
    entry = {
        "current_price": close, "support": support.get("support"),
        "resistance": support.get("resistance"), "next_resistance": None,
        "resistance_levels": [support["resistance"]] if support.get("resistance") else [],
        "broken_resistance": breakout.get("broken_resistance"),
        "support_distance": support.get("distance_from_support_pct"),
        "resistance_distance": support.get("distance_to_resistance_pct"),
        "atr": float(latest["ATR"]), "breakout_probability": 0,
    }
    buffer_ = float(latest["ATR"]) * float(getattr(settings, "lightweight_stop_atr_buffer", .2))
    stop = float(support["support"]) - buffer_ if support.get("support") is not None else None
    target = float(support["resistance"]) if support.get("resistance") is not None else None
    risk = close - stop if stop is not None else None
    reward = target - close if target is not None else None
    rr = reward / risk if risk is not None and reward is not None and risk > 0 and reward >= 0 else None
    entry.update({
        "risk": round(risk, 2) if risk is not None else None,
        "reward": round(reward, 2) if reward is not None else None,
        "risk_reward": round(rr, 2) if rr is not None else 0.0,
        "quality": "GOOD" if rr is not None and rr >= 2 else "AVERAGE"
        if rr is not None and rr >= 1.5 else "POOR",
    })
    context.lightweight_risk_reward = {
        "entry": close, "stop": stop, "target": target, "risk_reward": rr,
    }

    decision_started = perf_counter()
    candlestick = _basic_candlestick(prepared)
    analysis_object = SimpleNamespace(score=technical_score)
    setup = SetupEntryEvaluator.evaluate(
        prepared, analysis_object, entry, breakout, candlestick, settings=settings,
    )
    decision = DecisionEngine.decide({
        "analysis": analysis_object, "entry": entry, "breakout": breakout,
        "setup_evaluation": setup,
    })
    decision_seconds = perf_counter() - decision_started

    quality = _market_quality(prepared)
    liquidity_started = perf_counter()
    liquidity, trust = _liquidity_trust(latest, quality)
    liquidity_seconds = perf_counter() - liquidity_started
    recommendation = ScoreEngine.recommendation(technical_score)
    trend_direction = (
        "STRONG BULLISH" if components["trend"] >= 80 else "BULLISH" if components["trend"] >= 60
        else "NEUTRAL" if components["trend"] >= 40 else "BEARISH"
        if components["trend"] >= 20 else "STRONG BEARISH"
    )
    analysis = {
        "symbol": symbol, "current_price": close,
        "ema20": float(latest["EMA20"]), "ema50": float(latest["EMA50"]),
        "ema200": float(latest["EMA200"]), "trend": trend_direction,
        "rsi": float(latest["RSI"]), "rsi_signal": setup["momentum_label"],
        "macd": float(latest["MACD"]), "macd_signal_line": float(latest["MACD_SIGNAL"]),
        "macd_histogram": float(latest["MACD_HISTOGRAM"]), "macd_signal": setup["momentum_label"],
        "atr": float(latest["ATR"]), "expected_low": close - float(latest["ATR"]),
        "expected_high": close + float(latest["ATR"]), "volume": int(latest["Volume"]),
        "average_volume": float(latest["AVG_VOLUME"]), "relative_volume": float(latest["RVOL"]),
        "volume_signal": str(latest.get("VOLUME_SIGNAL", "")),
        "volume_state": str(latest.get("VOLUME_STATE", latest.get("VOLUME_SIGNAL", ""))),
        "volume_progress": (
            float(latest["VOLUME_PROGRESS"]) if pd.notna(latest.get("VOLUME_PROGRESS")) else None
        ),
        "projected_volume": (
            float(latest["PROJECTED_VOLUME"]) if pd.notna(latest.get("PROJECTED_VOLUME")) else None
        ),
        "volume_confidence": (
            float(latest["VOLUME_CONFIDENCE"]) if pd.notna(latest.get("VOLUME_CONFIDENCE")) else None
        ),
        "score": technical_score,
        "max_score": 100, "recommendation": recommendation,
    }
    trade_plan = {
        "entry": round(close, 2), "stop_loss": round(stop, 2) if stop is not None else 0.0,
        "target1": round(target, 2) if target is not None else 0.0,
        "target2": round(close + 2 * (risk or 0), 2), "target3": round(close + 3 * (risk or 0), 2),
        "risk": round(risk, 2) if risk is not None else 0.0,
        "reward": round(reward, 2) if reward is not None else 0.0,
        "expected_reward": round(reward, 2) if reward is not None else 0.0,
        "risk_reward": round(rr, 2) if rr is not None else 0.0,
        "quality": entry["quality"], "target_basis": "LIGHTWEIGHT_ROLLING_RESISTANCE",
        "diagnostics": [],
    }
    timings = {
        "lightweight_indicator_seconds": round(indicator_seconds, 6),
        "lightweight_support_seconds": round(support_seconds, 6),
        "setup_decision_seconds": round(decision_seconds, 6),
        "liquidity_trust_seconds": round(liquidity_seconds, 6),
        "total_seconds": round(perf_counter() - total_started, 6),
    }
    context.timings.update(timings)
    candidate = {
        "symbol": symbol, "action": decision["action"], "confidence": decision["confidence"],
        "technical_score": technical_score, "recommendation": recommendation,
        "current_price": close, "stock_liquidity": liquidity, "trust": trust,
        "historical_gap_factor": round(1 + min(1, quality["large_gap_days"] / 10), 3),
        "risk_reward": trade_plan["risk_reward"], "candlestick": candlestick,
        "setup_evaluation": setup, "reason": decision["reason"], "trade_plan": trade_plan,
        "entry_report": entry, "analysis_report": {
            "analysis": analysis, "entry": entry, "breakout": breakout,
            "candlestick": candlestick, "setup_evaluation": setup,
            "supply_demand": {}, "price_action": {}, "intraday_recovery": {},
        },
        "position_size": {}, "market_quality": quality, "_analysis_context": context,
        "data_health": {
            "state": "LIVE" if live_candle is not None else "CACHED",
            "live_quote_timestamp": (
                live_candle.get("timestamp") or live_candle.get("Timestamp")
                if live_candle else None
            ),
            "latest_completed_candle": str(prepared.index[-2]),
            "live_candle_available": live_candle is not None,
        },
        "discovery_metrics": {
            "daily_return_percent": candle_metrics["daily_return"],
            "opening_gap_percent": candle_metrics["opening_gap_percent"],
            "relative_volume": float(latest["RVOL"]),
            "volume_state": analysis["volume_state"],
            "volume_confidence": analysis["volume_confidence"],
            "projected_volume": analysis["projected_volume"],
            "breakout_confirmed": breakout["confirmed"],
            "distance_to_resistance_percent": support.get("distance_to_resistance_pct"),
            "close_location": round(candle_metrics["close_location"], 4),
            "ema20_extension_atr": round(candle_metrics["extension_atr"], 4),
            "daily_range_contraction": (
                round(daily_range_contraction, 4)
                if daily_range_contraction is not None else None
            ),
            "stabilized_discovery_score": round(max(0, min(100,
                (25 if -.5 <= candle_metrics["daily_return"] <= 1.5 else 0)
                + (20 if candle_metrics["extension_atr"] <= .75 else
                   10 if candle_metrics["extension_atr"] <= 1.0 else 0)
                + (15 if 50 <= float(latest["RSI"]) <= 68 else 5 if 45 <= float(latest["RSI"]) <= 72 else 0)
                + (15 if float(latest["MACD"]) > float(latest["MACD_SIGNAL"]) else 0)
                + (15 if daily_range_contraction is not None and daily_range_contraction <= .85 else 0)
                + (10 if close > float(latest["EMA20"]) else 0)
            )), 2),
        },
        "lightweight_screen": {
            "components": components, "support": support, "timings": timings,
            "reason_codes": list(support["reason_codes"]),
        },
    }
    return LightweightScreenResult(
        symbol=symbol, technical_score=technical_score,
        technical_recommendation=recommendation, trend_score=components["trend"],
        momentum_score=components["momentum"], volume_score=components["volume"],
        volatility_score=components["volatility"], support_location_score=components["support"],
        setup_category=setup["stage_1"]["category"], decision_action=decision["action"],
        liquidity_score=float(liquidity["score"]), trust_score=float(trust["score"]),
        basic_risk_reward=round(rr, 2) if rr is not None else None,
        entry_confirmation=setup["entry_confirmation"], lightweight_support=support,
        reason_codes=list(support["reason_codes"]), warnings=[], timings=timings,
        context=context, candidate=candidate,
    )
