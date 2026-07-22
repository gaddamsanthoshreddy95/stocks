import unittest

import pandas as pd

from src.risk.adverse_move import AdverseMoveRisk


class AdverseMoveRiskTests(unittest.TestCase):
    @staticmethod
    def intraday_history(gap_percent=0.0):
        rows, index = [], []
        previous_close = 100.0
        for day in pd.bdate_range("2025-01-01", periods=130):
            session_close = previous_close * 1.003
            session_open = previous_close * (1 + gap_percent / 100)
            prices = [session_open, (session_open + session_close) / 2,
                      session_close * .999, session_close]
            for offset, price in enumerate(prices):
                index.append(day + pd.Timedelta(hours=9, minutes=15 + offset * 15))
                rows.append({"Open": price, "High": price * 1.004,
                             "Low": price * .996, "Close": price})
            previous_close = session_close
        return pd.DataFrame(rows, index=pd.DatetimeIndex(index))

    @staticmethod
    def bearish_intraday_history(gap_percent=0.0):
        rows, index = [], []
        previous_close = 150.0
        for day in pd.bdate_range("2025-01-01", periods=130):
            session_close = previous_close * .997
            session_open = previous_close * (1 + gap_percent / 100)
            prices = [session_open, (session_open + session_close) / 2,
                      session_close * 1.001, session_close]
            for offset, price in enumerate(prices):
                index.append(day + pd.Timedelta(hours=9, minutes=15 + offset * 15))
                rows.append({"Open": price, "High": price * 1.004,
                             "Low": price * .996, "Close": price})
            previous_close = session_close
        return pd.DataFrame(rows, index=pd.DatetimeIndex(index))

    def test_stable_bullish_history_reports_high_three_percent_hold_probability(self):
        rows = []
        close = 100.0
        for _ in range(220):
            close *= 1.003
            rows.append({"Close": close, "High": close * 1.004, "Low": close * .996})
        result = AdverseMoveRisk.assess(
            pd.DataFrame(rows), target_percent=1.0, adverse_percent=3.0,
            horizon_days=5, minimum_samples=60,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["probability_stays_above_adverse_barrier"], 100.0)
        self.assertEqual(result["probability_adverse_barrier_before_target"], 0.0)
        self.assertGreater(result["probability_target_before_adverse_barrier"], 90)

    def test_insufficient_comparable_windows_fail_closed(self):
        frame = pd.DataFrame({"Close": range(100, 130), "High": range(101, 131),
                              "Low": range(99, 129)})
        result = AdverseMoveRisk.assess(frame, 2, minimum_samples=60)
        self.assertFalse(result["available"])
        self.assertIn("required", result["reason"])

    def test_same_day_target_and_barrier_is_counted_adverse_first(self):
        rows = []
        close = 100.0
        for index in range(220):
            close *= 1.002
            rows.append({"Close": close, "High": close * 1.05, "Low": close * .95})
        result = AdverseMoveRisk.assess(pd.DataFrame(rows), 2, 3, 5, 60)
        self.assertTrue(result["available"])
        self.assertEqual(result["probability_target_before_adverse_barrier"], 0.0)
        self.assertEqual(result["probability_stays_above_adverse_barrier"], 0.0)
        self.assertEqual(result["probability_adverse_barrier_before_target"], 100.0)

    def test_intraday_model_resolves_order_and_reports_overnight_gap_probability(self):
        result = AdverseMoveRisk.assess_intraday(
            self.intraday_history(), target_percent=1, adverse_percent=3,
            horizon_days=5, minimum_samples=40,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["data_resolution"], "15_MINUTE")
        self.assertEqual(result["probability_no_overnight_gap_beyond_barrier"], 100.0)
        self.assertGreater(result["probability_target_before_adverse_barrier"], 90)

    def test_intraday_model_detects_overnight_gap_before_later_recovery(self):
        result = AdverseMoveRisk.assess_intraday(
            self.intraday_history(gap_percent=-4), target_percent=1, adverse_percent=3,
            horizon_days=5, minimum_samples=40,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["probability_overnight_gap_beyond_barrier"], 100.0)
        self.assertEqual(result["probability_target_before_adverse_barrier"], 0.0)
        self.assertEqual(result["probability_adverse_barrier_before_target"], 100.0)

    def test_bearish_model_uses_downside_target_and_upside_adverse_barrier(self):
        result = AdverseMoveRisk.assess_intraday(
            self.bearish_intraday_history(), 1, 3, 5, 30, direction="BEARISH"
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["direction"], "BEARISH")
        self.assertGreater(result["probability_target_before_adverse_barrier"], 90)
        self.assertEqual(result["probability_no_overnight_gap_beyond_barrier"], 100.0)

    def test_bearish_model_counts_upward_overnight_gap_as_adverse(self):
        result = AdverseMoveRisk.assess_intraday(
            self.bearish_intraday_history(gap_percent=4), 1, 3, 5, 30, direction="BEARISH"
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["probability_overnight_gap_beyond_barrier"], 100.0)
        self.assertEqual(result["probability_target_before_adverse_barrier"], 0.0)


if __name__ == "__main__":
    unittest.main()
