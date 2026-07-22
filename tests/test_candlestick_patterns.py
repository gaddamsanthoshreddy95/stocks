import unittest

import pandas as pd

from src.candlestick.pattern_detector import PatternDetector


class CandlestickPatternTests(unittest.TestCase):
    def test_supported_set_is_the_compact_sixteen_patterns(self):
        self.assertEqual(len(PatternDetector.SUPPORTED_PATTERNS), 16)
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
