from src.workflow.continuation_assessment import assess_continuation
from src.workflow.opportunity_ranking import annotate_opportunity_rankings


def _technical(**overrides):
    values = {
        "current_price": 1680,
        "ema20": 1545,
        "atr": 42,
        "rsi": 80,
        "relative_volume": 4,
        "volume_state": "VERY HIGH",
        "macd": 50,
        "macd_signal_line": 45,
        "macd_histogram": 5,
    }
    values.update(overrides)
    return values


def test_extended_breakout_is_do_not_chase_even_with_strong_volume():
    result = assess_continuation(
        technical=_technical(),
        levels={"entry": 1680, "stop_loss": 1520, "target_1": 1840},
        setup_evaluation={"stage_1": {"evidence": {
            "breakout_retest_holds": False,
            "breakout_consolidation_holds": False,
        }}},
        breakout_confirmed=True,
        entry_confirmed=False,
        alignment_status="ALIGNED",
        sector_score=75,
        day_open=1625,
        day_high=1685,
        day_low=1617,
    )

    assert result["extension_atr"] > 3
    assert result["state"] == "EXTENDED_DO_NOT_CHASE"
    assert result["recommended_action"] == "WAIT_FOR_NEW_BASE"
    assert result["executable_now"] is False
    assert "NO_CHASE_EXTENSION" in {item["code"] for item in result["penalties"]}


def test_good_location_can_be_buy_now_when_continuation_and_entry_confirm():
    result = assess_continuation(
        technical=_technical(
            current_price=105, ema20=102, atr=5, rsi=66,
            relative_volume=1.4, macd=3, macd_signal_line=2, macd_histogram=1,
        ),
        levels={"entry": 104, "stop_loss": 100, "target_1": 115},
        setup_evaluation={"stage_1": {"evidence": {
            "breakout_retest_holds": True,
            "breakout_consolidation_holds": False,
        }}},
        breakout_confirmed=True,
        entry_confirmed=True,
        alignment_status="ALIGNED",
        sector_score=80,
        day_open=103,
        day_high=106,
        day_low=100,
    )

    assert result["extension_atr"] < .75
    assert result["remaining_risk_reward"] == 2
    assert result["state"] == "BUY_NOW"
    assert result["executable_now"] is True


def test_extended_candidate_is_demoted_in_actionability_ranking():
    extended = {
        "symbol": "EXTENDED", "status": "WATCHLIST", "final_action": "WATCHLIST",
        "execution_readiness_score": 90, "final_candidate_score": 90,
        "entry_confirmation": {"score": 90}, "risk_reward_quality_score": 90,
        "path_quality_score": 90,
        "continuation_assessment": {"state": "EXTENDED_DO_NOT_CHASE", "score": 30},
    }
    normal = {
        "symbol": "NORMAL", "status": "WATCHLIST", "final_action": "WATCHLIST",
        "execution_readiness_score": 60, "final_candidate_score": 60,
        "entry_confirmation": {"score": 60}, "risk_reward_quality_score": 60,
        "path_quality_score": 60,
        "continuation_assessment": {"state": "WAIT_FOR_CONFIRMATION", "score": 60},
    }

    annotate_opportunity_rankings([extended, normal])

    assert extended["actionability_bucket"] == "EXTENDED"
    assert extended["actionability_score"] < 40
    assert normal["actionability_rank"] < extended["actionability_rank"]


def test_five_percent_intraday_move_is_hard_no_chase_even_with_retest():
    result = assess_continuation(
        technical=_technical(
            current_price=105.5, ema20=104.5, atr=4, rsi=64,
            relative_volume=1.4, macd=2, macd_signal_line=1, macd_histogram=1,
        ),
        levels={"entry": 105.5, "stop_loss": 103, "target_1": 112},
        setup_evaluation={"stage_1": {"evidence": {
            "breakout_retest_holds": True,
        }}},
        breakout_confirmed=True,
        entry_confirmed=True,
        alignment_status="ALIGNED",
        sector_score=80,
        day_open=100,
        day_high=106,
        day_low=99.5,
    )

    assert result["move_from_open_percent"] == 5.5
    assert result["extension_atr"] < 1.5
    assert result["retest_or_consolidation_holds"] is True
    assert result["state"] == "EXTENDED_DO_NOT_CHASE"
    assert result["recommended_action"] == "WAIT_FOR_NEW_BASE"
    assert result["executable_now"] is False
    assert "INTRADAY_MOVE_ALREADY_EXTENDED" in {
        item["code"] for item in result["penalties"]
    }
