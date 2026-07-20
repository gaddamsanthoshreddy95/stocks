"""Regression tests for the public final-report workflow."""

import unittest
from unittest.mock import patch

from src.application.platform import TradingPlatform
from src.application.settings import PlatformSettings
from src.presenter.daily_report import DailyReportPresenter
from src.workflow.daily_trading_assistant import DailyTradingAssistant


class DailyReportContractTests(unittest.TestCase):
    def test_null_short_put_candidate_is_normalized(self):
        self.assertEqual(
            DailyTradingAssistant._short_put_candidate({"candidate": None}), {}
        )

    def test_unavailable_context_is_omitted_from_weighted_score(self):
        score = DailyTradingAssistant._available_weighted_score([
            (80, .55, True), (20, .10, False), (60, .05, True),
        ])
        self.assertAlmostEqual(score, (80 * .55 + 60 * .05) / .60)

    def test_news_collection_and_analysis_states_are_independent(self):
        news = DailyTradingAssistant._normalize_news_state({
            "available": False, "article_count": 2,
            "headlines": [{"title": "Results"}], "analysis_method": "AI_UNAVAILABLE",
        })
        self.assertEqual(news["collection_state"], "FETCHED")
        self.assertEqual(news["analysis_state"], "FAILED")

    @patch("src.workflow.daily_trading_assistant.OutcomeRepository")
    def test_cache_report_contains_actionable_trade_fields(self, outcome_repository):
        outcome_repository.return_value.calibrated_probability.return_value = None
        outcome_repository.return_value.contextual_probability.return_value = None
        outcome_repository.return_value.learning_summary.return_value = {
            "completed_outcomes": 0, "by_symbol": [], "by_setup_and_regime": []
        }
        outcome_repository.return_value.record_recommendation.return_value = "test-recommendation"
        platform = TradingPlatform(
            settings=PlatformSettings(market_data_source="cache", capital=100_000, risk_percent=1)
        )
        report = platform.daily_report(limit=1, minimum_score=40)

        self.assertEqual(report["report_type"], "daily_trading_assistant")
        self.assertIn("filter_stages", report)
        self.assertIn("sector_ranking", report)
        self.assertIn("timings", report)
        self.assertIn("total_seconds", report["timings"])
        self.assertIn("news_model_load_seconds", report["timings"])
        self.assertIn("news_inference_seconds", report["timings"])
        self.assertIn("news_network_seconds", report["timings"])
        self.assertLessEqual(report["timings"]["candidates_enriched"], 20)
        self.assertEqual(report["timings"]["news_stocks_requested"], 0)
        self.assertEqual(
            report["summary"]["trades_generated"] + report["summary"]["watchlisted"]
            + report["summary"]["rejected"],
            report["summary"]["stocks_qualified"],
        )
        for trade in report["trades"] + report["watchlist"]:
            self.assertIn("levels", trade)
            self.assertIn("risk", trade)
            self.assertIn("option_strategy", trade)
            self.assertIn("stock_liquidity", trade)
            self.assertIn("trust", trade)
            self.assertIn("model_confidence", trade)
            self.assertIn("trade_eligibility", trade)
            self.assertIn("trade_readiness", trade)
            self.assertIn("risk_reward_policy", trade)
            self.assertIn("expected_value", trade)
            self.assertIn("market_policy", trade)
            self.assertIn(trade["trade_readiness"]["classification"], {
                "EXECUTE", "PREPARE_ORDER", "WATCH_INTRADAY", "IGNORE",
            })
            self.assertIn("collection_state", trade["news"])
            self.assertIn("analysis_state", trade["news"])
            self.assertIn("current_month_seasonality", trade)
            self.assertTrue(trade["option_budget_policy"]["stock_eligibility_independent"])
        self.assertIn("TODAY'S MARKET SUMMARY", DailyReportPresenter.render(report))
