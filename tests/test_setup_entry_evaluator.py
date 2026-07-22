import unittest
from types import SimpleNamespace

import pandas as pd

from src.decision.setup_entry_evaluator import SetupEntryEvaluator


def frame(confirmed=False):
    rows = []
    lows = [94, 95, 96, 97, 98, 98.5, 99, 99.2]
    for index, low in enumerate(lows):
        rows.append({"Close": 100, "Low": low, "EMA20": 99 if confirmed else 105,
                     "EMA50": 105, "EMA200": 95, "RSI": 29,
                     "MACD": -0.9, "MACD_SIGNAL": -1.0, "MACD_HISTOGRAM": .1,
                     "RVOL": 1.3 if confirmed else .4})
    return pd.DataFrame(rows)


class SetupEntryEvaluatorTests(unittest.TestCase):
    @staticmethod
    def full_frame(last_open=99, last_close=101, ema20=100, atr=2):
        rows = []
        for index in range(8):
            close = 100 + index * .1
            rows.append({"Open": close - .4, "High": close + .5, "Low": close - .6,
                         "Close": close, "EMA20": ema20, "EMA50": 98, "EMA200": 95,
                         "RSI": 50, "MACD": .5, "MACD_SIGNAL": .3,
                         "MACD_HISTOGRAM": .2, "RVOL": 1.4, "ATR": atr})
        rows[-1].update({"Open": last_open, "Close": last_close,
                         "High": max(last_open, last_close) + .5,
                         "Low": min(last_open, last_close) - .5})
        return pd.DataFrame(rows)

    def test_oversold_is_candidate_not_trade(self):
        result = SetupEntryEvaluator.evaluate(
            frame(False), SimpleNamespace(score=65), {"support": 98, "risk_reward": 2},
            {"confirmed": False}, {"signal": "NEUTRAL"},
        )
        self.assertEqual(result["stage_1"]["category"], "REVERSAL CANDIDATE")
        self.assertEqual(result["momentum_label"], "EARLY REVERSAL")
        self.assertEqual(result["stage_2"]["status"], "WAIT")
        self.assertFalse(result["stage_2"]["eligible"])

    def test_reversal_requires_every_confirmation(self):
        result = SetupEntryEvaluator.evaluate(
            frame(True), SimpleNamespace(score=65), {"support": 98, "risk_reward": 2},
            {"confirmed": False}, {"signal": "BUY"},
        )
        self.assertEqual(result["stage_1"]["category"], "REVERSAL CANDIDATE")
        self.assertTrue(result["stage_2"]["eligible"])
        self.assertEqual(result["stage_2"]["status"], "TRADE_ELIGIBLE")
        self.assertEqual(result["stage_2"]["missing"], [])

    def test_reversal_setup_requires_good_reward_risk(self):
        result = SetupEntryEvaluator.evaluate(
            frame(False), SimpleNamespace(score=50), {"support": 98, "risk_reward": 1.2},
            {"confirmed": False}, {"signal": "NEUTRAL"},
        )
        self.assertNotEqual(result["stage_1"]["category"], "REVERSAL CANDIDATE")
        self.assertFalse(result["stage_1"]["evidence"]["risk_reward_at_least_1_5"])

    def test_higher_low_is_not_an_extra_entry_gate(self):
        data = frame(True)
        data["Low"] = [99.5, 99.4, 99.3, 99.2, 99.1, 99.0, 98.9, 98.8]
        result = SetupEntryEvaluator.evaluate(
            data, SimpleNamespace(score=65), {"support": 98, "risk_reward": 2},
            {"confirmed": False}, {"signal": "BUY"},
        )
        self.assertTrue(result["stage_2"]["eligible"])
        self.assertNotIn("higher_low", result["stage_2"]["checks"])

    def test_latest_red_candle_blocks_immediate_entry(self):
        result = SetupEntryEvaluator.evaluate(
            self.full_frame(last_open=102, last_close=101), SimpleNamespace(score=75),
            {"support": 99, "risk_reward": 2}, {"confirmed": False}, {"signal": "BUY"},
        )
        self.assertFalse(result["stage_2"]["eligible"])
        self.assertIn("latest_candle_green", result["stage_2"]["missing"])

    def test_large_ema20_atr_extension_blocks_immediate_entry(self):
        result = SetupEntryEvaluator.evaluate(
            self.full_frame(last_open=109, last_close=110, ema20=100, atr=2),
            SimpleNamespace(score=75), {"support": 99, "risk_reward": 2},
            {"confirmed": False}, {"signal": "BUY"},
        )
        self.assertFalse(result["stage_2"]["eligible"])
        self.assertIn("not_overextended_above_ema20", result["stage_2"]["missing"])
