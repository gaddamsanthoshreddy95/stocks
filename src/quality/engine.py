"""Pure, explainable quality assessment for shortlisted candidates."""

from __future__ import annotations

from dataclasses import asdict
from math import isfinite
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

from src.quality.config import QualityConfig
from src.quality.models import (
    CandidateQualityAssessment, FundamentalDataProvider, FundamentalSnapshot, QualityScore,
)


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def _weighted(
    values: dict[str, float | None], weights: dict[str, float],
    *, missing_status: str = "PARTIAL",
) -> QualityScore:
    available = {name: value for name, value in values.items() if value is not None}
    available_weight = sum(weights.get(name, 0) for name in available)
    if not available or available_weight <= 0:
        return QualityScore(None, "UNKNOWN", 0, values, ["DATA_UNAVAILABLE"])
    score = sum(float(value) * weights[name] for name, value in available.items()) / available_weight
    confidence = available_weight * 100 / max(sum(weights.values()), 1e-12)
    return QualityScore(
        _clamp(score), "AVAILABLE" if len(available) == len(values) else missing_status,
        _clamp(confidence), values,
        warnings=[] if len(available) == len(values) else ["OPTIONAL_DATA_MISSING"],
    )


def _returns(data: pd.DataFrame | None, lookbacks: tuple[int, ...]) -> dict[int, float] | None:
    if data is None or data.empty or "Close" not in data:
        return None
    close = pd.to_numeric(data["Close"], errors="coerce").dropna()
    if len(close) <= max(lookbacks):
        return None
    return {
        days: float((close.iloc[-1] / close.iloc[-days - 1] - 1) * 100)
        for days in lookbacks
    }


