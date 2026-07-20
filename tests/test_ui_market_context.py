import unittest

from ui_app import snapshot_rows


class UIMarketContextTests(unittest.TestCase):
    def test_snapshot_rows_preserve_global_quote_status(self):
        rows = snapshot_rows({"sp500_futures": {"price": 6000, "change": -10,
                                                 "change_percent": -.17}})
        self.assertEqual(rows[0]["Market"], "Sp500 Futures")
        self.assertEqual(rows[0]["Status"], "AVAILABLE")

    def test_missing_price_is_unavailable(self):
        rows = snapshot_rows({"nikkei": {"price": None}})
        self.assertEqual(rows[0]["Status"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
