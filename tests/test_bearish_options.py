import unittest

from src.application.platform import TradingPlatform


class BearishOptionTests(unittest.TestCase):
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
