"""Generate bullish/bearish alternatives for all configured entry modes."""

from __future__ import annotations

from typing import Any

from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig
from src.risk.trade_levels import TradeLevelEngine


class SetupEntryEngine:
    MODES = ("LIMIT_ORDER", "CANDLE_CONFIRMATION_CLOSE", "RETEST_CONFIRMATION", "BREAKOUT_CLOSE")

    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config, self.levels = config, TradeLevelEngine(config)

    def build(self, direction: str, setup: str, current_price: float, atr: float, *,
              zone: dict | None = None, pattern: dict | None = None, structure: str = "RANGE",
              breakout: dict | None = None, retest: dict | None = None,
              confirmation_price: float | None = None, opposing_zones: list[dict] | None = None,
              next_candle_price: float | None = None,
              modes: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        entries = []
        for mode in modes or self.MODES:
            if mode == "LIMIT_ORDER":
                if (not zone or zone["classification"] != "STRONG"
                        or structure != direction):
                    continue
                price = zone["upper"] if direction == "BULLISH" else zone["lower"]
                confidence = 55
                signal_timestamp = zone.get("last_touch_time")
            elif mode == "CANDLE_CONFIRMATION_CLOSE":
                if confirmation_price is None:
                    continue
                price = (next_candle_price if self.config.entry_next_candle
                         and next_candle_price is not None else confirmation_price)
                confidence = 70
                signal_timestamp = (pattern or {}).get("candle_indexes", [None])[-1]
            elif mode == "RETEST_CONFIRMATION":
                if not retest or retest.get("entry_price") is None:
                    continue
                price, confidence = retest["entry_price"], min(95, retest["confidence"] + 5)
                signal_timestamp = retest.get("confirmation_timestamp")
            else:
                if not breakout or breakout.get("quality") not in ("VALID", "STRONG"):
                    continue
                if "STRONG_OPPOSING_ZONE_TOO_CLOSE" in breakout.get("reason_codes", []):
                    continue
                price, confidence = breakout["breakout_price"], breakout["confidence"]
                signal_timestamp = breakout.get("confirmation_candle") or breakout.get("breakout_candle")
            levels = self.levels.calculate(direction, price, atr, zone=zone,
                                           pattern_invalidation=(pattern or {}).get("invalidation_price"),
                                           opposing_zones=opposing_zones)
            if not levels["valid"]:
                continue
            entries.append({"entry_mode": mode, "direction": direction, "setup": setup,
                            "entry_price": round(price, 4), **levels, "atr_distance": round(levels["risk_per_share"] / max(atr, 1e-12), 3),
                            "signal_timestamp": signal_timestamp,
                            "zone_used": zone, "pattern_used": (pattern or {}).get("pattern"),
                            "market_structure_context": structure, "confidence": confidence,
                            "zone_strength": (zone or {}).get("classification"),
                            "breakout_quality": (breakout or {}).get("quality"),
                            "confidence_bucket": ("HIGH_CONFIDENCE" if confidence >= 80 else
                                                  "VALID_SETUP" if confidence >= 65 else "WATCHLIST"),
                            "invalidation_rule": f"Close beyond stop {levels['stop_loss']}",
                            "expiry_maximum_waiting_bars": self.config.entry_expiry_bars,
                            "explanation": f"{mode.replace('_', ' ').title()} for {setup} with structure/ATR risk controls."})
        return entries
