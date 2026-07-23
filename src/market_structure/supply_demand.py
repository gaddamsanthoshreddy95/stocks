"""Price-action supply and demand zones.

Zones are deliberately represented as ranges rather than exact prices.  The
engine uses confirmed swing points, the candle base around the swing, departure
strength, volume and subsequent retests to produce an auditable zone score.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


class SupplyDemandEngine:
    """Detect and rank demand/supply zones from ordered OHLCV candles."""

    @staticmethod
    def _zone(frame: pd.DataFrame, index: int, kind: str, atr: float) -> dict[str, Any]:
        candle = frame.iloc[index]
        close = float(candle["Close"])
        open_ = float(candle["Open"])
        high = float(candle["High"])
        low = float(candle["Low"])
        width = max(atr * .35, close * .003, abs(close - open_))
        if kind == "DEMAND":
            lower, upper = low, min(high, max(open_, close) + width * .20)
            departure = (
                float(frame.iloc[index + 1:index + 4]["High"].max()) - upper
            ) / max(atr, .000001)
        else:
            lower, upper = max(low, min(open_, close) - width * .20), high
            departure = (
                lower - float(frame.iloc[index + 1:index + 4]["Low"].min())
            ) / max(atr, .000001)

        later = frame.iloc[index + 3:]
        if later.empty:
            touches = 0
        else:
            touches = int(((later["Low"] <= upper) & (later["High"] >= lower)).sum())
        average_volume = float(frame["Volume"].rolling(20, min_periods=1).mean().iloc[index])
        volume_ratio = float(candle["Volume"]) / max(average_volume, 1)
        freshness = max(0, 30 - touches * 10)
        departure_score = min(40, max(0, departure) * 16)
        volume_score = min(20, volume_ratio * 12)
        base_score = 10 if abs(close - open_) <= max(high - low, .000001) * .55 else 5
        score = round(min(100, freshness + departure_score + volume_score + base_score), 2)
        return {
            "type": kind,
            "lower": round(float(lower), 2),
            "upper": round(float(upper), 2),
            "midpoint": round(float(lower + upper) / 2, 2),
            "formed_at": frame.index[index].isoformat()
            if hasattr(frame.index[index], "isoformat") else str(frame.index[index]),
            "departure_atr": round(float(departure), 2),
            "formation_volume_ratio": round(volume_ratio, 2),
            "retests": touches,
            "fresh": touches == 0,
            "score": score,
        }

    @classmethod
    def analyze(cls, candles: pd.DataFrame, current_price: float | None = None,
                maximum_zones: int = 5) -> dict[str, Any]:
        required = {"Open", "High", "Low", "Close", "Volume"}
        if (candles is None or candles.empty or len(candles) < 10
                or not required.issubset(candles.columns)):
            return {
                "available": False, "demand_zones": [], "supply_zones": [],
                "nearest_demand": None, "nearest_supply": None,
                "reason": "Sufficient ordered OHLCV candles are unavailable.",
            }
        frame = candles.sort_index().dropna(subset=list(required)).copy()
        if len(frame) < 10:
            return {
                "available": False, "demand_zones": [], "supply_zones": [],
                "nearest_demand": None, "nearest_supply": None,
                "reason": "Too few complete candles remain for zone detection.",
            }
        price = float(current_price if current_price is not None else frame.iloc[-1]["Close"])
        if "ATR" in frame:
            atr = float(frame.iloc[-1]["ATR"] or 0)
        else:
            previous = frame["Close"].shift(1)
            true_range = pd.concat([
                frame["High"] - frame["Low"],
                (frame["High"] - previous).abs(),
                (frame["Low"] - previous).abs(),
            ], axis=1).max(axis=1)
            atr = float(true_range.rolling(14, min_periods=3).mean().iloc[-1])
        atr = max(atr, price * .005)

        demand: list[dict[str, Any]] = []
        supply: list[dict[str, Any]] = []
        lows, highs = frame["Low"], frame["High"]
        for index in range(2, len(frame) - 3):
            if lows.iloc[index] < lows.iloc[index - 2:index].min() and lows.iloc[index] <= lows.iloc[index + 1:index + 3].min():
                demand.append(cls._zone(frame, index, "DEMAND", atr))
            if highs.iloc[index] > highs.iloc[index - 2:index].max() and highs.iloc[index] >= highs.iloc[index + 1:index + 3].max():
                supply.append(cls._zone(frame, index, "SUPPLY", atr))

        demand = sorted(demand, key=lambda zone: (zone["score"], zone["formed_at"]), reverse=True)
        supply = sorted(supply, key=lambda zone: (zone["score"], zone["formed_at"]), reverse=True)
        actionable_demand = [zone for zone in demand if zone["lower"] <= price and zone["upper"] >= price - atr * 1.5]
        actionable_supply = [zone for zone in supply if zone["upper"] >= price and zone["lower"] <= price + atr * 4]
        nearest_demand = (
            min(actionable_demand, key=lambda zone: abs(price - zone["upper"]))
            if actionable_demand else None
        )
        nearest_supply = (
            min(actionable_supply, key=lambda zone: abs(zone["lower"] - price))
            if actionable_supply else None
        )
        return {
            "available": bool(demand or supply),
            "method": "CONFIRMED_SWING_BASE_DEPARTURE",
            "timeframe": "DAILY",
            "current_price": round(price, 2),
            "atr": round(atr, 2),
            "demand_zones": demand[:maximum_zones],
            "supply_zones": supply[:maximum_zones],
            "nearest_demand": nearest_demand,
            "nearest_supply": nearest_supply,
            "reason": None if demand or supply else "No confirmed price-action zones were found.",
        }
