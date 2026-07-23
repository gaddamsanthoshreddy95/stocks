from src.workflow.opportunity_ranking import (
    annotate_opportunity_rankings,
)


def candidate(symbol, *, status="WATCHLIST", action="WAIT_FOR_CONFIRMATION",
              readiness=70, quality=70, confirmation=70, rr=70, path=70,
              option_approved=False, option_valid=False, option_available=False,
              option_score=None):
    return {
        "symbol": symbol,
        "status": status,
        "final_action": action,
        "execution_readiness_score": readiness,
        "final_candidate_score": quality,
        "quality_score": quality,
        "entry_confirmation": {"score": confirmation, "passed": confirmation == 100},
        "risk_reward_quality_score": rr,
        "path_quality_score": path,
        "trade_eligibility": {"eligible": status == "TRADE"},
        "option_trade_approval": {
            "approved": option_approved,
            "rejection_codes": [] if option_approved else ["OPTION_NOT_APPROVED"],
        },
        "option_structure": {"valid": option_valid},
        "option_strategy": {"available": option_available},
        "option_sell_suitability_score": option_score,
    }


def test_actionable_candidate_ranks_above_higher_quality_wait_candidate():
    ready = candidate(
        "READY", status="TRADE", action="BUY", readiness=85, quality=65,
        confirmation=100,
    )
    waiting = candidate(
        "WAITING", readiness=55, quality=90, confirmation=40,
    )

    annotate_opportunity_rankings([waiting, ready])

    assert ready["actionability_rank"] == 1
    assert waiting["quality_rank"] == 1
    assert waiting["actionability_rank"] == 2


def test_failed_red_candle_confirmation_reduces_actionability_not_quality():
    confirmed = candidate("CONFIRMED", confirmation=100, quality=70)
    red_candle = candidate("RED", confirmation=50, quality=80)

    annotate_opportunity_rankings([red_candle, confirmed])

    assert red_candle["quality_rank"] == 1
    assert confirmed["actionability_rank"] == 1
    assert red_candle["actionability_score"] < confirmed["actionability_score"]


def test_failed_entry_wait_action_is_not_promoted_by_prepare_readiness():
    waiting = candidate("WAITING", action="WAIT_FOR_CONFIRMATION")
    waiting["execution_status"] = "PREPARE"

    annotate_opportunity_rankings([waiting])

    assert waiting["actionability_bucket"] == "WAIT_FOR_CONFIRMATION"


def test_option_rank_requires_approval_valid_structure_and_available_strategy():
    liquid = candidate(
        "LIQUID", option_approved=True, option_valid=True,
        option_available=True, option_score=80,
    )
    illiquid = candidate(
        "ILLIQUID", option_approved=False, option_valid=False,
        option_available=False, option_score=95,
    )

    annotate_opportunity_rankings([illiquid, liquid])

    assert liquid["option_selling_rank"] == 1
    assert liquid["option_selling_eligible"]
    assert illiquid["option_selling_rank"] is None
    assert not illiquid["option_selling_eligible"]
