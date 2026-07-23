"""Objective pivot-pattern and structure reversal models."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.analysis.candle_utils import atr_series, normalise_ohlcv
from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig
from src.market_structure.structure_detector import MarketStructureDetector


class ReversalDetector:
    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config = config

    def detect(self, data: pd.DataFrame, breakouts: list[dict] | None = None,
               retests: list[dict] | None = None,
               structure_result: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        df = normalise_ohlcv(data)
        structure = structure_result or MarketStructureDetector(self.config).analyze(df)
        pivots, results = structure["confirmed_pivots"], []
        atr = float(atr_series(df, self.config.atr_period).iloc[-1]) if len(df) else 0
        alternating = pivots
        for i in range(2, len(alternating)):
            a, b, c = alternating[i - 2:i + 1]
            if a["type"] != c["type"] or a["type"] == b["type"]:
                continue
            similar = abs(a["price"] - c["price"]) <= atr * self.config.pattern_similarity_atr
            separated = c["index"] - a["index"] >= self.config.min_pattern_separation
            if similar and separated:
                name = "DOUBLE TOP" if a["type"] == "HIGH" else "DOUBLE BOTTOM"
                direction = "BEARISH" if a["type"] == "HIGH" else "BULLISH"
                result = self._result(name, "PATTERN_PLUS_BREAKOUT", direction, [a, b, c],
                                      b["price"], df, 72, ["SIMILAR_EXTREMES", "PIVOT_SEPARATION"],
                                      require_break=True)
                if result:
                    results.append(result)
        for i in range(4, len(alternating)):
            p = alternating[i - 4:i + 1]
            types = [x["type"] for x in p]
            if types == ["HIGH", "LOW", "HIGH", "LOW", "HIGH"]:
                shoulders = abs(p[0]["price"] - p[4]["price"]) <= atr * self.config.pattern_similarity_atr
                if shoulders and p[2]["price"] > max(p[0]["price"], p[4]["price"]):
                    neckline = (p[1]["price"] + p[3]["price"]) / 2
                    result = self._result("HEAD AND SHOULDERS", "PATTERN_PLUS_BREAKOUT", "BEARISH", p,
                                          neckline, df, 80, ["HEAD_ABOVE_SHOULDERS", "NECKLINE_DEFINED"],
                                          require_break=True)
                    if result:
                        results.append(result)
            if types == ["LOW", "HIGH", "LOW", "HIGH", "LOW"]:
                shoulders = abs(p[0]["price"] - p[4]["price"]) <= atr * self.config.pattern_similarity_atr
                if shoulders and p[2]["price"] < min(p[0]["price"], p[4]["price"]):
                    neckline = (p[1]["price"] + p[3]["price"]) / 2
                    result = self._result("INVERSE HEAD AND SHOULDERS", "PATTERN_PLUS_BREAKOUT", "BULLISH", p,
                                          neckline, df, 80, ["HEAD_BELOW_SHOULDERS", "NECKLINE_DEFINED"],
                                          require_break=True)
                    if result:
                        results.append(result)
        for event in structure["events"]:
            if event["event_type"] == "CHOCH":
                direction = str(event["structure_after"])
                model = "BREAKOUT_PLUS_STRUCTURE"
                result = self._result(event["event_type"], model, direction, [event],
                                      event["price"], df, event["confidence"], [event["event_type"]])
                if result:
                    results.append(result)
        # Objective structure sequence model: CHOCH followed by BOS in the new direction.
        structure_events = structure["events"]
        for choch in [e for e in structure_events if e["event_type"] == "CHOCH"]:
            later_bos = next((e for e in structure_events
                              if e["event_type"] == "BOS"
                              and e["confirmation_timestamp"] > choch["confirmation_timestamp"]
                              and e["structure_after"] == choch["structure_after"]), None)
            if later_bos:
                result = self._result("STRUCTURE REVERSAL", "BREAKOUT_PLUS_LH_LL_OR_HL_HH",
                                      str(choch["structure_after"]), [choch, later_bos],
                                      choch["price"], df, 84, ["CHOCH", "FOLLOW_THROUGH_BOS"])
                if result:
                    results.append(result)
        # New-extreme failure is actionable only after a directional zone break.
        for failure in [e for e in structure_events if e["event_type"].startswith("FAILED_")]:
            wanted = "BEARISH" if "HIGH" in failure["event_type"] else "BULLISH"
            trigger = next((b for b in breakouts or [] if b.get("direction") == wanted
                            and b.get("breakout_candle") >= failure["confirmation_timestamp"]
                            and b.get("quality") in ("VALID", "STRONG")), None)
            if trigger:
                result = self._result(failure["event_type"],
                                      "NEW_HIGH_FAILURE_PLUS_BREAKOUT" if wanted == "BEARISH"
                                      else "NEW_LOW_FAILURE_PLUS_BREAKOUT",
                                      wanted, [failure], trigger["breakout_price"], df, 82,
                                      [failure["event_type"], "CONFIRMED_ZONE_BREAK"])
                if result:
                    result["trigger_time"] = trigger["breakout_candle"]
                    result["confirmation_time"] = trigger.get("confirmation_candle") or trigger["breakout_candle"]
                    result["relevant_zone"] = trigger["zone_crossed"]
                    results.append(result)
        for retest in retests or []:
            if not retest or retest.get("retest_quality") not in ("VALID", "STRONG"):
                continue
            breakout = next((b for b in breakouts or []
                             if b.get("breakout_candle") == retest.get("breakout_timestamp")), None)
            if breakout:
                result = self._result("BREAK AND RETEST REVERSAL", "BREAK_PLUS_RETEST_REVERSAL",
                                      breakout["direction"], [breakout], breakout["breakout_price"],
                                      df, min(95, retest["confidence"] + 5),
                                      ["ZONE_BREAK", "RETEST_HOLD", "DIRECTIONAL_CONFIRMATION"])
                if result:
                    result["trigger_time"] = breakout["breakout_candle"]
                    result["confirmation_time"] = retest["confirmation_timestamp"]
                    result["relevant_zone"] = breakout["zone_crossed"]
                    result["entry"] = retest["entry_price"]
                    results.append(result)
        return results

    def _result(self, setup: str, model: str, direction: str, points: list[dict],
                neckline: float, df: pd.DataFrame, confidence: float, reasons: list[str],
                require_break: bool = False) -> dict[str, Any] | None:
        bullish = direction == "BULLISH"
        height = max(abs(p.get("price", neckline) - neckline) for p in points)
        trigger_time = points[-1].get("confirmation_timestamp", points[-1].get("timestamp"))
        if require_break:
            try:
                position = df.index.get_loc(trigger_time)
            except KeyError:
                return None
            later = df.iloc[position + 1:]
            crossed = later[later["Close"] > neckline] if bullish else later[later["Close"] < neckline]
            if crossed.empty:
                return None
            trigger_time = crossed.index[0]
        entry = neckline
        invalidation = max(p.get("price", neckline) for p in points) if not bullish else min(p.get("price", neckline) for p in points)
        return {"reversal_model": model, "setup": setup, "direction": direction,
                "setup_start_time": points[0].get("timestamp"), "trigger_time": trigger_time,
                "confirmation_time": trigger_time, "relevant_swing_points": points,
                "relevant_zone": None, "neckline": neckline, "entry": entry,
                "invalidation": invalidation,
                "targets": [entry + height if bullish else entry - height],
                "confidence": confidence, "component_scores": {"pattern": confidence, "structure": confidence},
                "explanation": f"{setup.title()} supports a {direction.lower()} {model.lower().replace('_', ' ')}.",
                "reason_codes": reasons}
