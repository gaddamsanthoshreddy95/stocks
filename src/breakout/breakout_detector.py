"""Bidirectional zone breakout, breakdown and fakeout detection."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.analysis.candle_utils import atr_series, candle, normalise_ohlcv, relative_volume
from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig
from src.market_structure.support_resistance import SupportResistanceEngine


class BreakoutDetector:
    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config = config

    @classmethod
    def analyze(cls, df: pd.DataFrame) -> dict[str, Any]:
        """Legacy summary enriched with the latest event and all events."""
        return cls().detect(df)

    def detect(self, data: pd.DataFrame, zones: list[dict[str, Any]] | None = None,
               lookback_bars: int | None = None) -> dict[str, Any]:
        df = normalise_ohlcv(data)
        if len(df) < 2:
            return {"confirmed": False, "events": [], "quality": "NONE", "conditions": {}}
        zones = zones if zones is not None else SupportResistanceEngine(self.config).analyze(df)["zones"]
        atrs, events = atr_series(df, self.config.atr_period), []
        start = max(1, len(df) - lookback_bars) if lookback_bars else 1
        for i in range(start, len(df)):
            current, previous = candle(df.iloc[i], float(atrs.iloc[i])), candle(df.iloc[i - 1], float(atrs.iloc[i]))
            for zone in zones:
                if zone.get("confirmation_timestamp") is not None and zone["confirmation_timestamp"] > df.index[i]:
                    continue
                event = self._event(df, i, current, previous, zone, float(atrs.iloc[i]), zones)
                if event:
                    if i + 1 < len(df) and event["quality"] != "FALSE_BREAKOUT":
                        follow = candle(df.iloc[i + 1], float(atrs.iloc[i + 1]))
                        bullish = event["direction"] == "BULLISH"
                        returned = follow["close"] <= zone["upper"] if bullish else follow["close"] >= zone["lower"]
                        if returned:
                            event["quality"] = "FALSE_BREAKOUT"
                            event["confidence"] = 18
                            event["reason_codes"].append("IMMEDIATE_RETURN_INSIDE_ZONE")
                            event["confirmation_candle"] = df.index[i + 1]
                        else:
                            event["confirmation_candle"] = df.index[i + 1]
                            event["condition_results"]["following_close_holds"] = True
                            event["confidence"] = min(95, event["confidence"] + 5)
                    elif self.config.require_breakout_confirmation and i + 1 >= len(df):
                        event["quality"] = "WEAK"
                        event["confidence"] = min(event["confidence"], 45)
                        event["reason_codes"].append("AWAITING_CONFIRMATION_CLOSE")
                    events.append(event)
        latest = events[-1] if events else None
        resistance = min((z["midpoint"] for z in zones if z["type"] == "RESISTANCE"
                          and z["midpoint"] > float(df.iloc[-1]["Close"])), default=None)
        result = {"confirmed": bool(latest and latest["quality"] in ("VALID", "STRONG")),
                  "events": events, "latest_event": latest, "quality": latest["quality"] if latest else "NONE",
                  "score": int(bool(latest)), "total_conditions": 1, "conditions": latest["condition_results"] if latest else {},
                  "resistance": resistance, "broken_resistance": latest["zone_crossed"]["midpoint"]
                  if latest and latest["direction"] == "BULLISH" else None}
        if latest:
            result.update(latest)
        return result

    def _event(self, df: pd.DataFrame, i: int, bar: dict, previous: dict,
               zone: dict[str, Any], atr: float,
               zones: list[dict[str, Any]]) -> dict[str, Any] | None:
        bullish = zone["type"] == "RESISTANCE"
        crossed_wick = bar["high"] > zone["upper"] if bullish else bar["low"] < zone["lower"]
        closed_outside = bar["close"] > zone["upper"] if bullish else bar["close"] < zone["lower"]
        was_inside = previous["close"] <= zone["upper"] if bullish else previous["close"] >= zone["lower"]
        if not crossed_wick or not was_inside:
            return None
        distance = ((bar["close"] - zone["upper"]) if bullish else (zone["lower"] - bar["close"])) / max(atr, 1e-12)
        close_location = ((bar["close"] - bar["low"]) / max(bar["range"], 1e-12) if bullish
                          else (bar["high"] - bar["close"]) / max(bar["range"], 1e-12))
        wick = bar["upper_wick"] if bullish else bar["lower_wick"]
        volume = relative_volume(df, i)
        confirmations = zone.get("pivot_confirmations")
        confirmed_touches = (sum(timestamp <= df.index[i] for timestamp in confirmations)
                             if confirmations is not None else zone.get("touches", 2))
        effective_zone_strength = min(float(zone["strength_score"]),
                                      30 + confirmed_touches * 12)
        conditions = {
            "close_outside": closed_outside,
            "minimum_distance": distance >= self.config.breakout_atr_threshold,
            "meaningful_body": bar["body"] >= atr * self.config.long_body_atr,
            "close_near_extreme": close_location >= .65,
            "volume_confirmation": volume >= self.config.min_relative_volume,
            "controlled_wick": wick <= max(bar["body"] * self.config.max_wick_body_ratio, atr * .1),
            "quality_zone": effective_zone_strength >= 50,
        }
        opposing = [z for z in zones if z["type"] == ("RESISTANCE" if bullish else "SUPPORT")
                    and ((bullish and z["lower"] > zone["upper"])
                         or (not bullish and z["upper"] < zone["lower"]))]
        next_zone_distance = min(
            (((z["lower"] - bar["close"]) if bullish else (bar["close"] - z["upper"])) / max(atr, 1e-12)
             for z in opposing if ((bullish and z["lower"] > bar["close"])
                                   or (not bullish and z["upper"] < bar["close"]))),
            default=float("inf"))
        conditions["clear_path_to_next_zone"] = next_zone_distance >= self.config.next_zone_min_atr
        reason_codes: list[str] = []
        if not closed_outside:
            quality, reason_codes = "FALSE_BREAKOUT", ["WICK_ONLY_FAKEOUT", "CLOSE_INSIDE_ZONE"]
        elif distance > self.config.breakout_extended_atr:
            quality, reason_codes = "EXTENDED", ["EXCESSIVE_ATR_EXTENSION"]
        else:
            passed = sum(conditions.values())
            quality = "STRONG" if passed >= 7 else "VALID" if passed >= 5 else "WEAK"
            if volume < self.config.min_relative_volume:
                reason_codes.append("LOW_RELATIVE_VOLUME")
            if wick > bar["body"] * self.config.max_wick_body_ratio:
                reason_codes.append("EXCESSIVE_REJECTION_WICK")
            if next_zone_distance < self.config.next_zone_min_atr:
                reason_codes.append("STRONG_OPPOSING_ZONE_TOO_CLOSE")
        confidence = {"FALSE_BREAKOUT": 20, "WEAK": 42, "VALID": 70, "STRONG": 88, "EXTENDED": 48}[quality]
        direction = "BULLISH" if bullish else "BEARISH"
        return {"direction": direction, "zone_crossed": zone, "breakout_candle": df.index[i],
                "confirmation_candle": None, "breakout_price": bar["close"],
                "invalidation_price": zone["lower"] if bullish else zone["upper"],
                "atr_normalized_breakout_distance": round(distance, 3), "volume_ratio": round(volume, 3),
                "quality": quality, "confidence": confidence, "reason_codes": reason_codes,
                "condition_results": conditions,
                "distance_to_next_zone_atr": round(next_zone_distance, 3) if next_zone_distance != float("inf") else None,
                "explanation": f"{quality.title()} {direction.lower()} zone cross: {sum(conditions.values())}/{len(conditions)} quality checks passed."}
