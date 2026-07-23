"""ATR-normalised support/resistance zones and three-candle rejections."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.analysis.candle_utils import atr_series, candle, normalise_ohlcv, prior_trend, relative_volume
from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig


class SupportResistanceEngine:
    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config = config

    @classmethod
    def calculate(cls, df: pd.DataFrame) -> dict[str, Any]:
        """Backward-compatible summary plus rich ``zones`` and ``rejections``."""
        return cls().analyze(df)

    def analyze(self, data: pd.DataFrame) -> dict[str, Any]:
        df = normalise_ohlcv(data)
        if df.empty:
            return {"support": None, "resistance": None, "zones": [], "rejections": []}
        atrs = atr_series(df, self.config.atr_period)
        pivots = self._pivots(df)
        pivots.extend(self._consolidation_pivots(df, atrs))
        zones = self._zones(df, pivots, float(atrs.iloc[-1]))
        close = float(df.iloc[-1]["Close"])
        supports = [z for z in zones if z["type"] == "SUPPORT" and z["midpoint"] < close]
        resistances = [z for z in zones if z["type"] == "RESISTANCE" and z["midpoint"] > close]
        broken = [z for z in zones if z["type"] == "RESISTANCE" and z["midpoint"] <= close]
        supports.sort(key=lambda z: close - z["midpoint"])
        resistances.sort(key=lambda z: z["midpoint"] - close)
        rejections = self.detect_rejections(df, zones)
        resistance_levels = [z["midpoint"] for z in resistances]
        support = supports[0]["midpoint"] if supports else None
        resistance = resistances[0]["midpoint"] if resistances else None
        return {
            "support": support, "resistance": resistance,
            "next_resistance": resistance_levels[1] if len(resistance_levels) > 1 else None,
            "resistance_levels": resistance_levels,
            "broken_resistance": max((z["midpoint"] for z in broken), default=None),
            "support_distance": round((close - support) / close * 100, 2) if support else None,
            "resistance_distance": round((resistance - close) / close * 100, 2) if resistance else None,
            "zones": zones, "rejections": rejections, "pivots": pivots,
        }

    def _consolidation_pivots(self, df: pd.DataFrame, atrs: pd.Series) -> list[dict[str, Any]]:
        """Add confirmed recent range boundaries as low-weight zone seeds."""
        n = self.config.consolidation_lookback
        if len(df) < n + 1:
            return []
        window = df.iloc[-n - 1:-1]
        atr = max(float(atrs.iloc[-2]), 1e-12)
        if float(window["High"].max() - window["Low"].min()) > atr * self.config.consolidation_max_atr:
            return []
        confirmation = df.index[-1]
        high_i, low_i = window["High"].idxmax(), window["Low"].idxmin()
        return [
            {"type": "HIGH", "price": float(window.loc[high_i, "High"]), "timestamp": high_i,
             "confirmation_timestamp": confirmation, "index": df.index.get_loc(high_i),
             "source": "CONSOLIDATION"},
            {"type": "LOW", "price": float(window.loc[low_i, "Low"]), "timestamp": low_i,
             "confirmation_timestamp": confirmation, "index": df.index.get_loc(low_i),
             "source": "CONSOLIDATION"},
        ]

    def _pivots(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        n, out = self.config.pivot_lookback, []
        for i in range(n, len(df) - n):
            high, low = float(df.iloc[i]["High"]), float(df.iloc[i]["Low"])
            before_after = df.iloc[i - n:i + n + 1]
            # A pivot is emitted only at i+n (confirmation timestamp), avoiding repainting.
            if high >= float(before_after["High"].max()):
                out.append({"type": "HIGH", "price": high, "timestamp": df.index[i],
                            "confirmation_timestamp": df.index[i + n], "index": i})
            if low <= float(before_after["Low"].min()):
                out.append({"type": "LOW", "price": low, "timestamp": df.index[i],
                            "confirmation_timestamp": df.index[i + n], "index": i})
        return out

    def _zones(self, df: pd.DataFrame, pivots: list[dict[str, Any]], atr: float) -> list[dict[str, Any]]:
        width = max(atr * self.config.zone_width_atr, float(df.iloc[-1]["Close"]) * .001)
        clusters: list[list[dict[str, Any]]] = []
        for pivot in pivots:
            target = next((c for c in clusters if c[0]["type"] == pivot["type"]
                           and abs(sum(x["price"] for x in c) / len(c) - pivot["price"]) <= width), None)
            if target is None:
                clusters.append([pivot])
            else:
                target.append(pivot)
        zones = []
        for cluster in clusters:
            midpoint = sum(p["price"] for p in cluster) / len(cluster)
            lower, upper = midpoint - width / 2, midpoint + width / 2
            touch_mask = (df["Low"] <= upper) & (df["High"] >= lower)
            touches = list(df.index[touch_mask])
            if cluster[0]["type"] == "LOW":
                rejection_count = int(((df["Close"] > upper) & (df["Low"] <= upper)).sum())
                breakout_count = int((df["Close"] < lower).sum())
                successful_retests = int(((df["Close"].shift(1) < lower)
                                          & (df["High"] >= lower)
                                          & (df["Close"] < lower)).sum())
            else:
                rejection_count = int(((df["Close"] < lower) & (df["High"] >= lower)).sum())
                breakout_count = int((df["Close"] > upper).sum())
                successful_retests = int(((df["Close"].shift(1) > upper)
                                          & (df["Low"] <= upper)
                                          & (df["Close"] > upper)).sum())
            volume = sum(relative_volume(df, p["index"]) for p in cluster) / len(cluster)
            touch_score = min(35, len(cluster) * 12)
            weakened = max(0, len(cluster) - self.config.max_zone_touches_before_weakening) * 5
            recency = (len(df) - 1 - cluster[-1]["index"]) / max(len(df), 1)
            score = max(0, min(100, 30 + touch_score + min(15, rejection_count * 3)
                               + min(10, max(0, volume - 1) * 10) + (10 if recency < .2 else 0) - weakened))
            factors = ["clustered pivots", f"{len(cluster)} confirmed touches"]
            latest = df.iloc[-1]
            for label in ("EMA20", "EMA50", "EMA200"):
                value = latest.get(label)
                if value is not None and pd.notna(value) and abs(float(value) - midpoint) <= atr * self.config.ema_confluence_atr:
                    factors.append(f"{label} confluence")
            vwap = latest.get("VWAP")
            if vwap is not None and pd.notna(vwap) and abs(float(vwap) - midpoint) <= atr * self.config.vwap_confluence_atr:
                factors.append("VWAP confluence")
            magnitude = 10 ** max(0, len(str(int(abs(midpoint)))) - 2)
            if abs(midpoint - round(midpoint / magnitude) * magnitude) <= width:
                factors.append("round-number proximity")
            if any(p.get("source") == "CONSOLIDATION" for p in cluster):
                factors.append("consolidation boundary")
            if successful_retests:
                factors.append(f"{successful_retests} successful retests")
            if volume > 1.2:
                factors.append("relative-volume confirmation")
            confluence_bonus = min(12, max(0, len(factors) - 2) * 2)
            score = min(100, score + confluence_bonus
                        + min(8, successful_retests * 2))
            zones.append({
                "type": "SUPPORT" if cluster[0]["type"] == "LOW" else "RESISTANCE",
                "lower": round(lower, 4), "upper": round(upper, 4), "midpoint": round(midpoint, 4),
                "touches": len(cluster), "first_touch_time": cluster[0]["timestamp"],
                "last_touch_time": cluster[-1]["timestamp"], "recency": round(recency, 4),
                "confirmation_timestamp": max(p["confirmation_timestamp"] for p in cluster),
                "pivot_confirmations": [p["confirmation_timestamp"] for p in cluster],
                "rejection_count": rejection_count, "breakout_count": breakout_count,
                "successful_retest_count": successful_retests, "volume_confirmation": round(volume, 3),
                "confluence_factors": factors, "strength_score": round(score, 2),
                "classification": "STRONG" if score >= 75 else "STANDARD" if score >= 50 else "WEAK",
                "explanation": f"{cluster[0]['type'].title()} pivot cluster with {len(cluster)} confirmed pivots.",
            })
        return sorted(zones, key=lambda z: z["midpoint"])

    def detect_rejections(self, df: pd.DataFrame, zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(df) < 3:
            return []
        atrs, out = atr_series(df, self.config.atr_period), []
        # Zones are built with all confirmed information in this dataframe.
        # Evaluating only the latest completed three-candle window ensures a
        # historical rejection never uses a zone learned from later candles;
        # callers obtain history safely by passing successive prefixes.
        for i in range(len(df) - 1, len(df)):
            if i < 2:
                continue
            a = max(float(atrs.iloc[i]), 1e-12)
            one, two, three = (candle(df.iloc[j], a) for j in range(i - 2, i + 1))
            for zone in zones:
                bearish = zone["type"] == "RESISTANCE"
                tested = (one["high"] >= zone["lower"] and two["high"] >= zone["lower"]) if bearish else (
                    one["low"] <= zone["upper"] and two["low"] <= zone["upper"])
                away = three["close"] < two["close"] if bearish else three["close"] > two["close"]
                if not tested or not away or one["body"] <= 0:
                    continue
                retrace = ((one["close"] - three["close"]) if bearish else
                           (three["close"] - one["close"])) / one["body"]
                raw = ("STRONG" if retrace >= self.config.rejection_strong_ratio else
                       "STANDARD" if retrace >= self.config.rejection_standard_ratio else "WEAK")
                wick = two["upper_wick"] if bearish else two["lower_wick"]
                adjustment = min(20, wick / a * 15) + min(10, relative_volume(df, i) * 4)
                adjustment += zone["strength_score"] * .15
                score = min(100, {"WEAK": 30, "STANDARD": 55, "STRONG": 75}[raw] + adjustment)
                out.append({"direction": "BEARISH" if bearish else "BULLISH", "zone": zone,
                            "candle_indexes": list(df.index[i - 2:i + 1]), "retracement_ratio": round(retrace, 3),
                            "raw_retracement_classification": raw, "adjusted_zone_rejection_strength": round(score, 2),
                            "classification": "STRONG" if score >= 75 else "STANDARD" if score >= 50 else "WEAK",
                            "trend_context": prior_trend(
                                df, i - 2, self.config.trend_lookback,
                                self.config.trend_min_change_ratio),
                            "explanation": f"{raw.title()} three-candle rejection adjusted for wick, volume and zone quality."})
        return out
