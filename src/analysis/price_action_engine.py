"""End-to-end coordinator for modular price-action analysis."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.analysis.candle_utils import atr_series, normalise_ohlcv, prior_trend
from src.analysis.reversal_detector import ReversalDetector
from src.breakout.breakout_detector import BreakoutDetector
from src.breakout.retest_detector import RetestDetector
from src.candlestick.triple_patterns import TripleCandlePatternDetector
from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig
from src.market_structure.structure_detector import MarketStructureDetector
from src.market_structure.support_resistance import SupportResistanceEngine
from src.scoring.setup_score_engine import SetupScoreEngine
from src.strategy.setup_entry_engine import SetupEntryEngine
from src.multi_timeframe.alignment import MultiTimeframeAlignment


class PriceActionEngine:
    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config = config

    def analyze(self, data: pd.DataFrame,
                higher_timeframe_data: pd.DataFrame | None = None) -> dict[str, Any]:
        df = normalise_ohlcv(data)
        if len(df) > self.config.analysis_lookback_bars:
            df = df.iloc[-self.config.analysis_lookback_bars:]
        if len(df) < 3:
            return {"status": "INSUFFICIENT_DATA", "patterns": [], "zones": [], "entries": [],
                    "rejection_reasons": ["INSUFFICIENT_DATA"]}
        zones_result = SupportResistanceEngine(self.config).analyze(df)
        structure = MarketStructureDetector(self.config).analyze(df)
        breakout_result = BreakoutDetector(self.config).detect(
            df, zones_result["zones"], self.config.signal_history_bars)
        breakout = breakout_result.get("latest_event")
        if breakout:
            breakout_age = len(df) - 1 - df.index.get_loc(breakout["breakout_candle"])
            if breakout_age > self.config.retest_max_bars:
                breakout = None
        retest = RetestDetector(self.config).detect(
            df, breakout, zones_result["zones"], structure["structure"]) if breakout else None
        pattern_frame = df.iloc[-max(
            self.config.signal_history_bars,
            self.config.trend_lookback + 4):]
        pattern_history = TripleCandlePatternDetector(self.config).detect_all(pattern_frame)
        active_patterns = [item for item in pattern_history
                           if item["candle_indexes"][-1] == df.index[-1]]
        pattern = active_patterns[-1] if active_patterns else None
        reversals = ReversalDetector(self.config).detect(
            df, breakout_result["events"], [retest] if retest else [],
            structure_result=structure)
        latest_close, atr = float(df.iloc[-1]["Close"]), float(atr_series(df, self.config.atr_period).iloc[-1])
        direction = ((retest or breakout or pattern or (reversals[-1] if reversals else {})).get("direction")
                     or ("BULLISH" if structure["structure"] == "BULLISH" else "BEARISH" if structure["structure"] == "BEARISH" else None))
        nearby = next((z for z in zones_result["zones"] if z["type"] ==
                       ("SUPPORT" if direction == "BULLISH" else "RESISTANCE")), None) if direction else None
        opposing = [z for z in zones_result["zones"] if z["type"] ==
                    ("RESISTANCE" if direction == "BULLISH" else "SUPPORT")] if direction else []
        entries = SetupEntryEngine(self.config).build(
            direction, (pattern or {}).get("pattern", "PRICE_ACTION"), latest_close, atr,
            zone=nearby, pattern=pattern, structure=structure["structure"], breakout=breakout,
            retest=retest, confirmation_price=(pattern or {}).get("confirmation_price"),
            opposing_zones=opposing) if direction else []
        trend = prior_trend(df, len(df), self.config.trend_lookback,
                            self.config.trend_min_change_ratio)
        higher_timeframe = {"enabled": False, "trend": "UNKNOWN", "agreement": 0}
        if self.config.higher_timeframe_enabled:
            higher = (normalise_ohlcv(higher_timeframe_data)
                      if higher_timeframe_data is not None else
                      MultiTimeframeAlignment.resample_completed(
                          df, self.config.higher_timeframe_frequency, df.index[-1]))
            higher_trend = prior_trend(higher, len(higher), self.config.trend_lookback,
                                       self.config.trend_min_change_ratio)
            higher_zones = (SupportResistanceEngine(self.config).analyze(higher)["zones"]
                            if len(higher) >= self.config.pivot_lookback * 2 + 1 else [])
            agreement = int((direction == "BULLISH" and higher_trend == "UPTREND")
                            or (direction == "BEARISH" and higher_trend == "DOWNTREND")) * 100
            higher_timeframe = {"enabled": True, "trend": higher_trend,
                                "agreement": agreement, "completed_candles": len(higher),
                                "zones": higher_zones}
        trend_aligned = ((direction == "BULLISH" and trend == "UPTREND")
                         or (direction == "BEARISH" and trend == "DOWNTREND"))
        score = SetupScoreEngine(self.config).score({
            "trend": 80 if trend_aligned else 45,
            "market_structure": 80 if structure["structure"] == direction else 45,
            "candlestick_pattern": (pattern or {}).get("confidence", 0),
            "support_resistance": (nearby or {}).get("strength_score", 0),
            "breakout": (breakout or {}).get("confidence", 0), "retest": (retest or {}).get("confidence", 0),
            "volume": min(100, (breakout or {}).get("volume_ratio", 1) * 50),
            "volatility": 65, "momentum": 50, "opposing_zone_distance": 60,
            "risk_reward": max((e["confidence"] for e in entries), default=0),
            "higher_timeframe": higher_timeframe["agreement"], "option_selling": 0,
        }, [] if entries else ["NO_VALID_ENTRY"])
        return {"status": "OK", "direction": direction, "patterns": active_patterns,
                "pattern_history": pattern_history,
                **zones_result, "market_structure": structure, "breakout": breakout,
                "breakout_events": breakout_result["events"], "retest": retest,
                "reversals": reversals, "entries": entries, "score": score,
                "higher_timeframe": higher_timeframe,
                "invalidation": entries[0]["stop_loss"] if entries else None,
                "rejection_reasons": score["rejection_reasons"]}
