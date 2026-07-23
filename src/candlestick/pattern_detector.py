"""Context-validated candlestick patterns for swing trading.

Geometry is never enough for a reversal.  Reversal patterns are emitted only
in the correct prior trend and their strength combines geometry, location,
volume, EMA/RSI, and MACD evidence.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


class PatternDetector:
    REVERSAL_LOOKBACK = 8
    SUPPORTED_PATTERNS = (
        "MORNING STAR", "BULLISH ENGULFING", "HAMMER", "PIERCING LINE", "BULLISH HARAMI",
        "THREE WHITE SOLDIERS", "EVENING STAR", "BEARISH ENGULFING", "SHOOTING STAR",
        "DARK CLOUD COVER", "BEARISH HARAMI", "THREE BLACK CROWS", "RISING THREE METHODS",
        "FALLING THREE METHODS", "DOJI", "SPINNING TOP", "TWEEZER TOP", "TWEEZER BOTTOM",
    )
    ADVANCED_PATTERNS = (
        "MORNING DOJI STAR", "BULLISH ABANDONED BABY", "BEARISH THREE-LINE STRIKE",
        "EVENING DOJI STAR", "BEARISH ABANDONED BABY", "BULLISH THREE-LINE STRIKE",
    )

    @staticmethod
    def _candle(row) -> dict[str, Any]:
        open_, close, high, low = (float(row[key]) for key in ("Open", "Close", "High", "Low"))
        body, span = abs(close - open_), max(high - low, 0.000001)
        return {"open": open_, "close": close, "high": high, "low": low, "body": body,
                "span": span, "upper": high - max(open_, close), "lower": min(open_, close) - low,
                "bull": close > open_, "bear": close < open_,
                "close_location": (close - low) / span}

    @classmethod
    def _context(cls, df: pd.DataFrame, pattern_size: int) -> dict[str, Any]:
        prior = df.iloc[:-pattern_size]
        window = prior.tail(cls.REVERSAL_LOOKBACK)
        closes = pd.to_numeric(window.get("Close"), errors="coerce").dropna()
        lows = pd.to_numeric(prior.tail(20).get("Low"), errors="coerce").dropna()
        highs = pd.to_numeric(prior.tail(20).get("High"), errors="coerce").dropna()
        current = cls._candle(df.iloc[-1])
        if len(closes) >= 4:
            change = (float(closes.iloc[-1]) - float(closes.iloc[0])) / max(abs(float(closes.iloc[0])), .000001)
            downward_steps = int((closes.diff().dropna() < 0).sum())
            upward_steps = int((closes.diff().dropna() > 0).sum())
            required_steps = max(2, math.ceil((len(closes) - 1) * .57))
            downtrend = change <= -.01 and downward_steps >= required_steps
            uptrend = change >= .01 and upward_steps >= required_steps
        else:
            change = 0.0
            downtrend = uptrend = False

        latest = df.iloc[-1]
        ema20 = float(latest.get("EMA20", current["close"]) or current["close"])
        rsi = float(latest.get("RSI", 50) or 50)
        macd = float(latest.get("MACD", 0) or 0)
        macd_signal = float(latest.get("MACD_SIGNAL", latest.get("MACD_SIGNAL_LINE", 0)) or 0)
        rvol = float(latest.get("RVOL", 0) or 0)
        true_ranges = (pd.to_numeric(df.tail(15)["High"], errors="coerce")
                       - pd.to_numeric(df.tail(15)["Low"], errors="coerce")).dropna()
        atr = float(latest.get("ATR", true_ranges.mean() if not true_ranges.empty else current["span"])
                    or current["span"])
        support = float(lows.min()) if not lows.empty else current["low"]
        resistance = float(highs.max()) if not highs.empty else current["high"]
        near_support = current["low"] <= support + max(atr, current["span"]) * .75
        near_resistance = current["high"] >= resistance - max(atr, current["span"]) * .75
        return {"downtrend": downtrend, "uptrend": uptrend, "trend_change": change,
                "ema20": ema20, "rsi": rsi, "macd": macd, "macd_signal": macd_signal,
                "rvol": rvol, "atr": atr, "near_support": near_support,
                "near_resistance": near_resistance, "pattern_high": current["high"],
                "pattern_low": current["low"]}

    @staticmethod
    def _result(pattern: str, signal: str, geometry: int, context: dict[str, Any],
                trend_valid: bool, location_valid: bool, continuation: bool = False) -> dict[str, Any]:
        confirmations: list[str] = []
        trend_score = 20 if trend_valid else 0
        location_score = 15 if location_valid else 0
        volume_score = 10 if context["rvol"] >= 1.5 else 5 if context["rvol"] >= 1 else 0
        bullish = signal == "BUY"
        ema_ok = (context["ema20"] >= 0 and
                  ((bullish and (context["downtrend"] or continuation))
                   or (not bullish and (context["uptrend"] or continuation))))
        rsi_ok = context["rsi"] <= 45 if bullish and not continuation else (
            context["rsi"] >= 60 if not bullish and not continuation else True)
        macd_ok = context["macd"] >= context["macd_signal"] if bullish else context["macd"] <= context["macd_signal"]
        momentum_score = 5 * int(rsi_ok) + 5 * int(macd_ok)
        if trend_valid: confirmations.append("validated prior trend")
        if location_valid: confirmations.append("support/resistance location")
        if volume_score: confirmations.append(f"relative volume {context['rvol']:.2f}x")
        if rsi_ok: confirmations.append(f"RSI {context['rsi']:.1f} confirms context")
        if macd_ok: confirmations.append("MACD confirms direction")
        if ema_ok: confirmations.append("EMA20 context aligned")
        strength = min(100, geometry + trend_score + location_score + volume_score + momentum_score)
        return {"pattern": pattern, "strength": strength, "signal": signal,
                "base_strength": geometry, "confirmations": confirmations,
                "context_validated": trend_valid and (location_valid or continuation),
                "pattern_high": context["pattern_high"], "pattern_low": context["pattern_low"],
                "component_scores": {"geometry": geometry, "trend": trend_score,
                                     "location": location_score, "volume": volume_score,
                                     "momentum": momentum_score}}

    @staticmethod
    def _none(pattern: str = "NONE") -> dict[str, Any]:
        return {"pattern": pattern, "strength": 0, "signal": "NONE", "confirmations": [],
                "context_validated": False,
                "component_scores": {"geometry": 0, "trend": 0, "location": 0,
                                     "volume": 0, "momentum": 0}}

    @staticmethod
    def _neutral(pattern: str, base: int, context: dict[str, Any],
                 location_sensitive: bool = False) -> dict[str, Any]:
        volume = 5 if context["rvol"] >= 1.5 else 0
        location = ("RESISTANCE" if context["near_resistance"] else
                    "SUPPORT" if context["near_support"] else "MID_RANGE")
        context_validated = not location_sensitive or location != "MID_RANGE"
        return {"pattern": pattern, "strength": min(100, base + volume), "signal": "NEUTRAL",
                "base_strength": base,
                "confirmations": ([f"{location.lower()} location"] if context_validated else [])
                                 + ([f"relative volume {context['rvol']:.2f}x"] if volume else []),
                "context_validated": context_validated, "location_context": location,
                "pattern_high": context["pattern_high"], "pattern_low": context["pattern_low"],
                "component_scores": {"geometry": base, "trend": 0,
                                     "location": 15 if context_validated and location_sensitive else 0,
                                     "volume": volume, "momentum": 0}}

    @classmethod
    def detect(cls, df: pd.DataFrame) -> dict[str, Any]:
        if len(df) < 2:
            return cls._none("UNKNOWN")
        candles = [cls._candle(row) for _, row in df.tail(5).iterrows()]
        curr, prev = candles[-1], candles[-2]

        # Five-candle continuation patterns: strong trend candle, three small
        # counter-trend candles contained in its range, then continuation.
        if len(candles) >= 5:
            a, b, c, d, e = candles[-5:]
            middle_inside = all(item["high"] <= a["high"] and item["low"] >= a["low"]
                                for item in (b, c, d))
            if a["bull"] and e["bull"] and middle_inside and all(item["bear"] for item in (b, c, d)) and e["close"] > a["close"]:
                context = cls._context(df, 5)
                if context["uptrend"]:
                    return cls._result("RISING THREE METHODS", "BUY", 40, context, True, True, True)
            if a["bear"] and e["bear"] and middle_inside and all(item["bull"] for item in (b, c, d)) and e["close"] < a["close"]:
                context = cls._context(df, 5)
                if context["downtrend"]:
                    return cls._result("FALLING THREE METHODS", "SELL", 40, context, True, True, True)

        if len(candles) >= 3:
            first, middle, third = candles[-3:]
            context = cls._context(df, 3)
            if (first["bear"] and first["body"] > first["span"] * .45
                    and middle["body"] < first["body"] * .45 and third["bull"]
                    and third["close"] > (first["open"] + first["close"]) / 2
                    and context["downtrend"] and context["near_support"]):
                return cls._result("MORNING STAR", "BUY", 45, context, True, True)
            if (first["bull"] and first["body"] > first["span"] * .45
                    and middle["body"] < first["body"] * .45 and third["bear"]
                    and third["close"] < (first["open"] + first["close"]) / 2
                    and context["uptrend"] and context["near_resistance"]):
                return cls._result("EVENING STAR", "SELL", 45, context, True, True)
            if all(item["bull"] and item["body"] / item["span"] >= .55 for item in (first, middle, third)) and first["close"] < middle["close"] < third["close"]:
                context = cls._context(df, 3)
                if context["downtrend"] and context["near_support"]:
                    return cls._result("THREE WHITE SOLDIERS", "BUY", 45, context, True, True)
            if all(item["bear"] and item["body"] / item["span"] >= .55 for item in (first, middle, third)) and first["close"] > middle["close"] > third["close"]:
                context = cls._context(df, 3)
                if context["uptrend"] and context["near_resistance"]:
                    return cls._result("THREE BLACK CROWS", "SELL", 45, context, True, True)

        context = cls._context(df, 2)
        tolerance = max(context["atr"] * .10, curr["close"] * .001)
        tweezer_top = abs(curr["high"] - prev["high"]) <= tolerance
        tweezer_bottom = abs(curr["low"] - prev["low"]) <= tolerance
        bullish_engulfing = (prev["bear"] and curr["bull"] and curr["open"] <= prev["close"]
                             and curr["close"] >= prev["open"])
        bearish_engulfing = (prev["bull"] and curr["bear"] and curr["open"] >= prev["close"]
                             and curr["close"] <= prev["open"])
        if bullish_engulfing and context["downtrend"] and context["near_support"]:
            return cls._result("BULLISH ENGULFING", "BUY", 45, context, True, True)
        if bearish_engulfing and context["uptrend"] and context["near_resistance"]:
            return cls._result("BEARISH ENGULFING", "SELL", 45, context, True, True)
        if (tweezer_top and prev["bull"] and curr["bear"]
                and context["uptrend"] and context["near_resistance"]):
            return cls._result("TWEEZER TOP", "SELL", 42, context, True, True)
        if (tweezer_bottom and prev["bear"] and curr["bull"]
                and context["downtrend"] and context["near_support"]):
            return cls._result("TWEEZER BOTTOM", "BUY", 42, context, True, True)
        if (prev["bear"] and curr["bull"] and curr["open"] < prev["close"]
                and curr["close"] > (prev["open"] + prev["close"]) / 2
                and context["downtrend"] and context["near_support"]):
            return cls._result("PIERCING LINE", "BUY", 42, context, True, True)
        if (prev["bull"] and curr["bear"] and curr["open"] > prev["close"]
                and curr["close"] < (prev["open"] + prev["close"]) / 2
                and context["uptrend"] and context["near_resistance"]):
            return cls._result("DARK CLOUD COVER", "SELL", 42, context, True, True)
        if (prev["bear"] and curr["bull"] and curr["open"] >= prev["close"]
                and curr["close"] <= prev["open"] and context["downtrend"] and context["near_support"]):
            return cls._result("BULLISH HARAMI", "BUY", 35, context, True, True)
        if (prev["bull"] and curr["bear"] and curr["open"] <= prev["close"]
                and curr["close"] >= prev["open"] and context["uptrend"] and context["near_resistance"]):
            return cls._result("BEARISH HARAMI", "SELL", 35, context, True, True)

        if len(df) >= 3:
            from src.candlestick.triple_patterns import TripleCandlePatternDetector
            advanced = TripleCandlePatternDetector().detect(df)
            if advanced:
                return advanced
        single_context = cls._context(df, 1)
        if curr["body"] / curr["span"] <= .1:
            return cls._neutral("DOJI", 55, single_context, location_sensitive=True)
        if .1 < curr["body"] / curr["span"] <= .3 and curr["upper"] >= curr["body"] and curr["lower"] >= curr["body"]:
            return cls._neutral("SPINNING TOP", 58, single_context)
        lower_shadow_shape = (curr["lower"] >= curr["body"] * 2
                              and curr["upper"] <= max(curr["body"], curr["span"] * .12))
        upper_shadow_shape = (curr["upper"] >= curr["body"] * 2
                              and curr["lower"] <= max(curr["body"], curr["span"] * .12))
        if lower_shadow_shape and single_context["downtrend"] and single_context["near_support"]:
            return cls._result("HAMMER", "BUY", 43, single_context, True, True)
        if upper_shadow_shape and single_context["uptrend"] and single_context["near_resistance"]:
            return cls._result("SHOOTING STAR", "SELL", 43, single_context, True, True)
        return cls._none()
