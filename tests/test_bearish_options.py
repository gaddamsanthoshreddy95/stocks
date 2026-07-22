import unittest
from src.application.platform import TradingPlatform
from src.application.settings import PlatformSettings


class BearishOptionTests(unittest.TestCase):
    def test_bearish_trade_plan_places_stop_above_and_targets_below_entry(self):
        plan = TradingPlatform._bearish_trade_plan({
            "analysis": {"current_price": 100, "atr": 2},
            "entry": {"support": 94, "resistance": 102},
        })
        self.assertGreater(plan["stop_loss"], plan["entry"])
        self.assertLess(plan["target1"], plan["entry"])
        self.assertEqual(plan["risk_reward"], 3.0)

    def test_bearish_stock_filters_are_directionally_mirrored(self):
        settings = PlatformSettings(market_data_source="cache")
        result = TradingPlatform._bearish_stock_selection_filters(
            plan={"entry": 100, "stop_loss": 102, "risk_reward": 2},
            analysis={"ema20": 101, "atr": 2, "relative_volume": 1.2},
            adverse={"available": True, "sample_count": 40,
                     "probability_target_before_adverse_barrier": 60,
                     "probability_no_overnight_gap_beyond_barrier": 98},
            relative_strength={"available": True, "relative_strength": -5},
            sector={"available": True, "score": 30}, liquidity={"score": 80},
            settings=settings,
        )
        self.assertTrue(result["passed"])

    def test_strong_relative_stock_or_sector_blocks_bearish_selection(self):
        settings = PlatformSettings(market_data_source="cache")
        common = dict(
            plan={"entry": 100, "stop_loss": 102, "risk_reward": 2},
            analysis={"ema20": 101, "atr": 2, "relative_volume": 1.2},
            adverse={"available": True, "sample_count": 40,
                     "probability_target_before_adverse_barrier": 60,
                     "probability_no_overnight_gap_beyond_barrier": 98},
            liquidity={"score": 80}, settings=settings,
        )
        strong_stock = TradingPlatform._bearish_stock_selection_filters(
            **common, relative_strength={"available": True, "relative_strength": 4},
            sector={"available": True, "score": 30})
        strong_sector = TradingPlatform._bearish_stock_selection_filters(
            **common, relative_strength={"available": True, "relative_strength": -4},
            sector={"available": True, "score": 70})
        self.assertIn("negative_relative_strength", strong_stock["failed_checks"])
        self.assertIn("weak_sector", strong_sector["failed_checks"])
    def test_validated_bearish_pattern_selects_defined_risk_credit_spread(self):
        overlay = TradingPlatform._candlestick_option_overlay({
            "candlestick": {"pattern": "SHOOTING STAR", "strength": 88,
                            "context_validated": True}
        }, 70)
        self.assertEqual(overlay["strategy"], "Bear Call Spread")
        self.assertEqual(overlay["direction"], "BEARISH")

    def test_weak_reversal_requires_next_candle(self):
        overlay = TradingPlatform._candlestick_option_overlay({
            "candlestick": {"pattern": "HAMMER", "strength": 70,
                            "context_validated": True}
        }, 70)
        self.assertEqual(overlay["status"], "WAIT_NEXT_CANDLE")

    def test_contextual_doji_requires_high_iv_for_condor(self):
        candidate = {"candlestick": {"pattern": "DOJI", "strength": 60,
                                      "context_validated": True}}
        self.assertEqual(TradingPlatform._candlestick_option_overlay(candidate, 50)["status"],
                         "IV_TOO_LOW")
        self.assertEqual(TradingPlatform._candlestick_option_overlay(candidate, 70)["strategy"],
                         "Iron Condor")
    def test_confirmed_bearish_evidence_scores_independently(self):
        result = TradingPlatform._bearish_option_score({
            "trend": "STRONG_BEARISH", "current_price": 90, "ema20": 100,
            "macd": -2, "macd_signal_line": -1, "rsi": 38, "relative_volume": 1.1,
        }, {"signal": "SELL"})
        self.assertEqual(result["score"], 100)
        self.assertTrue(result["confirmed"])

    def test_bearish_label_alone_is_not_confirmation(self):
        result = TradingPlatform._bearish_option_score({
            "trend": "BEARISH", "current_price": 105, "ema20": 100,
            "macd": 1, "macd_signal_line": 0, "rsi": 60, "relative_volume": .5,
        }, {"signal": "NEUTRAL"})
        self.assertEqual(result["score"], 35)
        self.assertFalse(result["confirmed"])


if __name__ == "__main__":
    unittest.main()
