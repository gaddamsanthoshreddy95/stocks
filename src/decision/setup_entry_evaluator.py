"""Two-stage setup detection and entry confirmation."""

from __future__ import annotations

import pandas as pd

from src.workflow.final_decision import EntryConfirmationResult


class SetupEntryEvaluator:
    @staticmethod
    def evaluate(df: pd.DataFrame, analysis, entry: dict, breakout: dict,
                 candlestick: dict, settings=None) -> dict:
        if settings is None:
            from src.application.settings import PlatformSettings
            settings = PlatformSettings()
        latest = df.iloc[-1]
        close = float(latest["Close"])
        ema20, ema50, ema200 = (float(latest[name]) for name in ("EMA20", "EMA50", "EMA200"))
        rsi = float(latest["RSI"])
        macd, macd_signal = float(latest["MACD"]), float(latest["MACD_SIGNAL"])
        rvol = float(latest["RVOL"])
        histogram = float(latest.get("MACD_HISTOGRAM", macd - macd_signal))
        previous = df.iloc[-2] if len(df) > 1 else latest
        previous_histogram = float(previous.get(
            "MACD_HISTOGRAM",
            float(previous["MACD"]) - float(previous["MACD_SIGNAL"]),
        ))
        support = entry.get("support")
        support_nearby = bool(support and close > 0 and
                              0 <= (close - support) / close <= settings.setup_support_near_percent / 100)

        bullish_candle = candlestick.get("signal") == "BUY"
        bearish_reversal = candlestick.get("signal") == "SELL"
        ohlc_available = all(name in df.columns for name in ("Open", "High", "Low", "Close"))
        latest_open = float(latest.get("Open", close - .000001))
        latest_high = float(latest.get("High", close))
        latest_low = float(latest.get("Low", close - .000001))
        candle_range = max(latest_high - latest_low, .000001)
        latest_green = close > latest_open
        higher_close = close > float(previous["Close"]) if ohlc_available else bullish_candle
        close_location = (close - latest_low) / candle_range
        closes_upper_range = (close_location >= settings.entry_min_close_location
                              if ohlc_available else bullish_candle)
        macd_above_signal = macd > macd_signal
        macd_turning_up = macd_above_signal and histogram >= previous_histogram
        macd_not_weakening = histogram >= previous_histogram
        volume_expansion = rvol >= settings.entry_confirmation_relative_volume
        above_ema20 = close > ema20
        atr = float(latest.get("ATR", candle_range) or candle_range)
        extension_atr = max(0.0, (close - ema20) / max(atr, .000001))
        not_overextended = (extension_atr <= settings.entry_max_extension_atr
                            if ohlc_available else True)
        consecutive_bullish = 0
        for _, candle in df.tail(settings.entry_max_consecutive_bullish_candles + 1).iloc[::-1].iterrows():
            if not ohlc_available:
                break
            candle_span = max(float(candle["High"]) - float(candle["Low"]), .000001)
            strong_bull = (float(candle["Close"]) > float(candle["Open"])
                           and abs(float(candle["Close"]) - float(candle["Open"])) / candle_span >= .55)
            if not strong_bull:
                break
            consecutive_bullish += 1
        not_exhausted = consecutive_bullish < settings.entry_max_consecutive_bullish_candles
        bullish_stack = close > ema20 > ema50 > ema200
        long_trend_intact = ema50 > ema200 and close > ema200
        good_risk_reward = (entry.get("risk_reward") or 0) >= settings.equity_min_risk_reward
        reversal_setup = rsi < settings.setup_reversal_rsi and macd_turning_up and support_nearby and good_risk_reward
        higher_highs_and_lows = False
        if len(df) >= 8 and {"High", "Low"}.issubset(df.columns):
            highs = pd.to_numeric(df["High"], errors="coerce")
            lows = pd.to_numeric(df["Low"], errors="coerce")
            prior_high = highs.iloc[-8:-4].max()
            recent_high = highs.iloc[-4:].max()
            prior_low = lows.iloc[-8:-4].min()
            recent_low = lows.iloc[-4:].min()
            higher_highs_and_lows = bool(
                pd.notna([prior_high, recent_high, prior_low, recent_low]).all()
                and recent_high > prior_high and recent_low > prior_low
            )

        if breakout.get("confirmed"):
            category = "BREAKOUT"
        elif bullish_stack and macd_above_signal:
            category = "TREND FOLLOWING"
        elif reversal_setup:
            category = "REVERSAL CANDIDATE"
        elif long_trend_intact and close <= ema20 and support_nearby:
            category = "PULLBACK"
        elif analysis.score >= settings.setup_min_technical_score:
            category = "WATCHLIST"
        else:
            category = "REJECT"

        broken_resistance = breakout.get("broken_resistance") or entry.get("broken_resistance")
        retest_holds = bool(
            broken_resistance and atr > 0
            and float(previous["Close"]) >= float(broken_resistance)
            and latest_low <= float(broken_resistance) + atr * settings.breakout_retest_tolerance_atr
            and close >= float(broken_resistance)
        )
        recent = df.tail(3)
        consolidation_holds = bool(
            broken_resistance and len(recent) == 3 and atr > 0
            and all(float(row["Close"]) >= float(broken_resistance) for _, row in recent.iterrows())
            and all((float(row["High"]) - float(row["Low"])) / atr
                    <= settings.breakout_consolidation_max_range_atr
                    for _, row in recent.iterrows())
        )
        if extension_atr <= settings.entry_normal_extension_atr:
            extension_band = "NORMAL"
        elif extension_atr <= settings.entry_max_extension_atr:
            extension_band = "CAUTION"
        elif extension_atr <= settings.entry_no_chase_extension_atr:
            extension_band = "RETEST_REQUIRED"
        else:
            extension_band = "NO_CHASE"

        common = {
            "latest_candle_green": latest_green,
            "close_above_previous_close": higher_close,
            "close_in_upper_range": closes_upper_range,
            "no_bearish_reversal": not bearish_reversal,
            "macd_momentum_not_weakening": macd_not_weakening,
            "no_exhausted_green_run": not_exhausted,
        }
        if category == "BREAKOUT":
            entry_mode = ("RETEST_ENTRY" if retest_holds else
                          "CONSOLIDATION_ENTRY" if consolidation_holds else
                          "IMMEDIATE_BREAKOUT" if not_overextended else
                          "WAIT_FOR_RETEST" if extension_band == "RETEST_REQUIRED" else "NO_CHASE")
            safe_breakout_location = not_overextended or retest_holds or consolidation_holds
            confirmation_checks = {
                **common,
                "resistance_broken": bool(breakout.get("confirmed")),
                "volume_above_1_2x": volume_expansion,
                "macd_above_signal": macd_above_signal,
                "safe_breakout_location": safe_breakout_location,
            }
        elif category == "PULLBACK":
            entry_mode = "PULLBACK_REVERSAL"
            confirmation_checks = {
                "support_or_ema20_holds": support_nearby and long_trend_intact,
                "bullish_reversal_candle": bullish_candle,
                **common,
                "macd_momentum_not_weakening": macd_not_weakening,
                "not_overextended_above_ema20": not_overextended,
            }
        elif category == "REVERSAL CANDIDATE":
            entry_mode = "REVERSAL_CONFIRMATION"
            confirmation_checks = {
                "support_holds": support_nearby,
                "bullish_reversal_candle": bullish_candle,
                "volume_above_1_2x": volume_expansion,
                "macd_turning_up": macd_turning_up,
                "no_bearish_reversal": not bearish_reversal,
            }
        elif category == "TREND FOLLOWING":
            entry_mode = "TREND_CONTINUATION"
            confirmation_checks = {
                "price_above_ema20": above_ema20,
                **common,
                "volume_above_1_2x": volume_expansion,
                "macd_above_signal": macd_above_signal,
                "not_overextended_above_ema20": not_overextended,
            }
        else:
            entry_mode = "WATCH_ONLY" if category == "WATCHLIST" else "REJECT"
            confirmation_checks = {"actionable_setup": False}

        eligible = category != "REJECT" and all(confirmation_checks.values())
        confirmation = EntryConfirmationResult.from_checks(confirmation_checks, required=True)
        entry_score = confirmation.score
        if extension_band == "CAUTION":
            entry_score = min(entry_score, 84.99)
        elif extension_band in {"RETEST_REQUIRED", "NO_CHASE"} and not (retest_holds or consolidation_holds):
            entry_score = min(entry_score, 64.99)
        entry_grade = ("A" if entry_score >= 85 else "B" if entry_score >= 75
                       else "C" if entry_score >= 65 else "D")
        setup_evidence = {
            "oversold_rsi": rsi < settings.setup_reversal_rsi,
            "macd_turning_up": macd_turning_up,
            "latest_candle_green": latest_green,
            "close_location": round(close_location, 4),
            "ema20_extension_atr": round(extension_atr, 4),
            "consecutive_strong_bullish_candles": consecutive_bullish,
            "bearish_reversal_pattern": bearish_reversal,
            "support_nearby": support_nearby,
            "risk_reward_at_least_1_5": good_risk_reward,
            "long_trend_intact": long_trend_intact,
            "extension_band": extension_band,
            "breakout_retest_holds": retest_holds,
            "breakout_consolidation_holds": consolidation_holds,
        }
        return {
            "stage_1": {"detected": category != "REJECT", "category": category,
                        "evidence": setup_evidence},
            "stage_2": {"eligible": eligible, "status": "TRADE_ELIGIBLE" if eligible else "WAIT",
                        "checks": confirmation_checks,
                        "missing": [name for name, passed in confirmation_checks.items() if not passed]},
            "entry_confirmation": confirmation.to_dict(),
            "entry_quality": {"score": round(entry_score, 2), "grade": entry_grade,
                              "entry_mode": entry_mode, "extension_band": extension_band,
                              "position_size_guidance": ("NORMAL" if extension_band == "NORMAL"
                                                         else "REDUCED" if extension_band == "CAUTION"
                                                         else "ZERO_UNTIL_RETEST")},
            "momentum_label": ("EARLY REVERSAL" if rsi < settings.setup_reversal_rsi and macd_turning_up
                               else "STRONG BULLISH" if bullish_stack and volume_expansion and higher_highs_and_lows
                               else "BULLISH" if above_ema20 and macd_above_signal else "BEARISH"),
        }
