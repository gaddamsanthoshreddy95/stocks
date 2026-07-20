import unittest

from src.workflow.decision_policy import (
    classify_setup, market_alignment, normalize_market_regime,
    option_confidence_status, pcr_adjustment,
    market_risk_scale,
    combine_strategy_eligibility,
    risk_reward_tier,
    adaptive_market_policy,
    expected_value,
)


class DecisionPolicyTests(unittest.TestCase):
    def test_low_confidence_strong_bearish_is_uncertain(self):
        self.assertEqual(normalize_market_regime("STRONG_BEARISH", 40), "UNCERTAIN_BEARISH")
        self.assertEqual(market_alignment("UNCERTAIN_BEARISH", 40, "BULLISH")["status"], "UNCERTAIN")

    def test_pcr_is_a_bounded_score_adjustment(self):
        self.assertEqual(pcr_adjustment(.52, "BULLISH"), -7)
        self.assertEqual(pcr_adjustment(.52, "BEARISH"), 6)

    def test_option_confidence_tiers(self):
        self.assertEqual(option_confidence_status(70), "CONFIRMED")
        self.assertEqual(option_confidence_status(40), "CONFLICT")
        self.assertEqual(option_confidence_status(20), "UNRELIABLE")

    def test_pullback_classification(self):
        self.assertEqual(classify_setup("STRONG_BULLISH", "BEARISH"), "BULLISH_PULLBACK")

    def test_low_market_confidence_halves_risk(self):
        self.assertEqual(market_risk_scale(20), .5)
        self.assertEqual(market_risk_scale(60), .75)
        self.assertEqual(market_risk_scale(80), 1.0)
        self.assertEqual(market_risk_scale(0, available=False), 1.0)
        self.assertEqual(market_risk_scale(80, alignment_status="CONFLICT"), .5)

    def test_risk_reward_is_confidence_tiered(self):
        self.assertFalse(risk_reward_tier(82, 1.4)["approved"])
        self.assertTrue(risk_reward_tier(76, 1.3)["approved"])
        self.assertTrue(risk_reward_tier(68, 1.2)["watchlist_eligible"])
        self.assertFalse(risk_reward_tier(68, 1.19)["watchlist_eligible"])

    def test_market_regime_changes_execution_thresholds(self):
        self.assertEqual(adaptive_market_policy("BULLISH")["readiness_minimum"], 75)
        self.assertEqual(adaptive_market_policy("BEARISH")["readiness_minimum"], 90)
        self.assertIn("MEAN_REVERSION", adaptive_market_policy("SIDEWAYS")["preferred_strategies"])

    def test_expected_value_uses_probability_reward_and_risk(self):
        self.assertEqual(expected_value(60, 200, 100), {
            "amount": 80.0, "risk_multiple": .8, "win_probability": 60.0,
        })

    def test_equity_rr_rejection_does_not_block_approved_short_put(self):
        result = combine_strategy_eligibility(False, .4, 1.5, True)
        self.assertFalse(result["equity_approved"])
        self.assertTrue(result["short_put_approved"])
        self.assertTrue(result["any_approved"])
