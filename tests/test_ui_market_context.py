import unittest

from ui_app import candidate_rows, option_leg_rows, snapshot_rows


class UIMarketContextTests(unittest.TestCase):
    def test_snapshot_rows_preserve_global_quote_status(self):
        rows = snapshot_rows({"sp500_futures": {"price": 6000, "change": -10,
                                                 "change_percent": -.17}})
        self.assertEqual(rows[0]["Market"], "Sp500 Futures")
        self.assertEqual(rows[0]["Status"], "AVAILABLE")

    def test_missing_price_is_unavailable(self):
        rows = snapshot_rows({"nikkei": {"price": None}})
        self.assertEqual(rows[0]["Status"], "UNAVAILABLE")

    def test_candidate_rows_expose_support_and_resistance(self):
        rows = candidate_rows({"trades": [{
            "symbol": "SBIN", "levels": {"support": 790, "resistance": 825},
        }]})
        self.assertEqual(rows[0]["Support"], 790)
        self.assertEqual(rows[0]["Resistance"], 825)

    def test_option_leg_rows_expose_exact_trade_strikes(self):
        rows = option_leg_rows({"option_strategy": {"trade": {"legs": [{
            "side": "BUY", "quantity": 1, "strike": 800,
            "option_type": "PE", "premium": 14.5,
        }]}}})
        self.assertEqual(rows[0]["Strike"], 800)
        self.assertEqual(rows[0]["Type"], "PE")


if __name__ == "__main__":
    unittest.main()
