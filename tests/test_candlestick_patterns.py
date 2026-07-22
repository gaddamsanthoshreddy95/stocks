import unittest

import pandas as pd

from src.candlestick.pattern_detector import PatternDetector


class CandlestickPatternTests(unittest.TestCase):
    def test_supported_set_includes_contextual_tweezers(self):
        self.assertEqual(len(PatternDetector.SUPPORTED_PATTERNS), 18)
        self.assertIn("TWEEZER TOP", PatternDetector.SUPPORTED_PATTERNS)
        self.assertIn("TWEEZER BOTTOM", PatternDetector.SUPPORTED_PATTERNS)
        self.assertNotIn("HANGING MAN", PatternDetector.SUPPORTED_PATTERNS)
        self.assertNotIn("INVERTED HAMMER", PatternDetector.SUPPORTED_PATTERNS)

    def test_bullish_engulfing_with_volume_confirmation(self):
        history = [
            {"Open": close + 1, "Close": close, "High": close + 2, "Low": close - 1,
             "RVOL": 1.0, "RSI": 42, "MACD": -.2, "MACD_SIGNAL": -.1}
            for close in (120, 117, 115, 112, 110, 108, 106, 104)
        ]
        frame = pd.DataFrame([*history,
            {"Open": 110, "Close": 100, "High": 112, "Low": 98, "RVOL": 0.8},
            {"Open": 99, "Close": 113, "High": 114, "Low": 97, "RVOL": 1.8,
             "RSI": 40, "MACD": .1, "MACD_SIGNAL": 0},
        ])
        result = PatternDetector.detect(frame)
        self.assertEqual(result["pattern"], "BULLISH ENGULFING")
        self.assertEqual(result["signal"], "BUY")
        self.assertGreater(result["strength"], 90)
        self.assertTrue(result["context_validated"])

    def test_engulfing_geometry_without_prior_downtrend_is_rejected(self):
        history = [{"Open": close - 1, "Close": close, "High": close + 1, "Low": close - 2}
                   for close in (100, 102, 104, 106, 108, 110)]
        frame = pd.DataFrame([*history,
                              {"Open": 112, "Close": 108, "High": 113, "Low": 107},
                              {"Open": 107, "Close": 114, "High": 115, "Low": 106}])
        self.assertNotEqual(PatternDetector.detect(frame)["pattern"], "BULLISH ENGULFING")

    def test_hammer_requires_a_prior_downtrend(self):
        decline = [{"Open": close + .5, "Close": close, "High": close + 1, "Low": close - 1,
                    "RSI": 40, "MACD": 0, "MACD_SIGNAL": .1, "RVOL": 1}
                   for close in (120, 117, 114, 111, 108, 106, 104, 102)]
        hammer = {"Open": 100, "Close": 101, "High": 101.5, "Low": 95,
                  "RSI": 40, "MACD": .2, "MACD_SIGNAL": .1, "RVOL": 1.6}
        self.assertEqual(PatternDetector.detect(pd.DataFrame([*decline, hammer]))["pattern"], "HAMMER")

        advance = [{"Open": close - .5, "Close": close, "High": close + 1, "Low": close - 1}
                   for close in (100, 103, 106, 109, 112, 115, 118, 121)]
        self.assertNotEqual(PatternDetector.detect(pd.DataFrame([*advance, hammer]))["pattern"],
                            "HAMMER")

    def test_tweezer_top_uses_atr_tolerance_and_requires_uptrend(self):
        advance = [{"Open": close - 1, "Close": close, "High": close + 1, "Low": close - 2,
                    "ATR": 3, "RSI": 65, "MACD": .2, "MACD_SIGNAL": .3, "RVOL": 1.4}
                   for close in (100, 103, 106, 109, 112, 115, 118, 121)]
        pair = [{"Open": 122, "Close": 124, "High": 125, "Low": 121, "ATR": 3},
                {"Open": 124, "Close": 122.5, "High": 125.2, "Low": 121, "ATR": 3,
                 "RSI": 68, "MACD": .2, "MACD_SIGNAL": .3, "RVOL": 1.5}]
        result = PatternDetector.detect(pd.DataFrame([*advance, *pair]))
        self.assertEqual(result["pattern"], "TWEEZER TOP")
        self.assertEqual(result["signal"], "SELL")

    def test_doji_is_actionable_only_at_support_or_resistance(self):
        advance = [{"Open": close - 1, "Close": close, "High": close + 1, "Low": close - 1,
                    "ATR": 2} for close in (100, 102, 104, 106, 108, 110, 112, 114)]
        doji = {"Open": 115, "Close": 115.05, "High": 117, "Low": 113,
                "ATR": 2, "RVOL": 1, "RSI": 60, "MACD": 0, "MACD_SIGNAL": 0}
        result = PatternDetector.detect(pd.DataFrame([*advance, doji]))
        self.assertEqual(result["pattern"], "DOJI")
        self.assertTrue(result["context_validated"])
        self.assertEqual(result["location_context"], "RESISTANCE")
