"""Fifteen-minute recovery confirmation after an unusually large daily fall."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.market_structure.supply_demand import SupplyDemandEngine


class IntradayRecoveryEngine:
    """Classify a falling stock from falling-knife through confirmed reversal."""

    @staticmethod
    def _unavailable(daily_change: float | None, shock_threshold: float,
                     reason: str, shock_detected: bool = True) -> dict[str, Any]:
        return {
            "available": False, "required": shock_detected,
            "shock_detected": shock_detected, "daily_change_percent": daily_change,
            "shock_threshold_percent": round(shock_threshold, 2),
            "state": "DATA_UNAVAILABLE" if shock_detected else "NOT_REQUIRED",
            "confirmed": False, "score": 0, "checks": {},
            "intraday_demand": None, "intraday_supply": None,
            "recalculated_trade": None, "reason": reason,
        }

    @classmethod
    def analyze(cls, daily: pd.DataFrame, intraday: pd.DataFrame | None,
                daily_zones: dict[str, Any], *, shock_floor_percent: float = 4.0,
                shock_atr_multiple: float = 1.5, minimum_recovery_score: float = 70.0,
                minimum_risk_reward: float = 1.5,
                volume_confirmation_ratio: float = 1.1) -> dict[str, Any]:
        if daily is None or len(daily) < 2:
            return cls._unavailable(None, shock_floor_percent, "Two daily candles are required.")
        current = float(daily.iloc[-1]["Close"])
        previous = float(daily.iloc[-2]["Close"])
        daily_change = (current - previous) * 100 / max(previous, .000001)
        atr = float(daily.iloc[-1].get("ATR", 0) or 0)
        atr_percent = atr * 100 / max(current, .000001)
        threshold = max(shock_floor_percent, atr_percent * shock_atr_multiple)
        shock = daily_change <= -threshold
        if not shock:
            return cls._unavailable(
                round(daily_change, 2), threshold,
                "No exceptional bearish daily shock requires intraday reversal confirmation.",
                shock_detected=False,
            )
        required = {"Open", "High", "Low", "Close", "Volume"}
        if intraday is None or intraday.empty or not required.issubset(intraday.columns):
            return cls._unavailable(
                round(daily_change, 2), threshold,
                "Current 15-minute OHLCV candles are unavailable; recovery cannot be confirmed.",
            )
        frame = intraday.sort_index().dropna(subset=list(required)).copy()
        if isinstance(frame.index, pd.DatetimeIndex):
            latest_session = frame.index[-1].date()
            session = frame[frame.index.date == latest_session].copy()
        else:
            session = frame.tail(26).copy()
        if len(session) < 4:
            return cls._unavailable(
                round(daily_change, 2), threshold,
                "At least four current-session 15-minute candles are required.",
            )

        typical = (session["High"] + session["Low"] + session["Close"]) / 3
        session["VWAP"] = (typical * session["Volume"]).cumsum() / session["Volume"].cumsum().clip(lower=1)
        session["EMA20"] = session["Close"].ewm(span=20, adjust=False).mean()
        latest, previous_bar = session.iloc[-1], session.iloc[-2]
        close = float(latest["Close"])
        candle_range = max(float(latest["High"] - latest["Low"]), .000001)
        close_location = (close - float(latest["Low"])) / candle_range
        lower_wick = min(float(latest["Open"]), close) - float(latest["Low"])
        body = abs(close - float(latest["Open"]))
        bullish_rejection = close > float(latest["Open"]) and lower_wick >= body * .75 and close_location >= .60
        bullish_engulfing = (
            close > float(latest["Open"])
            and float(previous_bar["Close"]) < float(previous_bar["Open"])
            and float(latest["Open"]) <= float(previous_bar["Close"])
            and close >= float(previous_bar["Open"])
        )

        recent = session.tail(min(8, len(session)))
        half = max(2, len(recent) // 2)
        older, newer = recent.iloc[:half], recent.iloc[half:]
        higher_low = float(newer["Low"].min()) > float(older["Low"].min())
        prior_swing_high = float(session.iloc[:-1]["High"].tail(6).max())
        higher_high_break = close > prior_swing_high
        no_new_low = float(session.tail(3)["Low"].min()) > float(session["Low"].iloc[:-3].min())
        vwap_reclaimed = close > float(latest["VWAP"]) and float(previous_bar["Close"]) <= float(previous_bar["VWAP"])
        above_vwap = close >= float(latest["VWAP"])
        above_ema20 = close >= float(latest["EMA20"])
        red_volume = float(session.loc[session["Close"] < session["Open"], "Volume"].tail(3).mean())
        green_volume = float(session.loc[session["Close"] > session["Open"], "Volume"].tail(3).mean())
        if pd.isna(red_volume):
            red_volume = 0
        if pd.isna(green_volume):
            green_volume = 0
        volume_confirmed = green_volume >= max(1, red_volume * volume_confirmation_ratio)

        intraday_zones = SupplyDemandEngine.analyze(session, close)
        demand = intraday_zones.get("nearest_demand") or daily_zones.get("nearest_demand")
        supply = intraday_zones.get("nearest_supply") or daily_zones.get("nearest_supply")
        at_demand = bool(
            demand and float(demand["lower"]) <= float(session["Low"].min())
            <= float(demand["upper"]) + max(atr * .25, close * .003)
        )
        stop = float(demand["lower"]) - max(atr * .10, close * .002) if demand else float(session["Low"].min()) - atr * .10
        target = float(supply["lower"]) if supply and float(supply["lower"]) > close else close + max(atr, close - stop) * 1.5
        risk = close - stop
        reward = target - close
        risk_reward = reward / risk if risk > 0 else 0
        supply_clear = not supply or float(supply["lower"]) - close >= max(atr * .35, risk * minimum_risk_reward)

        checks = {
            "at_demand_zone": at_demand,
            "selling_stabilized": no_new_low,
            "bullish_rejection_or_engulfing": bullish_rejection or bullish_engulfing,
            "higher_low": higher_low,
            "previous_swing_high_broken": higher_high_break,
            "above_vwap": above_vwap,
            "vwap_reclaimed": vwap_reclaimed,
            "above_ema20": above_ema20,
            "green_volume_dominates": volume_confirmed,
            "supply_clearance": supply_clear,
            "risk_reward": risk_reward >= minimum_risk_reward,
        }
        weights = {
            "at_demand_zone": 15, "selling_stabilized": 10,
            "bullish_rejection_or_engulfing": 10, "higher_low": 15,
            "previous_swing_high_broken": 15, "above_vwap": 10,
            "vwap_reclaimed": 5, "above_ema20": 5,
            "green_volume_dominates": 5, "supply_clearance": 5, "risk_reward": 5,
        }
        score = sum(weight for name, weight in weights.items() if checks[name])
        confirmed = (
            score >= minimum_recovery_score and at_demand and higher_low
            and higher_high_break and above_vwap and volume_confirmed
            and risk_reward >= minimum_risk_reward
        )
        if confirmed:
            state = "REVERSAL_CONFIRMED"
        elif not no_new_low:
            state = "FALLING_KNIFE"
        elif higher_low and (bullish_rejection or bullish_engulfing):
            state = "RECOVERY_BUILDING"
        elif at_demand:
            state = "AT_DEMAND" if not no_new_low else "STABILIZING"
        else:
            state = "FAILED_REVERSAL" if demand and close < float(demand["lower"]) else "STABILIZING"
        return {
            "available": True, "required": True, "shock_detected": True,
            "daily_change_percent": round(daily_change, 2),
            "shock_threshold_percent": round(threshold, 2),
            "timeframe": "15_MINUTE", "session_bars": len(session),
            "state": state, "confirmed": confirmed, "score": round(score, 2),
            "minimum_score": minimum_recovery_score, "checks": checks,
            "metrics": {
                "close": round(close, 2), "vwap": round(float(latest["VWAP"]), 2),
                "ema20": round(float(latest["EMA20"]), 2),
                "prior_swing_high": round(prior_swing_high, 2),
                "close_location": round(close_location, 3),
                "green_to_red_volume_ratio": (
                    round(green_volume / red_volume, 2) if red_volume > 0 else None
                ),
            },
            "intraday_demand": demand, "intraday_supply": supply,
            "recalculated_trade": {
                "entry": round(close, 2), "stop_loss": round(stop, 2),
                "target": round(target, 2), "risk_reward": round(risk_reward, 2),
            },
            "reason": (
                "Demand held and 15-minute structure, VWAP, volume and reward/risk confirm recovery."
                if confirmed else
                f"Sharp fall is classified as {state}; wait for a higher low, swing-high break, "
                "VWAP hold and stronger green volume."
            ),
        }
