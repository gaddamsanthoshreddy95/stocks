"""ATR-, structure-, zone- and pattern-based stop/target calculations."""

from __future__ import annotations

from typing import Any

from src.config.trading_config import TECHNICAL_SETUP_CONFIG, TechnicalSetupConfig


class TradeLevelEngine:
    def __init__(self, config: TechnicalSetupConfig = TECHNICAL_SETUP_CONFIG):
        self.config = config

    def calculate(self, direction: str, entry: float, atr: float, *,
                  confirmation_candle: dict | None = None, swing: float | None = None,
                  zone: dict | None = None, pattern_invalidation: float | None = None,
                  opposing_zones: list[dict] | None = None,
                  previous_swing_target: float | None = None,
                  measured_move: float | None = None,
                  neckline_projection: float | None = None,
                  stop_method: str = "STRUCTURE", target_method: str = "ZONES") -> dict[str, Any]:
        bullish, buffer = direction == "BULLISH", atr * self.config.stop_atr_buffer
        candidates = {
            "CONFIRMATION_CANDLE": ((confirmation_candle or {}).get("low") - buffer if bullish else
                                    (confirmation_candle or {}).get("high") + buffer) if confirmation_candle else None,
            "SWING": swing - buffer if bullish and swing is not None else swing + buffer if swing is not None else None,
            "ZONE": zone["lower"] - buffer if bullish and zone else zone["upper"] + buffer if zone else None,
            "ATR": entry - atr - buffer if bullish else entry + atr + buffer,
            "STRUCTURE": swing - buffer if bullish and swing is not None else swing + buffer if swing is not None else
                         zone["lower"] - buffer if bullish and zone else zone["upper"] + buffer if zone else None,
            "PATTERN": pattern_invalidation,
        }
        stop = candidates.get(stop_method) or candidates["ATR"]
        risk = entry - stop if bullish else stop - entry
        if risk <= 0 or risk > atr * self.config.max_stop_atr:
            return {"valid": False, "rejection_reasons": ["INVALID_OR_EXCESSIVE_STOP_DISTANCE"]}
        zone_targets = sorted([z["midpoint"] for z in opposing_zones or []
                               if (bullish and z["midpoint"] > entry) or (not bullish and z["midpoint"] < entry)],
                              reverse=not bullish)
        if target_method == "ZONES" and zone_targets:
            targets = zone_targets[:3]
        elif target_method == "PREVIOUS_SWING" and previous_swing_target is not None:
            targets = [previous_swing_target]
        elif target_method == "MEASURED_MOVE" and measured_move is not None:
            targets = [entry + measured_move if bullish else entry - measured_move]
        elif target_method == "NECKLINE_PROJECTION" and neckline_projection is not None:
            targets = [neckline_projection]
        elif target_method == "ATR_MULTIPLES":
            targets = [entry + atr * r if bullish else entry - atr * r for r in (1, 2, 3)]
        else:
            targets = [entry + risk * r if bullish else entry - risk * r for r in (1.5, 2, 3)]
        rrs = [abs(target - entry) / risk for target in targets]
        valid = bool(rrs and rrs[0] >= self.config.min_risk_reward)
        return {"valid": valid, "stop_loss": round(stop, 4), "targets": [round(x, 4) for x in targets],
                "risk_per_share": round(risk, 4), "reward_per_share": [round(abs(x - entry), 4) for x in targets],
                "risk_reward": [round(x, 3) for x in rrs], "stop_method": stop_method,
                "target_method": target_method,
                "rejection_reasons": [] if valid else ["TARGET_TOO_CLOSE", "MINIMUM_RISK_REWARD_NOT_MET"]}
