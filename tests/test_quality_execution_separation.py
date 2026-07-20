import unittest
from types import SimpleNamespace

from src.application.settings import PlatformSettings
from src.workflow.daily_trading_assistant import DailyTradingAssistant


def assistant(**settings):
    instance = object.__new__(DailyTradingAssistant)
    instance.platform = SimpleNamespace(settings=PlatformSettings(
        market_data_source="cache", **settings
    ))
    instance.completed_outcomes = 0
    return instance


class QualityExecutionSeparationTests(unittest.TestCase):
    def test_quality_grade_boundaries(self):
        engine = assistant()
        expected = ((92, "A+"), (85, "A"), (80, "B+"), (75, "B"),
                    (70, "C+"), (65, "C"), (64.99, "D"))
        for score, grade in expected:
            with self.subTest(score=score):
                self.assertEqual(engine._quality_grade(score)["grade"], grade)

    def test_high_quality_is_not_downgraded_by_incomplete_entry(self):
        engine = assistant()
        analysis = {"analysis": {"trend": "STRONG BULLISH"}}
        candidate = {
            "technical_score": 90,
            "stock_liquidity": {"status": "EXCELLENT", "score": 98},
            "trust": {"status": "TRUSTED", "score": 96},
        }
        quality = engine._quality_score(
            analysis, candidate, {"available": True, "rating": "VERY STRONG", "score": 100},
            {"risk_reward": 2.1}, {"risk_multiple": 1.1}, 89, "STRONG BULLISH",
        )
        self.assertIn(quality["grade"], {"A", "A+"})
        # Entry readiness is deliberately absent from every intrinsic factor.
        self.assertNotIn("entry_confirmation", quality["factors"])

    def test_unavailable_relative_strength_is_excluded_not_neutralized(self):
        engine = assistant()
        quality = engine._quality_score(
            {"analysis": {"trend": "BULLISH"}},
            {"technical_score": 80,
             "stock_liquidity": {"status": "HIGH", "score": 80},
             "trust": {"status": "TRUSTED", "score": 80}},
            {"available": False, "rating": "UNAVAILABLE"},
            {"risk_reward": 1.5}, {"risk_multiple": .75}, 84, "BULLISH",
        )
        self.assertIsNone(quality["factors"]["relative_strength_quality"])

    def test_history_is_not_a_readiness_failure_during_calibration(self):
        engine = assistant(calibration_min_outcomes=200)
        result = engine._trade_readiness(
            {"breakout": {"confirmed": False},
             "setup_evaluation": {"stage_2": {"eligible": False}},
             "candlestick": {"signal": "NEUTRAL"},
             "analysis": {"relative_volume": 1.3}},
            {"technical_score": 88, "trade_plan": {"risk_reward": 2.0}},
            {"status": "UNCERTAIN"}, {"available": False, "rating": "UNAVAILABLE"},
            {"valid": True, "status": "VALID"}, "TREND FOLLOWING",
            {"sample_quality": "INSUFFICIENT"}, {"sample_quality": "INSUFFICIENT"},
        )
        history = [item for item in result["checks"] if "history" in item["name"]]
        self.assertTrue(all(not item["counted"] for item in history))
        self.assertTrue(all(item["state"] == "NEUTRAL" for item in history))
        self.assertEqual(result["total"], 7)

    def test_expected_value_ranking_is_default_and_configurable(self):
        first = {"expected_value": {"risk_multiple": 1.2}, "quality_score": 80,
                 "execution_readiness_score": 60, "probability": 80, "ai_score": 70}
        second = {"expected_value": {"risk_multiple": .5}, "quality_score": 95,
                  "execution_readiness_score": 90, "probability": 95, "ai_score": 95}
        self.assertGreater(assistant()._ranking_key(first), assistant()._ranking_key(second))
        quality_engine = assistant(candidate_ranking_mode="QUALITY_SCORE")
        self.assertGreater(quality_engine._ranking_key(second), quality_engine._ranking_key(first))

    def test_adaptive_execution_states_are_modest_and_independent(self):
        engine = assistant()
        self.assertEqual(engine._execution_state(76, "BULLISH")["status"], "EXECUTE")
        self.assertEqual(engine._execution_state(76, "UNCERTAIN_BEARISH")["status"], "PREPARE")
        self.assertEqual(engine._execution_state(56, "UNCERTAIN_BEARISH")["status"], "WATCH_INTRADAY")
        self.assertEqual(engine._execution_state(45, "STRONG_BEARISH")["status"], "WAIT")

    def test_valid_option_execution_survives_weak_directional_context(self):
        engine = assistant()
        score = engine._execution_score(
            {"analysis": {"trend": "STRONG BULLISH", "relative_volume": 1.3}},
            {"technical_score": 90}, {"score": 50},
            {"confidence": 40, "entry_validation": {"approved": True}},
            {"valid": True, "status": "VALID"},
            {"analysis_state": "ANALYSIS_FAILED", "score": 0}, {}, {},
            "BULLISH", {"risk_reward": 2.0},
        )
        option = next(item for item in score["factors"] if item["name"] == "option_context")
        self.assertEqual(option["score"], 80)
        self.assertGreaterEqual(score["score"], 70)


if __name__ == "__main__":
    unittest.main()
