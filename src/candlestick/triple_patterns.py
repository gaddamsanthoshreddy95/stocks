"""Configurable three/four-candle patterns with auditable conditions."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.analysis.candle_utils import atr_series, candle, normalise_ohlcv, prior_trend, relative_volume
from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig


class TripleCandlePatternDetector:
    """Detect the requested star, abandoned-baby, soldier/crow and strike patterns."""

    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config = config

    def detect_all(self, data: pd.DataFrame) -> list[dict[str, Any]]:
        df = normalise_ohlcv(data)
        if len(df) < 3:
            return []
        atr = atr_series(df, self.config.atr_period)
        results: list[dict[str, Any]] = []
        for end in range(2, len(df)):
            result = self._three(df, atr, end)
            if result:
                results.append(result)
            if end >= 3:
                result = self._strike(df, atr, end)
                if result:
                    results.append(result)
        return results

    def detect(self, data: pd.DataFrame) -> dict[str, Any] | None:
        matches = self.detect_all(data)
        return matches[-1] if matches and matches[-1]["candle_indexes"][-1] == data.index[-1] else None

    def _make(self, name: str, direction: str, df: pd.DataFrame, indexes: list[int],
              conditions: dict[str, bool], base: float, atr: float) -> dict[str, Any] | None:
        if not all(conditions.values()):
            return None
        last = candle(df.iloc[indexes[-1]], atr)
        volume_ratio = relative_volume(df, indexes[-1])
        volume_adjustment = min(10.0, max(-5.0, (volume_ratio - 1) * 10))
        adjusted = min(100.0, max(0.0, base + volume_adjustment))
        bullish = direction == "BULLISH"
        lows = [float(df.iloc[i]["Low"]) for i in indexes]
        highs = [float(df.iloc[i]["High"]) for i in indexes]
        invalidation = min(lows) - atr * self.config.stop_atr_buffer if bullish else max(highs) + atr * self.config.stop_atr_buffer
        timestamps = [df.index[i] for i in indexes]
        return {
            "pattern": name, "direction": direction, "signal": "BUY" if bullish else "SELL",
            "raw_pattern_strength": round(base, 2), "base_strength": round(base, 2),
            "adjusted_strength": round(adjusted, 2), "strength": round(adjusted, 2),
            "confidence": round(adjusted, 2), "candle_indexes": timestamps,
            "confirmation_price": last["close"], "invalidation_price": round(invalidation, 4),
            "conditions": conditions, "condition_results": conditions,
            "volume_ratio": round(volume_ratio, 3),
            "explanation": f"{name.title()} confirmed with prior trend, candle geometry, location and penetration checks.",
            "context_validated": True,
        }

    def _three(self, df: pd.DataFrame, atrs: pd.Series, end: int) -> dict[str, Any] | None:
        cfg, a = self.config, max(float(atrs.iloc[end]), 1e-12)
        first, second, third = (candle(df.iloc[i], a) for i in range(end - 2, end + 1))
        if not all(x.get("valid") for x in (first, second, third)):
            return None
        trend = prior_trend(df, end - 2, cfg.trend_lookback, cfg.trend_min_change_ratio)
        doji = second["body_ratio"] <= cfg.doji_body_ratio
        first_long = first["body_ratio"] >= cfg.min_pattern_body_ratio
        penetration_up = third["close"] >= (first["open"] + first["close"]) / 2
        penetration_down = third["close"] <= (first["open"] + first["close"]) / 2
        near_below = second["high"] <= min(first["open"], first["close"]) + a * cfg.gap_tolerance_atr
        near_above = second["low"] >= max(first["open"], first["close"]) - a * cfg.gap_tolerance_atr
        separated_below = (second["high"] <= first["low"] + a * cfg.gap_tolerance_atr
                           and third["low"] >= second["high"] - a * cfg.gap_tolerance_atr)
        separated_above = (second["low"] >= first["high"] - a * cfg.gap_tolerance_atr
                           and third["high"] <= second["low"] + a * cfg.gap_tolerance_atr)
        common_bull = {"prior_downtrend": trend == "DOWNTREND", "first_bearish_long": first["bearish"] and first_long,
                       "middle_doji": doji, "third_bullish": third["bullish"], "penetration": penetration_up}
        common_bear = {"prior_uptrend": trend == "UPTREND", "first_bullish_long": first["bullish"] and first_long,
                       "middle_doji": doji, "third_bearish": third["bearish"], "penetration": penetration_down}
        candidates = [
            ("BULLISH ABANDONED BABY", "BULLISH", {**common_bull, "separated": separated_below}, 82),
            ("BEARISH ABANDONED BABY", "BEARISH", {**common_bear, "separated": separated_above}, 82),
            ("MORNING DOJI STAR", "BULLISH", {**common_bull, "middle_location": near_below}, 75),
            ("EVENING DOJI STAR", "BEARISH", {**common_bear, "middle_location": near_above}, 75),
        ]
        soldiers = all(x["bullish"] and x["body"] >= a * cfg.long_body_atr for x in (first, second, third))
        crows = all(x["bearish"] and x["body"] >= a * cfg.long_body_atr for x in (first, second, third))
        opens_inside_bull = first["open"] <= second["open"] <= first["close"] and second["open"] <= third["open"] <= second["close"]
        opens_inside_bear = first["close"] <= second["open"] <= first["open"] and second["close"] <= third["open"] <= second["open"]
        not_extended = third["range"] <= a * cfg.breakout_extended_atr
        candidates += [
            ("THREE WHITE SOLDIERS", "BULLISH", {"downtrend_or_base": trend in ("DOWNTREND", "RANGE"),
             "three_bullish_bodies": soldiers, "rising_closes": first["close"] < second["close"] < third["close"],
             "opens_inside_bodies": opens_inside_bull, "controlled_wicks": max(x["upper_wick"] for x in (first, second, third)) <= a * cfg.max_wick_body_ratio},
             72 if not_extended else 42),
            ("THREE BLACK CROWS", "BEARISH", {"uptrend_or_base": trend in ("UPTREND", "RANGE"),
             "three_bearish_bodies": crows, "falling_closes": first["close"] > second["close"] > third["close"],
             "opens_inside_bodies": opens_inside_bear, "controlled_wicks": max(x["lower_wick"] for x in (first, second, third)) <= a * cfg.max_wick_body_ratio},
             72 if not_extended else 42),
        ]
        for args in candidates:
            result = self._make(*args[:2], df, [end - 2, end - 1, end], args[2], args[3], a)
            if result:
                if args[0] in ("THREE WHITE SOLDIERS", "THREE BLACK CROWS"):
                    result["condition_results"]["third_candle_extension_acceptable"] = not_extended
                    result["conditions"]["third_candle_extension_acceptable"] = not_extended
                    if not not_extended:
                        result["explanation"] += " Confidence was heavily penalized because the third candle is ATR-extended."
                return result
        return None

    def _strike(self, df: pd.DataFrame, atrs: pd.Series, end: int) -> dict[str, Any] | None:
        a = max(float(atrs.iloc[end]), 1e-12)
        bars = [candle(df.iloc[i], a) for i in range(end - 3, end + 1)]
        if not all(x.get("valid") for x in bars):
            return None
        one, two, three, strike = bars
        declining = all(x["bearish"] for x in bars[:3]) and one["close"] > two["close"] > three["close"]
        rising = all(x["bullish"] for x in bars[:3]) and one["close"] < two["close"] < three["close"]
        trend = prior_trend(df, end - 3, self.config.trend_lookback,
                            self.config.trend_min_change_ratio)
        meaningful = all(x["body"] >= a * self.config.min_pattern_body_ratio for x in bars[:3])
        bullish = {"prior_decline": trend in ("DOWNTREND", "RANGE"),
                   "three_bearish": declining, "meaningful_bodies": meaningful,
                   "large_bullish_strike": strike["bullish"] and strike["body"] >= a * self.config.long_body_atr,
                   "open_near_third": strike["open"] <= three["close"] + a * self.config.gap_tolerance_atr,
                   "engulfs_sequence": strike["close"] >= one["open"]}
        bearish = {"prior_advance": trend in ("UPTREND", "RANGE"),
                   "three_bullish": rising, "meaningful_bodies": meaningful,
                   "large_bearish_strike": strike["bearish"] and strike["body"] >= a * self.config.long_body_atr,
                   "open_near_third": strike["open"] >= three["close"] - a * self.config.gap_tolerance_atr,
                   "engulfs_sequence": strike["close"] <= one["open"]}
        indexes = list(range(end - 3, end + 1))
        return (self._make("BEARISH THREE-LINE STRIKE", "BULLISH", df, indexes, bullish, 80, a)
                or self._make("BULLISH THREE-LINE STRIKE", "BEARISH", df, indexes, bearish, 80, a))
