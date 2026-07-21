import unittest

from ui_app import (candidate_rows, likely_news_reaction, news_impact, news_rows,
                    option_leg_rows, snapshot_rows)


class UIMarketContextTests(unittest.TestCase):
    def test_news_impact_labels_cover_positive_and_negative_strength(self):
        self.assertEqual(news_impact(72), "SUPER POSITIVE")
        self.assertEqual(news_impact(20), "POSITIVE")
        self.assertEqual(news_impact(3), "NEUTRAL")
        self.assertEqual(news_impact(-20), "NEGATIVE")
        self.assertEqual(news_impact(-72), "SUPER NEGATIVE")

    def test_news_rows_show_each_headline_and_likely_reaction(self):
        report = {"trades": [{"symbol": "SBIN", "news": {
            "score": 40, "materiality": "MEDIUM",
            "headlines": [{"title": "SBIN profit beats estimates", "source": "Example",
                           "published": "2026-07-20T10:00:00+00:00"}],
            "article_assessments": [{
                "title": "SBIN profit beats estimates", "materiality": "HIGH",
                "probabilities": {"positive": 82, "negative": 5, "neutral": 13},
            }],
        }}]}
        rows = news_rows(report)
        self.assertEqual(rows[0]["Stock"], "SBIN")
        self.assertEqual(rows[0]["Impact"], "SUPER POSITIVE")
        self.assertIn("buying interest", rows[0]["Likely stock reaction"])
        self.assertIn("upward", likely_news_reaction("SUPER POSITIVE", "HIGH"))

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
            "symbol": "SBIN", "status": "TRADE",
            "levels": {"support": 790, "resistance": 825},
        }]})
        self.assertEqual(rows[0]["Support"], 790)
        self.assertEqual(rows[0]["Resistance"], 825)
        self.assertEqual(rows[0]["Executable trade"], "YES")

    def test_watchlist_candidate_is_not_presented_as_executable(self):
        rows = candidate_rows({"watchlist": [{"symbol": "PREMIERENE",
                                               "status": "WATCHLIST", "levels": {}}]})
        self.assertEqual(rows[0]["Executable trade"], "NO")

    def test_option_leg_rows_expose_exact_trade_strikes(self):
        rows = option_leg_rows({"option_strategy": {"trade": {"legs": [{
            "side": "BUY", "quantity": 1, "strike": 800,
            "option_type": "PE", "premium": 14.5,
        }]}}})
        self.assertEqual(rows[0]["Strike"], 800)
        self.assertEqual(rows[0]["Type"], "PE")


if __name__ == "__main__":
    unittest.main()
