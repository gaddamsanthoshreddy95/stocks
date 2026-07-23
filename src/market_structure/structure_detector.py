"""Non-repainting market-structure events based on confirmed pivots."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.analysis.candle_utils import atr_series, normalise_ohlcv
from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig
from src.market_structure.support_resistance import SupportResistanceEngine


class MarketStructureDetector:
    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config = config

    def analyze(self, data: pd.DataFrame) -> dict[str, Any]:
        df = normalise_ohlcv(data)
        pivots = SupportResistanceEngine(self.config)._pivots(df)
        events: list[dict[str, Any]] = []
        previous = {"HIGH": None, "LOW": None}
        latest_class = {"HIGH": None, "LOW": None}
        structure = "RANGE"
        for pivot in pivots:
            prior = previous[pivot["type"]]
            if prior:
                if pivot["type"] == "HIGH":
                    event_type = "HH" if pivot["price"] > prior["price"] else "LH"
                else:
                    event_type = "HL" if pivot["price"] > prior["price"] else "LL"
                before = structure
                latest_class[pivot["type"]] = event_type
                if latest_class == {"HIGH": "HH", "LOW": "HL"}:
                    structure = "BULLISH"
                elif latest_class == {"HIGH": "LH", "LOW": "LL"}:
                    structure = "BEARISH"
                elif all(latest_class.values()):
                    structure = "TRANSITION"
                events.append(self._event(event_type, pivot, prior, before, structure, 75))
            previous[pivot["type"]] = pivot

        events.extend(self._break_events(df, pivots, events))
        failures = self._failures(df, pivots)
        events.extend(failures)
        events.sort(key=lambda event: event["confirmation_timestamp"])
        return {"structure": structure, "events": events, "confirmed_pivots": pivots,
                "tentative_pivots": self._tentative(df), "latest_event": events[-1] if events else None}

    def _break_events(self, df: pd.DataFrame, pivots: list[dict[str, Any]],
                      swing_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[tuple[Any, str, dict, float]] = []
        for pivot in pivots:
            position = df.index.get_loc(pivot["confirmation_timestamp"])
            later = df.iloc[position + 1:]
            crossed = later[later["Close"] > pivot["price"]] if pivot["type"] == "HIGH" else later[later["Close"] < pivot["price"]]
            if crossed.empty:
                continue
            timestamp = crossed.index[0]
            direction = "BULLISH" if pivot["type"] == "HIGH" else "BEARISH"
            candidates.append((timestamp, direction, pivot, float(crossed.iloc[0]["Close"])))
        out, seen, last_direction = [], set(), None
        for timestamp, direction, pivot, price in sorted(candidates, key=lambda item: item[0]):
            if (timestamp, direction) in seen:
                continue
            seen.add((timestamp, direction))
            prior_structure = next(
                (e["structure_after"] for e in reversed(sorted(
                    [x for x in swing_events if x["confirmation_timestamp"] <= timestamp],
                    key=lambda x: x["confirmation_timestamp"]))), "RANGE")
            against_structure = ((prior_structure == "BULLISH" and direction == "BEARISH")
                                 or (prior_structure == "BEARISH" and direction == "BULLISH"))
            kind = "CHOCH" if against_structure or (last_direction and last_direction != direction) else "BOS"
            synthetic = {"timestamp": timestamp, "confirmation_timestamp": timestamp, "price": price}
            out.append(self._event(kind, synthetic, pivot, prior_structure, direction, 82,
                                   f"{kind} {direction.lower()} close beyond confirmed swing."))
            last_direction = direction
        return out

    @staticmethod
    def _event(kind: str, pivot: dict, prior: dict | None, before: str, after: str,
               confidence: float, explanation: str | None = None) -> dict[str, Any]:
        return {"event_type": kind, "timestamp": pivot["timestamp"], "price": pivot["price"],
                "previous_related_swing": prior, "confirmation_timestamp": pivot["confirmation_timestamp"],
                "structure_before": before, "structure_after": after, "confidence": confidence,
                "explanation": explanation or f"Confirmed {kind} relative to the previous {pivot.get('type', 'related')} swing."}

    def _tentative(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        n = self.config.pivot_lookback
        if len(df) <= n:
            return []
        tail = df.iloc[-n:]
        return [{"type": "HIGH", "timestamp": tail["High"].idxmax(), "price": float(tail["High"].max()), "confirmed": False},
                {"type": "LOW", "timestamp": tail["Low"].idxmin(), "price": float(tail["Low"].min()), "confirmed": False}]

    def _failures(self, df: pd.DataFrame, pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        atrs, out = atr_series(df, self.config.atr_period), []
        for kind in ("HIGH", "LOW"):
            same_type = [pivot for pivot in pivots if pivot["type"] == kind]
            for first, second in zip(same_type, same_type[1:]):
                pos = df.index.get_loc(second["confirmation_timestamp"])
                tolerance = float(atrs.iloc[pos]) * self.config.gap_tolerance_atr
                if kind == "HIGH" and second["price"] > first["price"] and float(df.iloc[pos]["Close"]) < first["price"] + tolerance:
                    out.append(self._event("FAILED_HIGHER_HIGH", second, first, "BULLISH", "TRANSITION", 70))
                if kind == "LOW" and second["price"] < first["price"] and float(df.iloc[pos]["Close"]) > first["price"] - tolerance:
                    out.append(self._event("FAILED_LOWER_LOW", second, first, "BEARISH", "TRANSITION", 70))
        return out
