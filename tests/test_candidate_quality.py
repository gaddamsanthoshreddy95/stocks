"""Deterministic coverage for the explainable candidate-quality layer."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from src.indicators.pipeline import IndicatorPipeline
from src.quality.config import QualityConfig
from src.quality.engine import CandidateQualityEngine
from src.quality.models import FundamentalSnapshot
from src.quality.portfolio import (
    apply_soft_sector_cap, reduce_correlated_exposure,
)


def frame(direction=1, rows=260, volume=1_000_000):
    index = pd.date_range("2024-01-01", periods=rows, freq="B")
    close = np.linspace(100, 160 if direction > 0 else 70, rows)
    open_ = close - direction * .5
    result = pd.DataFrame({
        "Open": open_, "High": np.maximum(open_, close) + 1,
        "Low": np.minimum(open_, close) - 1, "Close": close,
        "Volume": np.full(rows, volume, dtype=float),
    }, index=index)
    return IndicatorPipeline.run(result)


def benchmark(direction=1):
    return frame(direction)


class FundamentalProvider:
    def __init__(self, snapshot=None, failure=None):
        self.snapshot, self.failure, self.calls = snapshot, failure, 0

    def get_fundamentals(self, symbol):
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.snapshot


def test_relative_strength_outperformance_scores_higher():
    engine = CandidateQualityEngine()
    high, _ = engine.relative_strength(frame(1), benchmark(-1), benchmark(1))
    low, _ = engine.relative_strength(frame(-1), benchmark(1), benchmark(-1))
    assert high.score > low.score


def test_sector_outperformance_contributes():
    engine = CandidateQualityEngine()
    with_sector, _ = engine.relative_strength(frame(1), benchmark(1), benchmark(-1))
    without, _ = engine.relative_strength(frame(1), benchmark(1), None)
    assert with_sector.confidence > without.confidence


def test_missing_benchmark_is_warning():
    score, detail = CandidateQualityEngine().relative_strength(frame(), None)
    assert score.score is None
    assert "BENCHMARK_DATA_MISSING" in detail["reason_codes"]


def test_relative_strength_uses_only_trailing_rows():
    stock, market = frame(), benchmark()
    original, _ = CandidateQualityEngine().relative_strength(stock, market)
    future = stock.copy()
    future.loc[future.index[-1] + pd.Timedelta(days=1)] = future.iloc[-1] * 10
    as_of, _ = CandidateQualityEngine().relative_strength(future.iloc[:-1], market)
    assert as_of.score == original.score


def test_leading_sector_scores_above_lagging():
    leading = CandidateQualityEngine.sector_quality(frame(1), benchmark(-1))
    lagging = CandidateQualityEngine.sector_quality(frame(-1), benchmark(1))
    assert leading.score > lagging.score


def test_unknown_sector_is_not_perfect():
    result = CandidateQualityEngine.sector_quality(None, benchmark())
    assert result.score < 100 and result.status == "UNKNOWN"


def test_full_alignment_scores_above_falling_alignment():
    engine = CandidateQualityEngine()
    assert engine.trend_quality(frame(1)).score > engine.trend_quality(frame(-1)).score


def test_frequent_crossings_reduce_trend_stability():
    data = frame()
    data["Close"] = data["EMA20"] + np.tile([-2, 2], len(data) // 2)
    assert CandidateQualityEngine().trend_quality(data).factors["trend_stability"] < 60


def test_extension_penalty_works():
    normal = frame()
    extended = normal.copy()
    extended.loc[extended.index[-1], "Close"] += 30
    assert CandidateQualityEngine().trend_quality(extended).score < (
        CandidateQualityEngine().trend_quality(normal).score)


def momentum_data(rsi_values, hist_values, macd=.2, signal=.1):
    data = frame()
    data.loc[data.index[-len(rsi_values):], "RSI"] = rsi_values
    data.loc[data.index[-len(hist_values):], "MACD_HISTOGRAM"] = hist_values
    data.loc[data.index[-1], "MACD"] = macd
    data.loc[data.index[-1], "MACD_SIGNAL"] = signal
    return data


def test_oversold_falling_scores_low():
    result = CandidateQualityEngine.momentum_quality(
        momentum_data([32, 29, 27, 25, 23, 20], [.3, .2, .1]))
    assert result.score < 50
    assert "RSI_OVERSOLD_FALLING" in result.reason_codes


def test_oversold_rising_is_higher_but_not_maximum():
    falling = CandidateQualityEngine.momentum_quality(
        momentum_data([30, 28, 26, 24, 22, 20], [.3, .2, .1]))
    rising = CandidateQualityEngine.momentum_quality(
        momentum_data([20, 21, 22, 24, 26, 28], [.1, .2, .3]))
    assert falling.score < rising.score < 100


def test_rsi_crossing_30_is_recognized():
    result = CandidateQualityEngine.momentum_quality(
        momentum_data([25, 26, 27, 28, 29, 31], [.1, .2, .3]))
    assert "RSI_RECOVERY_CONFIRMED" in result.reason_codes


def test_improving_histogram_scores_above_weakening():
    improving = CandidateQualityEngine.momentum_quality(
        momentum_data([50] * 6, [.1, .2, .3]))
    weakening = CandidateQualityEngine.momentum_quality(
        momentum_data([50] * 6, [.3, .2, .1]))
    assert improving.score > weakening.score


def test_macd_below_zero_is_differentiated():
    positive = CandidateQualityEngine.momentum_quality(
        momentum_data([50] * 6, [.1, .2, .3], .2, .1))
    negative = CandidateQualityEngine.momentum_quality(
        momentum_data([50] * 6, [.1, .2, .3], -.1, -.2))
    assert positive.score > negative.score
    assert "MACD_BELOW_ZERO" in negative.reason_codes


def volume_candle(bullish=True, indecision=False, breakout=False):
    data = frame()
    index = data.index[-1]
    data.loc[index, "Volume"] = data["Volume"].iloc[-20:-1].mean() * 2
    data.loc[index, "RVOL"] = 2
    if indecision:
        data.loc[index, ["Open", "High", "Low", "Close"]] = [159.9, 161, 159, 160]
    elif bullish:
        data.loc[index, ["Open", "High", "Low", "Close"]] = [158, 162, 157.5, 161.5]
    else:
        data.loc[index, ["Open", "High", "Low", "Close"]] = [161, 161.5, 157, 157.2]
    return CandidateQualityEngine().volume_quality(data, breakout)


def test_high_volume_bullish_candle_positive():
    result = volume_candle(True)
    assert "BULLISH_VOLUME_CONFIRMATION" in result.reason_codes


def test_high_volume_bearish_is_distribution():
    assert "BEARISH_DISTRIBUTION" in volume_candle(False).reason_codes


def test_high_volume_indecision_not_bullish():
    result = volume_candle(indecision=True)
    assert "HIGH_VOLUME_INDECISION" in result.reason_codes


def test_low_volume_breakout_penalized():
    data = frame()
    result = CandidateQualityEngine().volume_quality(data, True)
    assert "BREAKOUT_WITHOUT_VOLUME" in result.reason_codes


def test_up_down_volume_ratio_is_finite():
    result = CandidateQualityEngine().volume_quality(frame())
    assert np.isfinite(result.factors["up_down_volume_ratio"])


def test_fresh_support_above_overtested_support():
    fresh = CandidateQualityEngine.support_resistance_quality(
        [{"price": 100, "touch_count": 2, "rejection_count": 3}], 105)
    tested = CandidateQualityEngine.support_resistance_quality(
        [{"price": 100, "touch_count": 8, "rejection_count": 3}], 105)
    assert fresh.score > tested.score


def test_failed_support_penalized():
    valid = CandidateQualityEngine.support_resistance_quality(
        [{"price": 100, "touch_count": 2}], 105)
    failed = CandidateQualityEngine.support_resistance_quality(
        [{"price": 100, "touch_count": 2, "failed": True}], 105)
    assert valid.score > failed.score


def test_clear_path_scores_above_multiple_resistance():
    clear = CandidateQualityEngine.path_quality(100, 120, [])
    blocked = CandidateQualityEngine.path_quality(100, 120, [105, 110, 115])
    assert clear.score > blocked.score


def test_major_supply_before_target_detected():
    result = CandidateQualityEngine.path_quality(
        100, 120, [], [{"lower": 110, "upper": 112}])
    assert "MAJOR_SUPPLY_BEFORE_TARGET" in result.reason_codes


@pytest.mark.parametrize("ratio,expected", [(4, 100), (3, 90), (2, 75), (1.5, 55), (1, 25), (.5, 0)])
def test_risk_reward_bands(ratio, expected):
    result = CandidateQualityEngine.risk_reward_quality({
        "entry": 100, "stop_loss": 90, "target1": 100 + ratio * 10,
    }, 5)
    assert result.score == expected


def test_breakout_quality_uses_the_same_projected_reward_as_trade_plan():
    result = CandidateQualityEngine.risk_reward_quality({
        "entry": 100,
        "stop_loss": 95,
        "target1": 102,
        "expected_reward": 10,
        "risk_reward": 2,
        "target_basis": "SECOND_TARGET_BREAKOUT",
    }, 5)

    assert result.status == "PASS"
    assert result.score == 75
    assert result.factors["risk_reward"] == 2


def test_invalid_stop_and_target_rejected():
    invalid_stop = CandidateQualityEngine.risk_reward_quality({
        "entry": 100, "stop_loss": 101, "target1": 120}, 5)
    invalid_target = CandidateQualityEngine.risk_reward_quality({
        "entry": 100, "stop_loss": 90, "target1": 99}, 5)
    assert "INVALID_STOP" in invalid_stop.reason_codes
    assert "INVALID_TARGET" in invalid_target.reason_codes


def test_tight_stop_penalized():
    result = CandidateQualityEngine.risk_reward_quality({
        "entry": 100, "stop_loss": 99.9, "target1": 105}, 5)
    assert result.score <= 35


def test_missing_fundamentals_policy_is_explicit():
    score = CandidateQualityEngine().fundamental_quality("TEST")
    assert score.score is None and score.status == "UNKNOWN"


def test_strong_fundamentals_above_balance_sheet_risk():
    strong = FundamentalSnapshot(
        "A", revenue_growth=15, profit_growth=20, roe=20, roce=22,
        debt_to_equity=.2, operating_cash_flow=100, promoter_pledge=0)
    weak = replace(strong, symbol="B", debt_to_equity=4, operating_cash_flow=-1)
    good = CandidateQualityEngine(
        fundamental_provider=FundamentalProvider(strong)).fundamental_quality("A")
    bad = CandidateQualityEngine(
        fundamental_provider=FundamentalProvider(weak)).fundamental_quality("B")
    assert good.score > bad.score


def test_fundamental_provider_failure_does_not_raise():
    score = CandidateQualityEngine(
        fundamental_provider=FundamentalProvider(failure=RuntimeError("offline"))
    ).fundamental_quality("A")
    assert score.status == "UNKNOWN"


@pytest.mark.parametrize("days,minimum", [(1, 0), (3, 40), (7, 70), (20, 90)])
def test_event_safety_windows(days, minimum):
    score = CandidateQualityEngine().event_safety({
        "event_data_availability_state": "COMPLETE",
        "days_to_event": days, "event_risk_level": "LOW", "hard_block": False,
    })
    assert score.score >= minimum


def test_unknown_event_status_is_not_perfect():
    score = CandidateQualityEngine().event_safety(None)
    assert score.status == "UNKNOWN" and score.score < 100


def test_bull_market_aligns_above_bear_market():
    bull = CandidateQualityEngine.market_alignment({"regime": "BULLISH"}, "TREND_FOLLOWING")
    bear = CandidateQualityEngine.market_alignment({"regime": "BEARISH"}, "TREND_FOLLOWING")
    assert bull.score > bear.score


def test_multitimeframe_alignment_and_missing_intraday_renormalization():
    result = CandidateQualityEngine.multi_timeframe(80, frame(), None)
    assert result.score is not None
    assert "INTRADAY_DATA_UNAVAILABLE" in result.reason_codes
    assert result.confidence == 80


def test_weekly_conflict_reduces_multitimeframe():
    aligned = CandidateQualityEngine.multi_timeframe(80, frame(1), 80)
    conflict = CandidateQualityEngine.multi_timeframe(80, frame(-1), 80)
    assert aligned.score > conflict.score


def option_payload(**updates):
    result = {
        "available": True, "spread_percent": 2, "open_interest": 20_000,
        "volume": 2_000, "strike": 90, "return_on_capital": 3,
        "iv_percentile": 70,
    }
    result.update(updates)
    return result


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"spread_percent": 10}, "OPTION_SPREAD_TOO_WIDE"),
        ({"open_interest": 10}, "OPTION_OI_TOO_LOW"),
        ({"volume": 10}, "OPTION_VOLUME_TOO_LOW"),
        ({"strike": 99}, "UNSAFE_STRIKE_DISTANCE"),
    ],
)
def test_option_hard_failures(updates, code):
    engine = CandidateQualityEngine()
    _, failures = engine.option_suitability(
        option_payload(**updates), 100,
        engine.fundamental_quality("A"),
        80, engine.event_safety({"event_data_availability_state": "COMPLETE",
                                 "days_to_event": 20, "event_risk_level": "LOW"}))
    assert code in failures


def test_event_blocks_option_selling():
    engine = CandidateQualityEngine()
    _, failures = engine.option_suitability(
        option_payload(), 100, engine.fundamental_quality("A"), 80,
        engine.event_safety({"event_data_availability_state": "COMPLETE",
                             "days_to_event": 1, "hard_block": True}))
    assert "EVENT_RISK_BLOCKS_OPTION_SELL" in failures


def test_missing_option_data_is_not_recommendation():
    engine = CandidateQualityEngine()
    score, failures = engine.option_suitability(
        {}, 100, engine.fundamental_quality("A"), 80, engine.event_safety(None))
    assert score.score is None and failures


def test_invalid_weight_total_rejected():
    with pytest.raises(ValueError, match="total 1.0"):
        QualityConfig(stock_quality_weights={"data_quality": .5})


def test_soft_sector_cap_creates_reserve():
    rows = [{"symbol": str(i), "sector": "BANK"} for i in range(4)]
    selected, reserve = apply_soft_sector_cap(rows, 2)
    assert len(selected) == 2 and len(reserve) == 2
    assert "SECTOR_CAP_REACHED" in reserve[0]["portfolio_reason_codes"]


def test_correlation_control_retains_higher_ranked_candidate():
    rows = [{"symbol": "A"}, {"symbol": "B"}]
    selected, reserve, conflicts = reduce_correlated_exposure(
        rows, {"A": frame(), "B": frame()}, .85, 60)
    assert [item["symbol"] for item in selected] == ["A"]
    assert [item["symbol"] for item in reserve] == ["B"]
    assert conflicts[0]["retained"] == "A"


def test_high_score_and_low_confidence_are_distinct():
    result = CandidateQualityEngine.multi_timeframe(100, None, None)
    assert result.score == 100
    assert result.confidence == 40


def test_new_signal_is_not_strictly_penalized_for_missing_history():
    result = CandidateQualityEngine.selection_stability(None)
    assert result.status == "NEW_SIGNAL"
    assert result.score == 50


def test_consistent_selection_history_scores_high():
    result = CandidateQualityEngine.selection_stability([
        {"rank": 4, "final_score": 75, "action": "WATCH"},
        {"rank": 4, "final_score": 76, "action": "WATCH"},
        {"rank": 5, "final_score": 75, "action": "WATCH"},
    ])
    assert result.score >= 80
