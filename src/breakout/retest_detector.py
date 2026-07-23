"""Breakout/retest state machine with confirmation-only entries."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.analysis.candle_utils import atr_series, candle, normalise_ohlcv, relative_volume
from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig


class RetestDetector:
    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config = config

    def detect(self, data: pd.DataFrame, breakout: dict[str, Any],
               zones: list[dict[str, Any]] | None = None,
               structure: str | None = None) -> dict[str, Any] | None:
        df = normalise_ohlcv(data)
        if not breakout or breakout.get("quality") not in ("VALID", "STRONG", "EXTENDED"):
            return None
        try:
            start = df.index.get_loc(breakout["breakout_candle"])
        except KeyError:
            return None
        zone, bullish = breakout["zone_crossed"], breakout["direction"] == "BULLISH"
        atrs = atr_series(df, self.config.atr_period)
        end = min(len(df), start + self.config.retest_max_bars + 2)
        for i in range(start + 1, end):
            bar, atr = candle(df.iloc[i], float(atrs.iloc[i])), float(atrs.iloc[i])
            tolerance = atr * self.config.retest_tolerance_atr
            touched = bar["low"] <= zone["upper"] + tolerance if bullish else bar["high"] >= zone["lower"] - tolerance
            if not touched:
                continue
            failed = bar["close"] < zone["lower"] if bullish else bar["close"] > zone["upper"]
            exact = (bar["low"] <= zone["midpoint"] <= bar["high"])
            retest_type = ("FAILED_RETEST" if failed else "EXACT_ZONE_RETEST" if exact
                           else "CLOSE_INSIDE_RETEST" if zone["lower"] <= bar["close"] <= zone["upper"]
                           else "WICK_RETEST" if (bar["low"] <= zone["upper"] if bullish else bar["high"] >= zone["lower"])
                           else "SHALLOW_RETEST")
            if failed:
                return self._result(df, breakout, i, None, retest_type, 20, atr, None, zones)
            for confirmation in range(i, min(i + 3, len(df))):
                confirm = candle(df.iloc[confirmation], float(atrs.iloc[confirmation]))
                directional = confirm["bullish"] if bullish else confirm["bearish"]
                held = confirm["close"] > zone["upper"] if bullish else confirm["close"] < zone["lower"]
                if directional and held:
                    contraction = relative_volume(df, i) <= max(1.1, breakout.get("volume_ratio", 1))
                    expansion = relative_volume(df, confirmation) >= relative_volume(df, i)
                    confidence = min(95, 65 + 10 * contraction + 10 * expansion
                                     + (10 if breakout["quality"] == "STRONG" else 0))
                    if structure == breakout["direction"]:
                        confidence = min(95, confidence + 5)
                    return self._result(df, breakout, i, confirmation, retest_type,
                                        confidence, atr, confirm["close"], zones)
            return self._result(df, breakout, i, None, "FAILED_RETEST", 25, atr, None, zones)
        elapsed = len(df) - start - 1
        if elapsed >= self.config.no_retest_min_bars:
            last = candle(df.iloc[-1], float(atrs.iloc[-1]))
            continued = last["close"] > zone["upper"] if bullish else last["close"] < zone["lower"]
            if continued:
                return self._result(df, breakout, len(df) - 1, len(df) - 1,
                                    "NO_RETEST_CONTINUATION", 55, float(atrs.iloc[-1]),
                                    last["close"], zones)
        return None

    def _result(self, df: pd.DataFrame, breakout: dict[str, Any], retest_i: int,
                confirmation_i: int | None, retest_type: str, confidence: float,
                atr: float, entry: float | None,
                zones: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        bullish, zone = breakout["direction"] == "BULLISH", breakout["zone_crossed"]
        stop = zone["lower"] - atr * self.config.stop_atr_buffer if bullish else zone["upper"] + atr * self.config.stop_atr_buffer
        risk = abs((entry or float(df.iloc[retest_i]["Close"])) - stop)
        zone_targets = sorted(
            [z["midpoint"] for z in zones or [] if z["type"] == ("RESISTANCE" if bullish else "SUPPORT")
             and entry is not None and ((bullish and z["midpoint"] > entry) or (not bullish and z["midpoint"] < entry))],
            reverse=not bullish)
        targets = ([round(x, 4) for x in zone_targets[:3]] if zone_targets else
                   [round((entry + risk * multiple) if bullish else (entry - risk * multiple), 4)
                    for multiple in (1.5, 2, 3)] if entry is not None else [])
        ratios = [round(abs(target - entry) / max(risk, 1e-12), 3) for target in targets] if entry is not None else []
        return {"breakout_timestamp": breakout["breakout_candle"], "retest_timestamp": df.index[retest_i],
                "confirmation_timestamp": df.index[confirmation_i] if confirmation_i is not None else None,
                "retest_type": retest_type, "retest_quality": "STRONG" if confidence >= 80 else
                "VALID" if confidence >= 60 else "FAILED", "entry_price": entry,
                "stop_loss_suggestion": round(stop, 4), "target_suggestions": targets,
                "risk_reward_ratios": ratios, "confidence": confidence,
                "retest_volume_ratio": round(relative_volume(df, retest_i), 3),
                "continuation_volume_ratio": round(relative_volume(df, confirmation_i), 3)
                if confirmation_i is not None else None,
                "explanation": f"{retest_type.replace('_', ' ').title()} after {breakout['direction'].lower()} breakout."}
