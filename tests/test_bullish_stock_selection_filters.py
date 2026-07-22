import unittest

from src.application.settings import PlatformSettings
from src.workflow.daily_trading_assistant import DailyTradingAssistant


def evaluate(**overrides):
    values = {
        "plan": {"entry": 100, "stop_loss": 98, "risk_reward": 2},
        "setup": "BREAKOUT",
        "technical": {"relative_volume": 1.4},
        "entry_quality": {"position_size_guidance": "NORMAL", "extension_band": "NORMAL"},
        "adverse": {"available": True, "sample_count": 50,
                    "probability_target_before_adverse_barrier": 60,
                    "probability_no_overnight_gap_beyond_barrier": 98},
        "relative_strength": {"available": True, "score": 65},
        "sector": {"available": True, "score": 60},
        "liquidity": {"score": 80},
        "settings": PlatformSettings(market_data_source="cache"),
    }
    values.update(overrides)
    return DailyTradingAssistant._bullish_stock_selection_filters(**values)


class BullishStockSelectionFilterTests(unittest.TestCase):
    def test_complete_high_quality_stock_passes(self):
        self.assertTrue(evaluate()["passed"])

    def test_logical_stop_beyond_three_percent_blocks_stock(self):
        result = evaluate(plan={"entry": 100, "stop_loss": 96, "risk_reward": 2})
        self.assertFalse(result["passed"])
        self.assertIn("logical_stop_within_limit", result["failed_checks"])

    def test_low_target_before_drawdown_probability_blocks_stock(self):
        result = evaluate(adverse={"available": True, "sample_count": 50,
                                   "probability_target_before_adverse_barrier": 45,
                                   "probability_no_overnight_gap_beyond_barrier": 99})
        self.assertIn("target_before_adverse_probability", result["failed_checks"])

    def test_weak_stock_or_sector_blocks_stock(self):
        weak_stock = evaluate(relative_strength={"available": True, "score": 45})
        weak_sector = evaluate(sector={"available": True, "score": 35})
        self.assertIn("positive_stock_relative_strength", weak_stock["failed_checks"])
        self.assertIn("supportive_sector", weak_sector["failed_checks"])

    def test_overextended_or_illiquid_stock_blocks(self):
        extended = evaluate(entry_quality={"position_size_guidance": "ZERO_UNTIL_RETEST",
                                           "extension_band": "RETEST_REQUIRED"})
        illiquid = evaluate(liquidity={"score": 20})
        self.assertIn("entry_not_overextended", extended["failed_checks"])
        self.assertIn("exit_liquidity", illiquid["failed_checks"])


if __name__ == "__main__":
    unittest.main()
