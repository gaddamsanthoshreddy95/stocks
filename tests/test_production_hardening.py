from datetime import datetime, timezone
import json
import tempfile
import unittest
from unittest.mock import Mock
import pandas as pd
from requests.exceptions import ConnectionError

from src.application.settings import PlatformSettings
from src.application.platform import TradingPlatform
from src.application.errors import DataUnavailableError
from src.event_risk.models import EventCategory, EventDirection, EventRiskItem, EventSeverity
from src.event_risk.service import DailyEventContext, EventRiskService
from src.learning.recommendation_journal import RecommendationJournal
from src.presenter.daily_report import DailyReportPresenter
from src.sector.sector_strength import SectorStrength
from src.workflow.context_enrichment import ContextEnrichment
from src.workflow.daily_trading_assistant import DailyTradingAssistant
from src.workflow.final_decision import (
    ConsistencyError, EntryConfirmationResult, FinalAction,
    FinalConsistencyValidator, FinalDecisionEngine,
)


class ProductionHardeningTests(unittest.TestCase):
    def entry(self, passed=True):
        return EntryConfirmationResult(passed, 100 if passed else 0, (), () if passed else ("volume",),
                                       datetime.now(timezone.utc).isoformat())

    def test_final_engine_is_deterministic_and_uses_allowed_actions(self):
        decision = FinalDecisionEngine.decide(
            direction="BULLISH", entry=self.entry(), readiness_status="EXECUTE",
            eligible=True, hard_block=False, critical_failure=False, news_complete=True,
        )
        self.assertEqual(decision.action, FinalAction.BUY)
        self.assertTrue(decision.executable)
        waiting = FinalDecisionEngine.decide(
            direction="BULLISH", entry=self.entry(False), readiness_status="EXECUTE",
            eligible=True, hard_block=False, critical_failure=False, news_complete=True,
        )
        self.assertEqual(waiting.action, FinalAction.WAIT_FOR_CONFIRMATION)
        self.assertFalse(waiting.executable)

    def test_incomplete_news_blocks_execution(self):
        decision = FinalDecisionEngine.decide(
            direction="BULLISH", entry=self.entry(), readiness_status="EXECUTE",
            eligible=True, hard_block=False, critical_failure=False, news_complete=False,
        )
        self.assertEqual(decision.action, FinalAction.WAIT_FOR_CONFIRMATION)

    def test_consistency_validator_rejects_blocked_nonzero_position(self):
        trade = {
            "final_action": "NO_TRADE", "action": "NO_TRADE", "recommendation": "NO_TRADE",
            "trade_eligibility": {"eligible": False}, "entry_confirmation": {"passed": False},
            "option_trade_approval": {"status": "REJECTED"}, "option_structure": {"valid": False},
            "event_risk": {"event_risk_level": "VERY_LOW"},
            "news": {"news_state": "NOT_REQUESTED", "sentiment": "UNAVAILABLE"},
            "risk": {"quantity": 1},
        }
        with self.assertRaises(ConsistencyError):
            FinalConsistencyValidator.validate(trade)

    def test_earnings_classifier_avoids_generic_false_positives(self):
        for headline in ("EPS growth remains strong", "Profit improves over five years",
                         "General earnings commentary", "Quarterly result analysis from last year"):
            self.assertIn(EventRiskService._earnings_state(headline), {
                "GENERAL_EARNINGS_REFERENCE", "HISTORICAL_EARNINGS_COMMENTARY"
            })
        self.assertEqual(EventRiskService._earnings_state("Company results due tomorrow"),
                         "UPCOMING_EARNINGS")
        self.assertEqual(EventRiskService._earnings_state("Company results just released"),
                         "RESULTS_JUST_RELEASED")

    def test_federal_bank_earnings_warrant_headline_is_not_geopolitical(self):
        events = EventRiskService(PlatformSettings(market_data_source="cache"))._news_events(
            "FEDERALBNK", {
                "collection_state": "FETCHED", "analysis_state": "ANALYZED",
                "confidence": 93.11, "entity_model_available": False,
                "headlines": [{"title": "Do Federal Bank's Earnings Warrant Your Attention?"}],
            }, datetime.now(timezone.utc),
        )
        self.assertFalse(any(item.category == EventCategory.GEOPOLITICAL for item in events))
        self.assertEqual(events, [])

    def test_geopolitical_confidence_requires_entity_model(self):
        events = EventRiskService(PlatformSettings(market_data_source="cache"))._news_events(
            "SBIN", {
                "collection_state": "FETCHED", "analysis_state": "ANALYZED",
                "confidence": 93.11, "entity_model_available": False,
                "headlines": [{"title": "Iran conflict attack raises market risks"}],
            }, datetime.now(timezone.utc),
        )
        self.assertEqual(events[0].category, EventCategory.GEOPOLITICAL)
        self.assertLessEqual(events[0].confidence, .35)
        self.assertFalse(events[0].is_confirmed)

    def test_unavailable_relative_strength_has_no_synthetic_score(self):
        result = ContextEnrichment(live=False).relative_strength("FEDERALBNK")
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertIsNone(result["score"])

    def test_unavailable_sector_context_is_not_weak(self):
        engine = SectorStrength()
        engine.download_symbol = Mock(return_value=None)
        result = engine.analyze()
        self.assertTrue(result)
        self.assertTrue(all(row["status"] == "UNAVAILABLE" for row in result.values()))
        self.assertTrue(all(row["rating"] == "UNAVAILABLE" for row in result.values()))
        self.assertTrue(all(row["score"] is None for row in result.values()))

    def test_nan_sector_context_is_not_available_or_weak(self):
        engine = SectorStrength()
        engine.download_symbol = Mock(return_value={"price": float("nan"),
                                                     "change_percent": float("nan")})
        result = engine.analyze()
        self.assertTrue(all(row["status"] == "UNAVAILABLE" for row in result.values()))
        self.assertTrue(all(row["rating"] == "UNAVAILABLE" for row in result.values()))
        self.assertTrue(all(row["score"] is None for row in result.values()))

    def test_available_sectors_receive_cross_sectional_scores(self):
        engine = SectorStrength()
        changes = iter([-2.0, -1.0, 0.0, 1.0, 2.0] * 10)
        engine.download_symbol = Mock(side_effect=lambda _symbol: {
            "price": 100.0, "change_percent": next(changes),
        })
        result = engine.analyze()
        scores = {row["score"] for row in result.values()}
        self.assertGreater(len(scores), 1)
        self.assertEqual(min(scores), 0.0)
        self.assertEqual(max(scores), 100.0)
        self.assertTrue(all(row["score_model"] ==
                            "SECTOR_RETURN_CROSS_SECTIONAL_PERCENTILE"
                            for row in result.values()))

    def test_sector_strength_prefers_kite_index_history(self):
        history = pd.DataFrame({"Close": [100.0, 102.0]})
        provider = Mock()
        provider.get_data.return_value = history
        engine = SectorStrength(historical_provider=provider)
        engine.download_symbol = Mock(return_value=None)
        result = engine.analyze()
        self.assertTrue(all(row["status"] == "AVAILABLE" for row in result.values()))
        self.assertTrue(all(row["source"] == "KITE" for row in result.values()))

    def test_sector_strength_falls_back_to_constituent_composite(self):
        history = pd.DataFrame({"Close": [100 + index * .5 for index in range(60)]})
        provider = Mock()
        provider.get_data.side_effect = lambda symbol: (
            pd.DataFrame() if str(symbol).startswith("NIFTY ") else history
        )
        engine = SectorStrength(historical_provider=provider)
        engine.download_symbol = Mock(return_value=None)
        result = engine.analyze()
        banking = result["BANKING"]
        self.assertEqual(banking["status"], "AVAILABLE")
        self.assertEqual(banking["source"], "KITE_CONSTITUENTS")
        self.assertEqual(banking["score_model"], "CONSTITUENT_TECHNICAL_COMPOSITE")
        self.assertIsNotNone(banking["score"])
        self.assertGreater(banking["sample_count"], 0)

    def test_canonical_option_rejection_ignores_legacy_approval(self):
        trade = {
            "option_trade_approval": {"status": "REJECTED"},
            "option_strategy": {"entry_validation": {"approved": True}},
        }
        self.assertEqual(DailyReportPresenter._option_approval_status(trade), "REJECTED")

    def test_timing_event_metrics_are_counts(self):
        for name in ("event_sources_requested", "events_detected", "event_clusters_created"):
            rendered = DailyReportPresenter._timing_metric(name, 3)
            self.assertIn("3 count", rendered)
            self.assertNotIn("stocks", rendered)
            self.assertNotIn("seconds", rendered)

    def test_news_unknown_is_never_neutral(self):
        news = DailyTradingAssistant._normalize_news_state({
            "requested": True, "available": False, "article_count": 0, "sentiment": "NEUTRAL",
        })
        self.assertEqual(news["news_state"], "NOT_FETCHED")
        self.assertEqual(news["sentiment"], "UNAVAILABLE")

    def test_option_structure_and_trade_approval_are_separate(self):
        structure = DailyTradingAssistant._option_structure({
            "available": True,
            "trade": {"available": True, "expiry": "2026-08-27", "legs": [{
                "premium": 2, "last_price": 2, "bid": 1.99, "ask": 2.01,
                "strike": 100, "quantity": 50, "lot_size": 50,
                "open_interest": 20000, "volume": 2000, "implied_volatility": 25,
                "quote_is_stale": False,
            }]},
        })
        self.assertTrue(structure["valid"])
        decision = FinalDecisionEngine.decide(
            direction="BULLISH", entry=self.entry(False), readiness_status="EXECUTE",
            eligible=True, hard_block=False, critical_failure=False, news_complete=True,
        )
        self.assertEqual(decision.action, FinalAction.WAIT_FOR_CONFIRMATION)

    def test_unlimited_profit_option_has_nullable_payoff_ratio(self):
        payoff = DailyTradingAssistant._option_payoff_metrics({
            "maximum_profit": None, "maximum_loss": 5000,
        })
        self.assertIsNone(payoff["option_payoff_rr"])
        self.assertEqual(payoff["option_payoff_profile"], "UNLIMITED_PROFIT")
        self.assertTrue(payoff["option_payoff_rr_requirement_passed"])

    def test_rejected_setup_cannot_report_passed_confirmation(self):
        result = EntryConfirmationResult.from_checks({
            "price_above_ema20": False, "macd_above_signal": True,
        }, required=True)
        self.assertFalse(result.passed)
        self.assertEqual(result.score, 50)

    def test_event_scores_are_separated_by_scope(self):
        now = datetime.now(timezone.utc)
        direct = EventRiskItem("direct", "Direct", "REGULATORY_ACTION", EventCategory.REGULATORY,
                               EventSeverity.HIGH, EventDirection.NEGATIVE, affected_symbols=["SBIN"],
                               raw_score=75, decayed_score=75, confidence=.9, is_confirmed=True,
                               detected_at=now, last_updated_at=now)
        market = EventRiskItem("market", "Market", "MARKET_WIDE_STRESS", EventCategory.MARKET_WIDE,
                               EventSeverity.HIGH, EventDirection.VOLATILITY_ONLY,
                               raw_score=70, decayed_score=70, confidence=.9, is_confirmed=True,
                               detected_at=now, last_updated_at=now)
        result = EventRiskService(PlatformSettings(market_data_source="cache")).assess_candidate(
            {"symbol": "SBIN", "sector": "BANKING"}, DailyEventContext([direct, market], "FRESH", [], {}),
            base_readiness=90, as_of=now,
        )
        self.assertGreater(result.stock_specific_score, 0)
        self.assertGreater(result.market_wide_score, 0)
        self.assertEqual(result.freshness_state, "FRESH")

    def test_primary_freshness_and_stale_supporting_source_are_separate(self):
        now = datetime.now(timezone.utc)
        primary = EventRiskItem("primary", "Fresh regulatory event", "REGULATORY_ACTION",
                                EventCategory.REGULATORY, EventSeverity.HIGH, EventDirection.NEGATIVE,
                                affected_symbols=["SBIN"], raw_score=75, decayed_score=75,
                                confidence=.9, freshness_score=100, is_confirmed=True,
                                detected_at=now, last_updated_at=now)
        supporting = EventRiskItem("support", "Old supporting event", "REGULATORY_ACTION_2",
                                   EventCategory.REGULATORY, EventSeverity.HIGH, EventDirection.NEGATIVE,
                                   affected_symbols=["SBIN"], raw_score=75, decayed_score=75,
                                   confidence=.9, freshness_score=40, is_confirmed=True,
                                   detected_at=now, last_updated_at=now)
        result = EventRiskService(PlatformSettings(market_data_source="cache")).assess_candidate(
            {"symbol": "SBIN", "sector": "BANKING"},
            DailyEventContext([primary, supporting], "FRESH", [], {}),
            base_readiness=90, as_of=now,
        )
        self.assertEqual(result.primary_event_freshness_state, "FRESH")
        self.assertEqual(result.supporting_sources_freshness_state, "STALE")
        self.assertNotIn("Event source data is stale.", result.warnings)
        self.assertTrue(any("Supporting event sources" in warning for warning in result.warnings))

    def test_summary_cluster_category_uses_candidate_canonical_category(self):
        reviewed = [{"event_risk": {"matched_events": [{
            "canonical_event_id": "cluster-1", "event_id": "raw-1",
            "category": "REGULATORY", "title": "Validated event",
            "candidate_event_score": 67.0,
        }]}}]
        clusters = DailyTradingAssistant._canonical_event_clusters(reviewed)
        self.assertEqual(clusters[0]["category"], "REGULATORY")
        self.assertEqual(clusters[0]["category"],
                         reviewed[0]["event_risk"]["matched_events"][0]["category"])

    def test_recommendation_journal_is_append_only_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = RecommendationJournal(directory)
            first = journal.append("run", {"symbol": "OIL", "final_action": "WATCHLIST"})
            journal.append("run", {"symbol": "SBIN", "final_action": "NO_TRADE"})
            rows = [json.loads(line) for line in first.read_text().splitlines()]
            self.assertEqual([row["symbol"] for row in rows], ["OIL", "SBIN"])

    def test_market_data_network_error_preserves_safe_application_error(self):
        platform = TradingPlatform(settings=PlatformSettings(market_data_source="cache"))
        platform.engine = Mock()
        platform.engine.analyze.side_effect = ConnectionError("offline")
        with self.assertRaises(DataUnavailableError):
            platform.analyze("SBIN")


if __name__ == "__main__":
    unittest.main()
