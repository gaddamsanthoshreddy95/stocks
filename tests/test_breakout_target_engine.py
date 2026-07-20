import unittest

from src.trade_plan.trade_plan import TradePlanEngine


def entry_report(probability=70):
    return {
        "current_price": 100,
        "support": 95,
        "resistance": 102,
        "next_resistance": 110,
        "resistance_levels": [102, 110],
        "atr": 5,
        "quality": "POOR",
        "breakout_probability": probability,
    }


class BreakoutTargetEngineTests(unittest.TestCase):
    def test_credible_breakout_uses_weighted_expected_reward(self):
        plan = TradePlanEngine.generate(entry_report(75))

        self.assertEqual(plan.target1, 102)
        self.assertEqual(plan.target2, 110)
        self.assertEqual(plan.target3, 115)
        self.assertEqual(plan.expected_reward, 7.0)
        self.assertEqual(plan.risk_reward, 1.4)
        self.assertEqual(plan.target_basis, "BREAKOUT_WEIGHTED_TARGETS")

    def test_low_breakout_probability_remains_conservative(self):
        plan = TradePlanEngine.generate(entry_report(50))

        self.assertEqual(plan.expected_reward, 2)
        self.assertEqual(plan.risk_reward, .4)
        self.assertEqual(plan.target_basis, "NEAREST_RESISTANCE")
        self.assertTrue(any("Target too close" in reason for reason in plan.diagnostics))

    def test_contextual_probability_can_override_technical_estimate(self):
        plan = TradePlanEngine.generate(entry_report(20), breakout_probability=80)

        self.assertEqual(plan.breakout_probability, 80)
        self.assertEqual(plan.target_basis, "SECOND_TARGET_BREAKOUT")
        self.assertEqual(plan.expected_reward, 10)
        self.assertEqual(plan.risk_reward, 2)


if __name__ == "__main__":
    unittest.main()
