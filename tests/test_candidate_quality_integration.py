"""Frozen-universe integration and ranking regression tests."""

from dataclasses import replace

import pandas as pd
import pytest

from src.quality.backtest import compare_rankings, outcome_metrics, slice_as_of
from src.quality.config import QualityConfig
from src.quality.engine import CandidateQualityEngine
from tests.test_candidate_quality import frame, option_payload


def assessment(
    *, direction=1, sector_score=80, rs_score=80, event_days=20,
    option=None, risk_reward=2, setup="TREND_FOLLOWING",
):
    data = frame(direction)
    close = float(data["Close"].iloc[-1])
    risk = 5
    plan = {
        "entry": close, "stop_loss": close - risk,
        "target1": close + risk * risk_reward,
    }
    analysis = {
        "analysis": {"atr": 3},
        "breakout": {"confirmed": setup == "BREAKOUT"},
        "candlestick": {"signal": "BUY" if direction > 0 else "SELL"},
        "entry": {"resistance_levels": [close + 8]},
        "price_action": {"zones": [{
            "price": close - 3, "touch_count": 2, "rejection_count": 3,
        }]},
        "supply_demand": {"supply_zones": []},
    }
    candidate = {
        "symbol": "TEST", "current_price": close, "technical_score": 80,
        "stock_liquidity": {"score": 85},
    }
    event = {
        "event_data_availability_state": "COMPLETE",
        "days_to_event": event_days, "event_risk_level": "LOW",
        "hard_block": event_days <= 1,
    }
    return CandidateQualityEngine().assess(
        symbol="TEST", daily_data=data, candidate=candidate, analysis=analysis,
        relative_strength={"score": rs_score},
        sector={"available": sector_score is not None, "score": sector_score},
        market={"regime": "BULLISH" if direction > 0 else "BEARISH"},
        event=event, option=option or option_payload(),
        setup=setup, plan=plan, weekly_data=data.resample("W-FRI").last(),
    )


def test_strong_stock_in_strong_sector_scores_well():
    result = assessment()
    assert result.final_candidate_score >= 60
    assert result.scores["sector_strength"].score == 80


def test_strong_stock_in_weak_sector_is_penalized():
    strong = assessment(sector_score=85)
    weak = assessment(sector_score=20)
    assert strong.final_candidate_score > weak.final_candidate_score


def test_weak_stock_in_strong_sector_does_not_become_strong():
    result = assessment(direction=-1, sector_score=90, rs_score=20)
    assert result.scores["trend_quality"].score < 50


def test_high_rvol_bearish_distribution_is_a_risk():
    data = frame(-1)
    data.loc[data.index[-1], "RVOL"] = 2
    data.loc[data.index[-1], "Volume"] *= 2
    close = float(data["Close"].iloc[-1])
    data.loc[data.index[-1], ["Open", "High", "Low", "Close"]] = [
        close + 3, close + 3.2, close - .2, close]
    result = CandidateQualityEngine().volume_quality(data)
    assert "BEARISH_DISTRIBUTION" in result.reason_codes


def test_good_setup_with_poor_risk_reward_fails_gate():
    result = assessment(risk_reward=.8)
    assert not result.hard_gates["minimum_risk_reward"]


def test_good_setup_with_nearby_event_is_not_ready():
    safe = assessment(event_days=20)
    event = assessment(event_days=1)
    assert safe.scores["entry_readiness"].score > event.scores["entry_readiness"].score
    assert not event.hard_gates["event_safety"]


def test_option_candidate_with_poor_liquidity_is_not_suitable():
    result = assessment(option=option_payload(open_interest=1, volume=1))
    assert result.scores["option_sell_suitability"].status == "FAIL"


def test_missing_sector_reduces_confidence():
    known = assessment(sector_score=80)
    missing = assessment(sector_score=None)
    assert known.analysis_confidence_score > missing.analysis_confidence_score


def test_all_selected_and_rejected_assessments_are_explainable():
    for result in (assessment(), assessment(direction=-1, risk_reward=.5)):
        assert result.reason_codes
        assert result.strengths or result.risks
        if result.hard_gate_failures:
            assert all(result.hard_gate_failures)


def test_quality_component_timings_are_exposed():
    result = assessment()
    for name in (
        "trend_quality_seconds", "momentum_quality_seconds",
        "directional_volume_seconds", "support_resistance_quality_seconds",
        "fundamental_quality_seconds", "event_safety_seconds",
        "multi_timeframe_seconds", "option_suitability_seconds",
        "quality_assessment_seconds",
    ):
        assert name in result.timings


def test_shadow_ranking_does_not_change_legacy_selection():
    config = QualityConfig()
    assert config.ranking_mode == "SHADOW"
    rows = [
        {"symbol": "A", "legacy": 3, "final_candidate_score": 10,
         "entry_readiness_score": 10, "risk_reward_quality_score": 10},
        {"symbol": "B", "legacy": 2, "final_candidate_score": 90,
         "entry_readiness_score": 90, "risk_reward_quality_score": 90},
    ]
    comparison = compare_rankings(rows, 1, lambda item: (item["legacy"],))
    assert comparison["legacy_shortlist"] == ["A"]
    assert comparison["composite_shortlist"] == ["B"]


def test_legacy_and_composite_modes_validate():
    assert replace(QualityConfig(), ranking_mode="LEGACY").ranking_mode == "LEGACY"
    assert replace(QualityConfig(), ranking_mode="COMPOSITE").ranking_mode == "COMPOSITE"


def test_as_of_slice_prevents_future_leakage():
    data = frame()
    as_of = data.index[-20]
    sliced = slice_as_of(data, as_of)
    assert sliced.index.max() == as_of
    assert len(sliced) < len(data)


def test_outcome_metrics_calculate_expectancy_and_drawdown():
    result = outcome_metrics([
        {"return_percent": 3, "mae_percent": -1, "mfe_percent": 4},
        {"return_percent": -1, "mae_percent": -2, "mfe_percent": 1},
    ])
    assert result["expectancy"] == 1
    assert result["maximum_drawdown"] == 1


@pytest.mark.parametrize("limit,overlap", [(1, 0), (2, 2)])
def test_legacy_composite_overlap(limit, overlap):
    rows = [
        {"symbol": "A", "legacy": 2, "final_candidate_score": 10,
         "entry_readiness_score": 10, "risk_reward_quality_score": 10},
        {"symbol": "B", "legacy": 1, "final_candidate_score": 90,
         "entry_readiness_score": 90, "risk_reward_quality_score": 90},
    ]
    assert compare_rankings(
        rows, limit, lambda item: (item["legacy"],))["overlap"] == overlap