class CandidateQualityEngine:
    """Build all independent scores without rerunning upstream analysis engines."""

    def __init__(
        self, config: QualityConfig | None = None,
        fundamental_provider: FundamentalDataProvider | None = None,
    ):
        self.config = config or QualityConfig.from_env()
        self.fundamental_provider = fundamental_provider
        self._fundamental_cache: dict[str, FundamentalSnapshot | None] = {}

    def relative_strength(
        self, stock: pd.DataFrame, benchmark: pd.DataFrame | None,
        sector: pd.DataFrame | None = None,
    ) -> tuple[QualityScore, dict[str, Any]]:
        lookbacks = self.config.rs_lookbacks
        stock_returns = _returns(stock, lookbacks)
        benchmark_returns = _returns(benchmark, lookbacks)
        sector_returns = _returns(sector, lookbacks)
        warnings, reasons = [], []
        if stock_returns is None or benchmark_returns is None:
            warnings.append("BENCHMARK_DATA_MISSING")
            return QualityScore(None, "UNKNOWN", 0, {}, warnings=warnings), {
                "stock_returns": stock_returns, "benchmark_returns": benchmark_returns,
                "sector_returns": sector_returns, "relative_returns": {},
                "trend": "UNKNOWN", "reason_codes": warnings,
            }

        def band(value: float) -> float:
            return next(
                (score for floor, score in self.config.rs_score_bands if value >= floor), 5)

        relative: dict[str, float] = {}
        values: dict[str, float | None] = {}
        for days in lookbacks:
            difference = stock_returns[days] - benchmark_returns[days]
            relative[f"nifty_{days}d"] = round(difference, 2)
            values[f"nifty_{days}d"] = band(difference)
            if sector_returns is not None:
                sector_difference = stock_returns[days] - sector_returns[days]
                relative[f"sector_{days}d"] = round(sector_difference, 2)
                values[f"sector_{days}d"] = band(sector_difference)
            else:
                values[f"sector_{days}d"] = None
        if sector_returns is None:
            warnings.append("SECTOR_BENCHMARK_MISSING")
        nifty_20 = relative.get("nifty_20d", 0)
        sector_20 = relative.get("sector_20d")
        reasons.append("OUTPERFORMING_NIFTY" if nifty_20 > 0 else "UNDERPERFORMING_NIFTY")
        if sector_20 is not None:
            reasons.append(
                "OUTPERFORMING_SECTOR" if sector_20 > 0 else "UNDERPERFORMING_SECTOR")
        short, medium, long = (
            relative.get("nifty_5d", 0), relative.get("nifty_20d", 0),
            relative.get("nifty_60d", 0),
        )
        trend = "IMPROVING" if short > medium > long else (
            "WEAKENING" if short < medium < long else "STABLE")
        reasons.append(f"RELATIVE_STRENGTH_{trend}")
        score = _weighted(values, self.config.rs_weights)
        score = QualityScore(
            score.score, score.status, score.confidence, score.factors,
            reasons, warnings,
        )
        return score, {
            "stock_returns": stock_returns, "benchmark_returns": benchmark_returns,
            "sector_returns": sector_returns, "relative_returns": relative,
            "trend": trend, "score": score.score, "reason_codes": reasons + warnings,
        }

    @staticmethod
    def data_quality(data: pd.DataFrame) -> QualityScore:
        required = {"Open", "High", "Low", "Close", "Volume"}
        missing = required - set(data.columns)
        reasons = []
        score = 100.0
        if missing:
            score -= 60
            reasons.append("MISSING_REQUIRED_COLUMNS")
        if len(data) < 200:
            score -= 20
            reasons.append("SHORT_HISTORY")
        if data.index.has_duplicates:
            score -= 20
            reasons.append("DUPLICATE_TIMESTAMPS")
        if not data.index.is_monotonic_increasing:
            score -= 20
            reasons.append("NON_MONOTONIC_TIMESTAMPS")
        numeric = data[list(required & set(data.columns))].apply(
            pd.to_numeric, errors="coerce")
        if numeric.isna().any().any():
            score -= 20
            reasons.append("INVALID_NUMERIC_DATA")
        return QualityScore(_clamp(score), "PASS" if score >= 60 else "FAIL",
                            _clamp(score), reason_codes=reasons)

    def trend_quality(self, data: pd.DataFrame) -> QualityScore:
        latest = data.iloc[-1]
        close = float(latest["Close"])
        emas = [float(latest[name]) for name in ("EMA20", "EMA50", "EMA200")]
        full = close > emas[0] > emas[1] > emas[2]
        partial = close > emas[1] > emas[2]
        bearish = close < emas[0] < emas[1] < emas[2]
        alignment = 100 if full else 75 if partial else 10 if bearish else 45
        price_position = sum(close > ema for ema in emas) * 100 / 3
        slope_scores, reasons = [], []
        for name, lookback in zip(("EMA20", "EMA50", "EMA200"),
                                  self.config.ema_slope_lookbacks):
            start = float(data[name].iloc[-lookback - 1])
            slope = (float(data[name].iloc[-1]) / start - 1) * 100 if start else 0
            slope_scores.append(_clamp(50 + slope * 12))
            if slope > 0:
                reasons.append(f"{name}_RISING")
        persistence = (
            (data["Close"].tail(20) > data["EMA20"].tail(20)).mean() * 35
            + (data["Close"].tail(50) > data["EMA50"].tail(50)).mean() * 35
            + (data["Close"].tail(100) > data["EMA200"].tail(100)).mean() * 30
        )
        crossings = (
            ((data["Close"] > data["EMA20"]).astype(int).diff().abs().tail(30) > 0).sum()
            + ((data["Close"] > data["EMA50"]).astype(int).diff().abs().tail(60) > 0).sum()
        )
        stability = _clamp(100 - crossings * 8)
        extension = max(
            0, (close - emas[0]) / max(float(latest.get("ATR", 0)), 1e-9))
        extension_penalty = max(0, extension - 1.5) * 12
        score = _clamp(
            price_position * .20 + alignment * .20
            + np.mean(slope_scores) * .25 + persistence * .20 + stability * .15
            - extension_penalty
        )
        if full:
            reasons.append("FULL_BULLISH_ALIGNMENT")
        if crossings > 6:
            reasons.extend(["CHOPPY_AROUND_EMA20", "FREQUENT_TREND_FLIPS"])
        if persistence >= 75:
            reasons.append("TREND_PERSISTENT")
        if extension_penalty:
            reasons.append("PRICE_OVEREXTENDED")
        return QualityScore(score, "AVAILABLE", 100, {
            "price_position": price_position, "ema_alignment": alignment,
            "ema_slope": float(np.mean(slope_scores)), "trend_persistence": persistence,
            "trend_stability": stability, "extension_penalty": extension_penalty,
        }, reasons)

    @staticmethod
    def sector_quality(
        sector_data: pd.DataFrame | None, benchmark_data: pd.DataFrame | None,
        breadth: dict[str, float] | None = None,
    ) -> QualityScore:
        if sector_data is None or sector_data.empty:
            return QualityScore(
                40, "UNKNOWN", 35, reason_codes=["SECTOR_BENCHMARK_MISSING"])
        latest = sector_data.iloc[-1]
        required = {"Close", "EMA20", "EMA50", "EMA200", "RSI"}
        if not required.issubset(sector_data.columns):
            return QualityScore(
                40, "UNKNOWN", 35, reason_codes=["SECTOR_INDICATORS_MISSING"])
        close = float(latest["Close"])
        trend = (
            sum(close > float(latest[name]) for name in ("EMA20", "EMA50", "EMA200"))
            * 100 / 3)
        momentum = _clamp(float(latest["RSI"]) / 70 * 100)
        sector_returns = _returns(sector_data, (5, 20, 60))
        benchmark_returns = _returns(benchmark_data, (5, 20, 60))
        relative = None
        if sector_returns and benchmark_returns:
            relative = _clamp(
                50 + np.mean([
                    sector_returns[days] - benchmark_returns[days]
                    for days in (5, 20, 60)
                ]) * 8
            )
        breadth_score = (
            float(np.mean(list(breadth.values()))) if breadth else None)
        score = _weighted(
            {"trend": trend, "momentum": momentum, "relative": relative,
             "breadth": breadth_score},
            {"trend": .30, "momentum": .20, "relative": .30, "breadth": .20},
        )
        classification = (
            "LEADING" if (score.score or 0) >= 85 else
            "STRONG" if (score.score or 0) >= 70 else
            "IMPROVING" if (score.score or 0) >= 55 else
            "NEUTRAL" if (score.score or 0) >= 40 else
            "WEAKENING" if (score.score or 0) >= 25 else "LAGGING"
        )
        return QualityScore(
            score.score, classification, score.confidence, score.factors,
            [f"SECTOR_{classification}"], score.warnings)

    @staticmethod
    def support_resistance_quality(
        zones: list[dict[str, Any]], current_price: float,
    ) -> QualityScore:
        if not zones:
            return QualityScore(
                None, "UNKNOWN", 0, reason_codes=["ADVANCED_LEVELS_UNAVAILABLE"])
        scored = []
        for zone in zones:
            touches = int(zone.get("touch_count") or zone.get("touches") or 1)
            rejections = int(zone.get("rejection_count") or 0)
            failed = bool(zone.get("failed") or zone.get("invalidated"))
            freshness = float(zone.get("freshness_score") or (
                90 if touches <= 2 else 65 if touches <= 3 else 40 if touches <= 5 else 15))
            touch_quality = 40 if touches == 1 else 90 if touches <= 3 else (
                55 if touches <= 5 else 20)
            score = _clamp(
                min(100, rejections * 20) * .20 + freshness * .25
                + float(zone.get("recency_score") or 60) * .10
                + float(zone.get("volume_confluence_score") or 50) * .15
                + float(zone.get("technical_confluence_score") or 50) * .20
                + float(zone.get("higher_timeframe_score") or 50) * .10
                + touch_quality * .10 - (50 if failed else 0)
            )
            scored.append((score, zone, failed, touches))
        score, zone, failed, touches = max(scored, key=lambda item: item[0])
        reasons = []
        if failed:
            reasons.append("LEVEL_ALREADY_FAILED")
        if touches > 5:
            reasons.append("LEVEL_HEAVILY_TESTED")
        if float(zone.get("upper") or zone.get("price") or current_price) > current_price:
            reasons.append("ACTIVE_RESISTANCE_SELECTED")
        else:
            reasons.append("ACTIVE_SUPPORT_SELECTED")
        return QualityScore(score, "AVAILABLE", 90, {
            "selected_zone_score": score, "touch_count": touches,
        }, reasons)

    @staticmethod
    def momentum_quality(data: pd.DataFrame) -> QualityScore:
        rsi = pd.to_numeric(data["RSI"], errors="coerce")
        macd = pd.to_numeric(data["MACD"], errors="coerce")
        signal = pd.to_numeric(data["MACD_SIGNAL"], errors="coerce")
        hist = pd.to_numeric(data["MACD_HISTOGRAM"], errors="coerce")
        current = float(rsi.iloc[-1])
        slope3, slope5 = current - float(rsi.iloc[-4]), current - float(rsi.iloc[-6])
        crossed_30 = float(rsi.iloc[-2]) < 30 <= current
        reasons = []
        if current < 30 and slope3 < 0:
            level, slope, transition = 15, 10, 10
            reasons.append("RSI_OVERSOLD_FALLING")
        elif current < 30:
            level, slope, transition = 35, 55, 25
            reasons.append("RSI_OVERSOLD_RECOVERING")
        elif crossed_30:
            level, slope, transition = 55, 80, 100
            reasons.append("RSI_RECOVERY_CONFIRMED")
        elif 55 <= current <= 70:
            level, slope, transition = 90, _clamp(50 + slope5 * 8), 70
            reasons.append("RSI_HEALTHY_BULLISH")
        elif current > 70:
            level, slope, transition = 55, _clamp(50 + slope5 * 5), 45
            reasons.append("RSI_OVERBOUGHT")
        else:
            level, slope, transition = 55, _clamp(50 + slope5 * 8), 50
        above_signal = float(macd.iloc[-1]) > float(signal.iloc[-1])
        above_zero = float(macd.iloc[-1]) > 0
        improving = bool(hist.iloc[-1] > hist.iloc[-2] > hist.iloc[-3])
        weakening = bool(hist.iloc[-1] < hist.iloc[-2] < hist.iloc[-3])
        if above_signal:
            reasons.append("MACD_BULLISH_CROSS")
        if not above_zero:
            reasons.append("MACD_BELOW_ZERO")
        if improving:
            reasons.append("MACD_HISTOGRAM_IMPROVING")
        if weakening:
            reasons.append("MACD_HISTOGRAM_WEAKENING")
        score = _clamp(
            level * .20 + slope * .20 + transition * .10
            + (100 if above_zero else 35) * .15
            + (100 if above_signal else 20) * .15
            + (90 if improving else 20 if weakening else 50) * .20
        )
        return QualityScore(score, "AVAILABLE", 100, {
            "rsi_level": level, "rsi_slope": slope, "rsi_transition": transition,
            "macd_position": 100 if above_zero else 35,
            "macd_cross": 100 if above_signal else 20,
            "macd_histogram": 90 if improving else 20 if weakening else 50,
        }, reasons)

    def volume_quality(self, data: pd.DataFrame, breakout: bool = False) -> QualityScore:
        latest, previous = data.iloc[-1], data.iloc[-2]
        volume = pd.to_numeric(data["Volume"], errors="coerce")
        returns = pd.to_numeric(data["Close"], errors="coerce").pct_change()
        rvol = float(latest.get("RVOL", volume.iloc[-1] / volume.tail(20).mean()))
        candle_range = max(float(latest["High"] - latest["Low"]), 1e-9)
        body = abs(float(latest["Close"] - latest["Open"])) / candle_range
        location = float(latest["Close"] - latest["Low"]) / candle_range
        bullish = (
            rvol >= self.config.bullish_rvol_threshold
            and latest["Close"] > latest["Open"] and latest["Close"] > previous["Close"]
            and location >= self.config.bullish_close_location
            and body >= self.config.minimum_candle_body_percent
        )
        bearish = (
            rvol >= self.config.bearish_rvol_threshold
            and latest["Close"] < latest["Open"] and latest["Close"] < previous["Close"]
            and location <= self.config.bearish_close_location
        )
        indecision = rvol >= 1.2 and body < .30 and .35 <= location <= .65
        up_average = float(volume[returns > 0].tail(20).mean())
        down_average = float(volume[returns < 0].tail(20).mean())
        up_average = up_average if isfinite(up_average) else float(volume.tail(20).mean())
        down_average = (
            down_average if isfinite(down_average) and down_average > 0
            else float(volume.tail(20).mean())
        )
        ratio = up_average / down_average if down_average > 0 else 1.0
        directional = 95 if bullish else 10 if bearish else 30 if indecision else 55
        reasons = [
            "BULLISH_VOLUME_CONFIRMATION" if bullish else
            "BEARISH_DISTRIBUTION" if bearish else
            "HIGH_VOLUME_INDECISION" if indecision else "VOLUME_NEUTRAL"
        ]
        if breakout:
            if rvol >= self.config.breakout_rvol_threshold and bullish:
                reasons.append("BREAKOUT_VOLUME_CONFIRMED")
            else:
                directional = min(directional, 35)
                reasons.append("BREAKOUT_WITHOUT_VOLUME")
        rvol_score = 100 if rvol >= 2 else 85 if rvol >= 1.5 else 65 if rvol >= 1 else 40
        ratio_score = _clamp(50 + (ratio - 1) * 35)
        volume_trend = _clamp(50 + (volume.tail(5).mean() / max(volume.tail(20).mean(), 1) - 1) * 100)
        score = _clamp(
            rvol_score * .25 + directional * .35 + ratio_score * .20 + volume_trend * .20)
        return QualityScore(score, "AVAILABLE", 100, {
            "rvol": rvol_score, "directional_context": directional,
            "up_down_volume": ratio_score, "volume_trend": volume_trend,
            "up_down_volume_ratio": round(ratio, 3),
        }, reasons)

    @staticmethod
    def risk_reward_quality(plan: dict[str, Any], atr: float) -> QualityScore:
        entry = float(plan.get("entry") or 0)
        stop = float(plan.get("stop_loss") or 0)
        target = float(plan.get("target1") or 0)
        reasons = []
        if not (entry > 0 and 0 < stop < entry):
            return QualityScore(0, "FAIL", 100, reason_codes=["INVALID_STOP"])
        if target <= entry:
            return QualityScore(0, "FAIL", 100, reason_codes=["INVALID_TARGET"])
        risk = entry - stop
        target_basis = str(plan.get("target_basis") or "NEAREST_RESISTANCE")
        projected_basis = target_basis in {
            "BREAKOUT_WEIGHTED_TARGETS", "SECOND_TARGET_BREAKOUT",
        }
        reward = (
            float(plan.get("expected_reward") or 0)
            if projected_basis else target - entry
        )
        ratio = reward / risk if risk > 1e-9 else 0
        score = 100 if ratio >= 4 else 90 if ratio >= 3 else 75 if ratio >= 2 else (
            55 if ratio >= 1.5 else 25 if ratio >= 1 else 0)
        stop_atr = risk / max(atr, 1e-9)
        if stop_atr < .25:
            score = min(score, 35)
            reasons.append("STOP_INSIDE_NORMAL_NOISE")
        if ratio < 1.5:
            reasons.append("LOW_RISK_REWARD")
        return QualityScore(score, "PASS" if ratio >= 1.5 else "FAIL", 100, {
            "risk_reward": ratio, "stop_distance_atr": stop_atr,
        }, reasons)

    @staticmethod
    def path_quality(
        entry: float, target: float, resistance_levels: list[float] | None,
        supply_zones: list[dict[str, Any]] | None = None,
    ) -> QualityScore:
        levels = sorted(
            float(level) for level in (resistance_levels or [])
            if entry < float(level) < target
        )
        major = 0
        for zone in supply_zones or []:
            lower = float(zone.get("lower") or zone.get("lower_bound") or 0)
            if entry < lower < target:
                major += 1
        score = _clamp(100 - len(levels) * 10 - major * 30
                       - (20 if len(levels) + major >= 3 else 0))
        classification = (
            "CLEAR" if not levels and not major else "MINOR" if len(levels) == 1 and not major
            else "MODERATE" if score >= 60 else "HEAVY" if score >= 30 else "BLOCKED"
        )
        reasons = ["CLEAR_PATH_TO_TARGET"] if classification == "CLEAR" else []
        if levels:
            reasons.append("MULTIPLE_RESISTANCE_OBSTACLES")
        if major:
            reasons.append("MAJOR_SUPPLY_BEFORE_TARGET")
        return QualityScore(score, classification, 100, {
            "obstacle_count": float(len(levels) + major),
            "major_obstacle_count": float(major),
            "nearest_obstacle": levels[0] if levels else None,
        }, reasons)

    @staticmethod
    def price_behaviour(data: pd.DataFrame) -> QualityScore:
        close = pd.to_numeric(data["Close"], errors="coerce")
        opens = pd.to_numeric(data["Open"], errors="coerce")
        volume = pd.to_numeric(data["Volume"], errors="coerce")
        returns = close.pct_change()
        gaps = opens / close.shift(1) - 1
        large_returns = int((returns.abs() > .05).tail(120).sum())
        large_gaps = int((gaps.abs() > .03).tail(120).sum())
        spikes = int((volume > volume.rolling(20).mean() * 3).tail(120).sum())
        volatility = returns.rolling(20).std().dropna()
        consistency = _clamp(100 - (volatility.std() / max(volatility.mean(), 1e-9)) * 50)
        score = _clamp(
            max(0, 100 - large_gaps * 12) * .25
            + max(0, 100 - large_returns * 8) * .25
            + 70 * .20 + consistency * .15 + max(0, 100 - spikes * 8) * .15
        )
        reasons = ["STABLE_PRICE_BEHAVIOUR"] if score >= 70 else ["CHOPPY_PRICE_BEHAVIOUR"]
        if large_gaps > 2:
            reasons.extend(["FREQUENT_LARGE_GAPS", "HIGH_OVERNIGHT_RISK"])
        if large_returns > 2:
            reasons.append("FREQUENT_EXTREME_RETURNS")
        if spikes > 3:
            reasons.append("ABNORMAL_VOLUME_SPIKES")
        return QualityScore(score, "AVAILABLE", 100, {
            "large_return_days": large_returns, "large_gap_days": large_gaps,
            "abnormal_volume_spikes": spikes, "volatility_consistency": consistency,
        }, reasons)

    def fundamental_quality(self, symbol: str) -> QualityScore:
        if self.fundamental_provider is None:
            return QualityScore(
                None, "UNKNOWN", 0, reason_codes=["FUNDAMENTAL_DATA_MISSING"],
                warnings=["No fundamental provider is configured."],
            )
        if symbol not in self._fundamental_cache:
            try:
                self._fundamental_cache[symbol] = (
                    self.fundamental_provider.get_fundamentals(symbol))
            except Exception as exc:
                self._fundamental_cache[symbol] = None
                return QualityScore(
                    None, "UNKNOWN", 0, reason_codes=["FUNDAMENTAL_PROVIDER_FAILED"],
                    warnings=[f"{exc.__class__.__name__}: {exc}"],
                )
        snapshot = self._fundamental_cache[symbol]
        if snapshot is None:
            return QualityScore(None, "UNKNOWN", 0,
                                reason_codes=["FUNDAMENTAL_DATA_MISSING"])
        profitability = np.mean([
            _clamp(50 + (snapshot.roe or 0) * 2),
            _clamp(50 + (snapshot.roce or 0) * 2),
        ])
        growth = np.mean([
            _clamp(50 + (snapshot.revenue_growth or 0) * 2),
            _clamp(50 + (snapshot.profit_growth or 0) * 2),
        ])
        balance = _clamp(100 - (snapshot.debt_to_equity or 0) * 35)
        cash = 80 if (snapshot.operating_cash_flow or 0) > 0 else 20
        governance = _clamp(100 - (snapshot.promoter_pledge or 0) * 3)
        score = _clamp(np.mean([profitability, growth, balance, cash, governance]))
        status = "PASS" if score >= 60 else "CAUTION" if score >= 40 else "FAIL"
        reasons = ["SERIOUS_BALANCE_SHEET_RISK"] if balance < 30 else []
        return QualityScore(score, status, 100, {
            "profitability": profitability, "growth": growth, "balance_sheet": balance,
            "cash_flow": cash, "governance": governance,
        }, reasons)

    def event_safety(self, event: dict[str, Any] | None) -> QualityScore:
        if not event or event.get("event_data_availability_state") in {
            "UNAVAILABLE", "FAILED", "NOT_REQUESTED",
        }:
            return QualityScore(
                55, "UNKNOWN", 50, reason_codes=["EVENT_STATUS_UNKNOWN"])
        days = event.get("days_to_event")
        level = str(event.get("event_risk_level", "VERY_LOW")).upper()
        hard = bool(event.get("hard_block"))
        if hard or days is not None and days <= self.config.event_critical_days:
            score, reasons = 10, ["EVENT_RISK_BLOCKS_TRADE"]
        elif days is not None and days <= self.config.event_high_risk_days:
            score, reasons = 40, ["EARNINGS_WITHIN_3_DAYS"]
        elif days is not None and days <= self.config.event_caution_days:
            score, reasons = 70, ["EARNINGS_WITHIN_7_DAYS"]
        elif level in {"HIGH", "EXTREME"}:
            score, reasons = 30, ["EVENT_RISK_HIGH"]
        else:
            score, reasons = 100, ["NO_NEAR_TERM_EVENT"]
        return QualityScore(score, "FAIL" if score < 40 else "CAUTION" if score < 80 else "PASS",
                            100, reason_codes=reasons)

    @staticmethod
    def market_alignment(market: dict[str, Any], setup: str) -> QualityScore:
        regime = str(market.get("regime") or market.get("direction") or "UNAVAILABLE").upper()
        if "STRONG_BULL" in regime:
            value = 95
        elif "BULL" in regime:
            value = 85
        elif "SIDEWAYS" in regime or "NEUTRAL" in regime:
            value = 60
        elif "STRONG_BEAR" in regime:
            value = 10
        elif "BEAR" in regime:
            value = 25
        else:
            value = 50
        if setup == "REVERSAL_CANDIDATE" and "BEAR" in regime:
            value = min(55, value + 25)
        return QualityScore(value, "AVAILABLE" if regime != "UNAVAILABLE" else "UNKNOWN",
                            100 if regime != "UNAVAILABLE" else 50,
                            {"regime_alignment": value})

    @staticmethod
    def multi_timeframe(
        daily_score: float, weekly_data: pd.DataFrame | None,
        intraday_score: float | None = None,
    ) -> QualityScore:
        weekly_score = None
        reasons = []
        if weekly_data is not None and not weekly_data.empty:
            close = weekly_data["Close"]
            weekly_score = 85 if len(close) >= 10 and close.iloc[-1] > close.tail(10).mean() else 35
        values = {"weekly": weekly_score, "daily": daily_score, "intraday": intraday_score}
        weights = {"weekly": .4, "daily": .4, "intraday": .2}
        score = _weighted(values, weights)
        if intraday_score is None:
            reasons.append("INTRADAY_DATA_UNAVAILABLE")
        if weekly_score is not None and weekly_score >= 60 and daily_score >= 60:
            reasons.append("WEEKLY_DAILY_ALIGNED")
        elif weekly_score is not None and weekly_score < 50 < daily_score:
            reasons.append("WEEKLY_TREND_CONFLICT")
        return QualityScore(score.score, score.status, score.confidence,
                            values, reasons, score.warnings)

    def option_suitability(
        self, option: dict[str, Any], underlying: float,
        fundamental: QualityScore, support: float,
        event: QualityScore,
    ) -> tuple[QualityScore, list[str]]:
        failures = []
        if not option or not option.get("available"):
            return QualityScore(None, "UNKNOWN", 0,
                                reason_codes=["OPTION_DATA_MISSING"]), ["OPTION_DATA_MISSING"]
        spread = float(option.get("spread_percent") or option.get("bid_ask_spread_percent") or 0)
        oi = int(option.get("open_interest") or option.get("oi") or 0)
        volume = int(option.get("volume") or 0)
        strike = float(option.get("strike") or option.get("recommended_strike") or 0)
        distance_pct = (underlying - strike) * 100 / underlying if underlying and strike else 0
        if spread <= 0 or spread > self.config.maximum_bid_ask_spread_percent:
            failures.append("OPTION_SPREAD_TOO_WIDE")
        if oi < self.config.minimum_option_oi:
            failures.append("OPTION_OI_TOO_LOW")
        if volume < self.config.minimum_option_volume:
            failures.append("OPTION_VOLUME_TOO_LOW")
        if distance_pct < self.config.minimum_strike_distance_percent:
            failures.append("UNSAFE_STRIKE_DISTANCE")
        if event.score is not None and event.score < 40:
            failures.append("EVENT_RISK_BLOCKS_OPTION_SELL")
        option_liquidity = _clamp(
            (100 if spread and spread <= 2 else 70 if spread <= 5 else 10) * .4
            + min(100, oi / max(self.config.minimum_option_oi, 1) * 50) * .35
            + min(100, volume / max(self.config.minimum_option_volume, 1) * 50) * .25)
        strike_safety = _clamp(distance_pct / max(
            self.config.minimum_strike_distance_percent, .01) * 70)
        roc = float(option.get("return_on_capital") or option.get(
            "return_on_margin_percent") or 0)
        values = {
            "underlying_quality": underlying,
            "fundamental_quality": fundamental.score,
            "support_quality": support,
            "strike_safety": strike_safety,
            "option_liquidity": option_liquidity,
            "volatility_premium": float(option.get("iv_percentile") or 50),
            "event_safety": event.score,
            "return_on_capital": _clamp(roc * 20),
        }
        result = _weighted(values, self.config.option_sell_weights)
        return QualityScore(
            result.score, "FAIL" if failures else "PASS", result.confidence,
            values, failures, result.warnings,
        ), failures

    @staticmethod
    def selection_stability(history: list[dict[str, Any]] | None) -> QualityScore:
        if not history:
            return QualityScore(
                50, "NEW_SIGNAL", 50, reason_codes=["NEW_SIGNAL"])
        ranks = [float(item["rank"]) for item in history if item.get("rank") is not None]
        scores = [
            float(item["final_score"]) for item in history
            if item.get("final_score") is not None
        ]
        actions = [str(item.get("action")) for item in history if item.get("action")]
        rank_consistency = _clamp(100 - (np.std(ranks) if ranks else 10) * 12)
        score_consistency = _clamp(100 - (np.std(scores) if scores else 10) * 4)
        action_consistency = (
            max(actions.count(action) for action in set(actions)) * 100 / len(actions)
            if actions else 50
        )
        score = _clamp(
            rank_consistency * .35 + score_consistency * .35 + action_consistency * .30)
        return QualityScore(score, "ESTABLISHED", 100, {
            "rank_consistency": rank_consistency,
            "score_consistency": score_consistency,
            "action_consistency": action_consistency,
        })

    def assess(
        self, *, symbol: str, daily_data: pd.DataFrame, candidate: dict[str, Any],
        analysis: dict[str, Any], relative_strength: dict[str, Any],
        sector: dict[str, Any], market: dict[str, Any], event: dict[str, Any],
        option: dict[str, Any], setup: str, plan: dict[str, Any],
        weekly_data: pd.DataFrame | None = None,
        benchmark_data: pd.DataFrame | None = None,
        sector_benchmark_data: pd.DataFrame | None = None,
    ) -> CandidateQualityAssessment:
        started = perf_counter()
        timings: dict[str, float] = {}

        def timed(name: str, operation):
            component_started = perf_counter()
            value = operation()
            timings[name] = round(perf_counter() - component_started, 6)
            return value

        scores: dict[str, QualityScore] = {}
        scores["data_quality"] = self.data_quality(daily_data)
        scores["liquidity"] = QualityScore(
            _clamp(candidate.get("stock_liquidity", {}).get("score", 0)),
            "AVAILABLE", 100)
        scores["price_behaviour"] = timed(
            "price_behaviour_seconds", lambda: self.price_behaviour(daily_data))
        rs_detail = {}
        if benchmark_data is not None:
            scores["relative_strength"], rs_detail = timed(
                "relative_strength_seconds",
                lambda: self.relative_strength(
                    daily_data, benchmark_data, sector_benchmark_data))
        else:
            raw = relative_strength.get("score")
            scores["relative_strength"] = QualityScore(
                _clamp(raw) if raw is not None else None,
                "AVAILABLE" if raw is not None else "UNKNOWN",
                80 if raw is not None else 0,
                reason_codes=[] if raw is not None else ["BENCHMARK_DATA_MISSING"])
        sector_score = sector.get("score")
        scores["sector_strength"] = QualityScore(
            _clamp(sector_score) if sector_score is not None else 40,
            "AVAILABLE" if sector.get("available") else "UNKNOWN",
            100 if sector.get("available") else 40,
            reason_codes=[] if sector.get("available") else ["SECTOR_BENCHMARK_MISSING"])
        scores["trend_quality"] = timed(
            "trend_quality_seconds", lambda: self.trend_quality(daily_data))
        scores["momentum_quality"] = timed(
            "momentum_quality_seconds", lambda: self.momentum_quality(daily_data))
        scores["volume_quality"] = timed(
            "directional_volume_seconds",
            lambda: self.volume_quality(
                daily_data, bool(analysis.get("breakout", {}).get("confirmed"))))
        zones = analysis.get("price_action", {}).get("zones", [])
        zone_result = timed(
            "support_resistance_quality_seconds",
            lambda: self.support_resistance_quality(
                zones, float(candidate.get("current_price") or 0)))
        zone_score = float(zone_result.score if zone_result.score is not None else 50)
        scores["support_resistance_quality"] = zone_result
        scores["risk_reward_quality"] = self.risk_reward_quality(
            plan, float(analysis.get("analysis", {}).get("atr") or 0))
        scores["path_quality"] = self.path_quality(
            float(plan.get("entry") or 0), float(plan.get("target1") or 0),
            analysis.get("entry", {}).get("resistance_levels", []),
            (analysis.get("supply_demand", {}) or {}).get("supply_zones", []),
        )
        scores["fundamental_quality"] = timed(
            "fundamental_quality_seconds", lambda: self.fundamental_quality(symbol))
        scores["event_safety"] = timed(
            "event_safety_seconds", lambda: self.event_safety(event))
        scores["market_alignment"] = self.market_alignment(market, setup)
        scores["multi_timeframe"] = timed(
            "multi_timeframe_seconds",
            lambda: self.multi_timeframe(
                float(candidate.get("technical_score") or 0), weekly_data))
        setup_values = {
            "structure": zone_score,
            "trend": scores["trend_quality"].score,
            "momentum": scores["momentum_quality"].score,
            "volume": scores["volume_quality"].score,
            "support": scores["support_resistance_quality"].score,
            "relative_strength": scores["relative_strength"].score,
            "sector": scores["sector_strength"].score,
        }
        setup_weights = {
            "structure": .25, "trend": .15, "momentum": .15, "volume": .15,
            "support": .15, "relative_strength": .10, "sector": .05,
        }
        scores["setup_quality"] = _weighted(setup_values, setup_weights)
        readiness_values = {
            "candle": 90 if analysis.get("candlestick", {}).get("signal") == "BUY" else 50,
            "price": 90 if plan.get("entry", 0) > 0 else 0,
            "volume": scores["volume_quality"].score,
            "momentum": scores["momentum_quality"].score,
            "level": scores["support_resistance_quality"].score,
            "extension": scores["trend_quality"].factors.get("extension_penalty", 0),
            "risk_reward": scores["risk_reward_quality"].score,
            "event": scores["event_safety"].score,
        }
        readiness_values["extension"] = 100 - float(readiness_values["extension"] or 0)
        scores["entry_readiness"] = _weighted(readiness_values, {
            "candle": .15, "price": .15, "volume": .15, "momentum": .15,
            "level": .15, "extension": .10, "risk_reward": .10, "event": .05,
        })
        readiness = scores["entry_readiness"].score or 0
        readiness_status = (
            "READY_NOW" if readiness >= 85 else "NEAR_READY" if readiness >= 70
            else "WAIT_FOR_CONFIRMATION" if readiness >= 55
            else "WAIT_FOR_PULLBACK_OR_RETEST" if readiness >= 40 else "NOT_READY"
        )
        scores["entry_readiness"] = QualityScore(
            readiness, readiness_status, scores["entry_readiness"].confidence,
            scores["entry_readiness"].factors,
            scores["entry_readiness"].reason_codes,
            scores["entry_readiness"].warnings,
        )
        stock = _weighted({
            name: scores[name].score for name in self.config.stock_quality_weights
        }, self.config.stock_quality_weights)
        scores["stock_quality"] = stock
        directional = _weighted({
            name: scores[name].score for name in self.config.directional_weights
        }, self.config.directional_weights)
        scores["directional_trade_suitability"] = directional
        option_score, option_failures = timed(
            "option_suitability_seconds",
            lambda: self.option_suitability(
                option, stock.score or 0, scores["fundamental_quality"],
                scores["support_resistance_quality"].score or 0,
                scores["event_safety"]))
        scores["option_sell_suitability"] = option_score
        stability_payload = candidate.get("selection_stability_history")
        scores["selection_stability"] = self.selection_stability(stability_payload)
        final = _weighted({
            name: scores[name].score for name in self.config.final_directional_weights
        }, self.config.final_directional_weights)
        scores["final_candidate"] = final
        scores["data_quality_score"] = scores["data_quality"]
        scores["liquidity_score"] = scores["liquidity"]
        scores["price_behaviour_score"] = scores["price_behaviour"]
        scores["fundamental_quality_score"] = scores["fundamental_quality"]
        scores["relative_strength_score"] = scores["relative_strength"]
        scores["sector_strength_score"] = scores["sector_strength"]
        scores["trend_quality_score"] = scores["trend_quality"]
        scores["momentum_quality_score"] = scores["momentum_quality"]
        scores["volume_quality_score"] = scores["volume_quality"]
        scores["support_resistance_quality_score"] = scores["support_resistance_quality"]
        scores["setup_quality_score"] = scores["setup_quality"]
        scores["entry_readiness_score"] = scores["entry_readiness"]
        scores["risk_reward_quality_score"] = scores["risk_reward_quality"]
        scores["path_quality_score"] = scores["path_quality"]
        scores["market_alignment_score"] = scores["market_alignment"]
        scores["multi_timeframe_score"] = scores["multi_timeframe"]
        scores["event_safety_score"] = scores["event_safety"]
        scores["directional_trade_suitability_score"] = directional
        scores["option_sell_suitability_score"] = option_score
        scores["final_candidate_score"] = final
        gates = {
            name: scores[name].score is not None
            and float(scores[name].score) >= threshold
            for name, threshold in self.config.hard_gate_thresholds.items()
        }
        gates.update({
            "valid_stop": "INVALID_STOP" not in scores["risk_reward_quality"].reason_codes,
            "valid_target": "INVALID_TARGET" not in scores["risk_reward_quality"].reason_codes,
            "minimum_risk_reward": scores["risk_reward_quality"].status == "PASS",
            "option_sell_safety": not option_failures,
        })
        if (scores["fundamental_quality"].status == "UNKNOWN"
                and self.config.fundamental_missing_policy == "REJECT"):
            gates["fundamental_data"] = False
        directional_gate_names = {
            *self.config.hard_gate_thresholds,
            "valid_stop", "valid_target", "minimum_risk_reward",
        }
        failures = [
            name.upper() for name, passed in gates.items()
            if name in directional_gate_names and not passed
        ]
        available_confidence = [
            score.confidence for name, score in scores.items() if not name.endswith("_score")
        ]
        confidence = _clamp(float(np.mean(available_confidence))) if available_confidence else 0
        confidence_status = (
            "HIGH" if confidence >= 80 else "MEDIUM" if confidence >= 60
            else "LOW" if confidence >= 35 else "INSUFFICIENT"
        )
        reasons = list(dict.fromkeys(
            code for score in scores.values() for code in score.reason_codes))
        strengths = [
            name.replace("_", " ").title() + f" is {score.score:.1f}"
            for name, score in scores.items()
            if not name.endswith("_score") and score.score is not None and score.score >= 70
        ][:8]
        risks = [
            name.replace("_", " ").title() + (
                " is unavailable" if score.score is None else f" is {score.score:.1f}")
            for name, score in scores.items()
            if not name.endswith("_score") and (score.score is None or score.score < 50)
        ][:8]
        composite = (
            "AVOID" if failures else "BUY" if (final.score or 0) >= 75 and readiness >= 70
            else "WATCH" if (final.score or 0) >= 55 else "WAIT"
        )
        return CandidateQualityAssessment(
            scores=scores, hard_gates=gates, strengths=strengths, risks=risks,
            hard_gate_failures=failures, analysis_confidence_score=confidence,
            analysis_confidence_status=confidence_status,
            final_candidate_score=float(final.score or 0),
            directional_trade_suitability_score=float(directional.score or 0),
            option_sell_suitability_score=option_score.score,
            composite_recommendation=composite, reason_codes=reasons,
            timings={
                **timings,
                "final_ranking_inputs_seconds": round(
                    perf_counter() - started - sum(timings.values()), 6),
                "quality_assessment_seconds": round(perf_counter() - started, 6),
            },
        )
