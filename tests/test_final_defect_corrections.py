from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import pandas as pd

from src.application.settings import PlatformSettings
from src.event_risk.models import (
    EventCategory, EventDirection, EventRiskItem, EventSeverity,
)
from src.event_risk.service import DailyEventContext, EventRiskService
from src.market.relative_strength import RelativeStrength
from src.news.ai_sentiment import AISentimentAnalyzer
from src.workflow.context_enrichment import ContextEnrichment
from src.workflow.daily_trading_assistant import DailyTradingAssistant


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


class FinalDefectCorrectionTests(unittest.TestCase):
    @staticmethod
    def _prices(start, daily_step, dates):
        return pd.DataFrame({"Close": [start + daily_step * index for index in range(len(dates))]},
                            index=dates)

    def test_relative_strength_aligns_dates_and_calculates_returns(self):
        stock_dates = pd.bdate_range("2026-01-01", periods=90)
        market_dates = pd.bdate_range("2026-01-05", periods=90)
        stock = self._prices(100, 1, stock_dates)
        market = self._prices(200, .5, market_dates)
        with patch("src.market.relative_strength.yf.download", side_effect=[stock, market]):
            result = RelativeStrength.analyze("TEST", minimum_sessions=60)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertGreaterEqual(result["sample_count"], 60)
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_model"], "CROSS_SECTIONAL_PERCENTILE_PENDING")
        self.assertNotEqual(result["stock_return"], result["market_return"])

    def test_twenty_identical_relative_strength_values_fail_safely(self):
        rows = [{"available": True, "status": "AVAILABLE", "rating": "UNDERPERFORM",
                 "relative_strength": -8.0, "score": None} for _ in range(20)]
        distribution = ContextEnrichment.finalize_relative_strength(rows)
        self.assertIsNotNone(distribution["warning"])
        self.assertTrue(all(row["status"] == "FAILED" and row["score"] is None for row in rows))

    def test_relative_strength_percentiles_are_not_categorical_fallbacks(self):
        rows = [{"available": True, "status": "AVAILABLE", "rating": "UNDERPERFORM",
                 "relative_strength": float(value), "score": None} for value in range(-20, 0)]
        distribution = ContextEnrichment.finalize_relative_strength(rows)
        self.assertIsNone(distribution["warning"])
        self.assertEqual(len(set(row["score"] for row in rows)), 20)

    def test_unavailable_relative_strength_has_none_score_and_reason(self):
        with patch.object(RelativeStrength, "analyze", return_value={
            "status": "UNAVAILABLE", "score": None, "reason": "benchmark unavailable",
        }):
            result = ContextEnrichment(live=True).relative_strength("NO_DATA")
        self.assertFalse(result["available"])
        self.assertIsNone(result["score"])
        self.assertEqual(result["reason"], "benchmark unavailable")

    def test_unavailable_sector_market_data_is_candidate_aggregate_only(self):
        rows = DailyTradingAssistant._rank_sectors(
            [{"sector": "BANKING", "ai_score": 75.52}],
            {"BANKING": {"available": False, "status": "UNAVAILABLE",
                         "score": None, "rating": "UNAVAILABLE"}},
        )
        self.assertIsNone(rows[0]["sector_market_score"])
        self.assertEqual(rows[0]["candidate_aggregate_score"], 75.52)
        self.assertEqual(rows[0]["ranking_basis"], "CANDIDATE_AGGREGATE_ONLY")

    def test_zero_impact_background_event_is_not_common_candidate_category(self):
        reviewed = [{"event_risk": {"primary_category": "NONE", "event_risk_score": 0,
                                    "stock_specific_score": 0, "sector_specific_score": 0,
                                    "market_wide_score": 0, "matched_events": []}}]
        self.assertEqual(DailyTradingAssistant._canonical_event_clusters(reviewed), [])

    def test_event_availability_summary_matches_candidate_states(self):
        reviewed = [{"event_risk": {"event_data_availability_state": state}}
                    for state in ("COMPLETE", "PARTIAL", "UNAVAILABLE", "FAILED", "NOT_REQUESTED")]
        counts = DailyTradingAssistant._event_data_counts(reviewed)
        self.assertEqual({state: counts[state] for state in
                          ("COMPLETE", "PARTIAL", "UNAVAILABLE", "FAILED", "NOT_REQUESTED")},
                         {state: 1 for state in
                          ("COMPLETE", "PARTIAL", "UNAVAILABLE", "FAILED", "NOT_REQUESTED")})

    def test_event_uncertainty_penalty_calibration_states(self):
        settings = PlatformSettings(
            market_data_source="cache", event_data_complete_no_match_penalty=0,
            event_data_partial_coverage_penalty=2, event_data_not_requested_penalty=0,
            event_data_primary_unavailable_penalty=3, event_data_all_unavailable_penalty=5,
            event_data_fetch_failed_penalty=8,
        )
        service = EventRiskService(settings)
        expected = {"COMPLETE": 0, "PARTIAL": 2, "NOT_REQUESTED": 0,
                    "PRIMARY_UNAVAILABLE": 3, "UNAVAILABLE": 5, "FAILED": 8}
        data_states = {"NOT_REQUESTED": "DISABLED", "UNAVAILABLE": "UNAVAILABLE",
                       "FAILED": "FAILED"}
        for coverage, penalty in expected.items():
            with self.subTest(coverage=coverage):
                result = service.assess_candidate(
                    {"symbol": "TEST", "sector": "BANKING"},
                    DailyEventContext([], data_states.get(coverage, "FRESH"), [], {}, coverage),
                    base_readiness=80, as_of=NOW,
                )
                self.assertEqual(result.event_data_uncertainty_penalty, penalty)
                self.assertEqual(result.readiness_penalty, 0)

    def test_very_low_event_penalty_respects_configured_bound(self):
        settings = PlatformSettings(market_data_source="cache", event_penalty_max_very_low=3)
        item = EventRiskItem(
            "small", "Small negative event", "SMALL_EVENT", EventCategory.COMPANY,
            EventSeverity.VERY_LOW, EventDirection.NEGATIVE, detected_at=NOW,
            last_updated_at=NOW, raw_score=18, decayed_score=18, confidence=.9,
            gap_risk=100, affected_symbols=["TEST"], is_confirmed=True,
        )
        result = EventRiskService(settings).assess_candidate(
            {"symbol": "TEST", "sector": "BANKING"},
            DailyEventContext([item], "FRESH", [], {}, "COMPLETE"),
            base_readiness=80, as_of=NOW,
        )
        self.assertEqual(result.event_risk_level, "VERY_LOW")
        self.assertLessEqual(result.readiness_penalty, 3)

    def test_not_requested_by_policy_is_not_fetch_failed(self):
        result = DailyTradingAssistant._normalize_news_state({
            "requested": False, "available": False, "sentiment": "UNAVAILABLE",
        })
        self.assertEqual(result["news_state"], "NOT_REQUESTED_BY_POLICY")
        self.assertNotEqual(result["news_state"], "FETCH_FAILED")

    def test_overnight_summary_identifies_exact_causes(self):
        reviewed = [
            {"overnight_hold_allowed": False, "event_risk": {"overnight_block_cause": cause}}
            for cause in ("EVENT_LEVEL_BLOCK", "GAP_RISK_BLOCK", "MARKET_POLICY_BLOCK", None)
        ]
        counts = DailyTradingAssistant._overnight_block_counts(reviewed)
        self.assertEqual(counts["EVENT_LEVEL_BLOCK"], 1)
        self.assertEqual(counts["GAP_RISK_BLOCK"], 1)
        self.assertEqual(counts["MARKET_POLICY_BLOCK"], 1)
        self.assertEqual(counts["OTHER_BLOCK"], 1)

    def test_missing_entity_model_is_reported_as_disabled_safe(self):
        def find_spec(name):
            return object() if name == "spacy" else None
        with patch("src.news.ai_sentiment.importlib.util.find_spec", side_effect=find_spec):
            health = AISentimentAnalyzer().dependency_health()
        self.assertEqual(health["entity_model_status"], "MISSING")
        self.assertEqual(health["entity_dependent_classification"], "DISABLED_SAFE")


if __name__ == "__main__":
    unittest.main()
