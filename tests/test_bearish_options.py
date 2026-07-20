import unittest

from src.application.platform import TradingPlatform


class BearishOptionTests(unittest.TestCase):
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
