import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.application.settings import PlatformSettings
from src.application.platform import TradingPlatform
from src.application.errors import ValidationError
from src.screener.lightweight_screen import (
    LightweightDataError,
    SCORE_WEIGHTS,
    calculate_lightweight_support,
    run_lightweight_screen,
)


def fixture_frame(rows=240, latest_close=None):
    index = pd.date_range("2025-01-01", periods=rows, freq="D")
    closes = np.linspace(100, 150, rows)
    if latest_close is not None:
        closes[-1] = latest_close
    opens = closes - .4
    highs = np.maximum(opens, closes) + 1
    lows = np.minimum(opens, closes) - 1
    return pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows, "Close": closes,
        "Volume": np.full(rows, 2_000_000),
    }, index=index)


def live_from(frame, timestamp=None):
    row = frame.iloc[-1]
    result = {name: float(row[name]) for name in ("Open", "High", "Low", "Close", "Volume")}
    if timestamp is not None:
        result["timestamp"] = timestamp
    return result


class LightweightSupportTests(unittest.TestCase):
    def test_uses_configured_window_and_excludes_live_row(self):
        frame = fixture_frame(40)
        frame.loc[frame.index[-25], "Low"] = 1
        frame.loc[frame.index[-1], "Low"] = 2
        result = calculate_lightweight_support(frame, 150, 2, 20, 2)
        self.assertNotEqual(result["support"], 1)
        self.assertNotEqual(result["support"], 2)
        self.assertEqual(result["method"], "ROLLING_20_DAY_EXTREMES")

    def test_near_support_score(self):
        frame = fixture_frame(30)
        frame.loc[frame.index[-20:-1], "Low"] = 100
        frame.loc[frame.index[-20:-1], "High"] = 120
        result = calculate_lightweight_support(frame, 101, 2, 20, 2)
        self.assertEqual(result["score"], 90)

    def test_near_resistance_score(self):
        frame = fixture_frame(30)
        frame.loc[frame.index[:-1], "Low"] = 80
        frame.loc[frame.index[:-1], "High"] = 102
        result = calculate_lightweight_support(frame, 101, 2, 20, 2)
        self.assertEqual(result["score"], 20)

    def test_unavailable_levels_use_fallback_score(self):
        result = calculate_lightweight_support(fixture_frame(1), 100, 0, 20, 2)
        self.assertEqual(result["score"], 40)


class LightweightScreenContractTests(unittest.TestCase):
    def setUp(self):
        self.settings = PlatformSettings(market_data_source="cache")
        self.frame = fixture_frame()

    def test_stage_one_has_no_advanced_dependencies_or_outputs(self):
        with patch("src.analysis.price_action_engine.PriceActionEngine.analyze",
                   side_effect=AssertionError("advanced price action called")), \
             patch("src.market_structure.structure_detector.MarketStructureDetector.analyze",
                   side_effect=AssertionError("market structure called")), \
             patch("src.candlestick.triple_patterns.TripleCandlePatternDetector.detect_all",
                   side_effect=AssertionError("advanced candlestick called")):
            result = run_lightweight_screen(
                "TEST", self.frame, live_from(self.frame), self.settings)
        self.assertIsNone(result.context.price_action)
        self.assertIsNone(result.context.market_structure)
        self.assertIsNone(result.context.advanced_support_resistance)

    def test_zero_weight_advanced_score_and_external_enrichment_are_not_called(self):
        with patch("src.scoring.setup_score_engine.SetupScoreEngine.score",
                   side_effect=AssertionError("advanced score called")), \
             patch("src.news.analysis_service.NewsAnalysisService.analyze",
                   side_effect=AssertionError("news called")), \
             patch("src.event_risk.service.EventRiskService.assess",
                   side_effect=AssertionError("events called"), create=True), \
             patch("src.options.engine.option_engine.OptionEngine.analyze",
                   side_effect=AssertionError("options called")):
            result = run_lightweight_screen(
                "TEST", self.frame, live_from(self.frame), self.settings)
        self.assertEqual(result.context.price_action, None)

    def test_indicator_pipeline_runs_once(self):
        from src.indicators.pipeline import IndicatorPipeline
        original = IndicatorPipeline.run
        with patch("src.screener.lightweight_screen.IndicatorPipeline.run",
                   wraps=original) as run:
            run_lightweight_screen("TEST", self.frame, live_from(self.frame), self.settings)
        self.assertEqual(run.call_count, 1)

    def test_only_the_allowed_stage_one_indicators_are_published(self):
        result = run_lightweight_screen(
            "TEST", self.frame, live_from(self.frame), self.settings)
        self.assertEqual(set(result.context.indicators), {
            "EMA20", "EMA50", "EMA200", "RSI", "MACD", "MACD_SIGNAL",
            "MACD_HISTOGRAM", "ATR", "AVG_VOLUME", "RVOL",
        })

    def test_price_action_engine_is_not_called(self):
        with patch("src.analysis.price_action_engine.PriceActionEngine.analyze",
                   side_effect=AssertionError("PriceActionEngine called")):
            run_lightweight_screen("TEST", self.frame, live_from(self.frame), self.settings)

    def test_market_structure_engine_is_not_called(self):
        with patch("src.market_structure.structure_detector.MarketStructureDetector.analyze",
                   side_effect=AssertionError("MarketStructureDetector called")):
            run_lightweight_screen("TEST", self.frame, live_from(self.frame), self.settings)

    def test_advanced_candlestick_engine_is_not_called(self):
        with patch("src.candlestick.triple_patterns.TripleCandlePatternDetector.detect_all",
                   side_effect=AssertionError("Triple patterns called")):
            run_lightweight_screen("TEST", self.frame, live_from(self.frame), self.settings)

    def test_option_chain_is_not_called(self):
        with patch("src.options.engine.option_engine.OptionEngine.analyze",
                   side_effect=AssertionError("Option chain called")):
            run_lightweight_screen("TEST", self.frame, live_from(self.frame), self.settings)

    def test_news_and_event_services_are_not_called(self):
        with patch("src.news.analysis_service.NewsAnalysisService.analyze",
                   side_effect=AssertionError("News called")), \
             patch("src.event_risk.service.EventRiskService.assess_candidate",
                   side_effect=AssertionError("Event risk called")):
            run_lightweight_screen("TEST", self.frame, live_from(self.frame), self.settings)

    def test_ema_is_calculated_once(self):
        from src.indicators.ema import EMAIndicator
        with patch("src.indicators.pipeline.EMAIndicator.calculate",
                   wraps=EMAIndicator.calculate) as calculate:
            run_lightweight_screen("TEST", self.frame, live_from(self.frame), self.settings)
        self.assertEqual(calculate.call_count, 1)

    def test_atr_is_calculated_once(self):
        from src.indicators.atr import ATRIndicator
        with patch("src.indicators.pipeline.ATRIndicator.calculate",
                   wraps=ATRIndicator.calculate) as calculate:
            run_lightweight_screen("TEST", self.frame, live_from(self.frame), self.settings)
        self.assertEqual(calculate.call_count, 1)

    def test_average_volume_and_rvol_are_calculated_once(self):
        from src.indicators.volume import VolumeIndicator
        with patch("src.indicators.pipeline.VolumeIndicator.calculate",
                   wraps=VolumeIndicator.calculate) as calculate:
            result = run_lightweight_screen(
                "TEST", self.frame, live_from(self.frame), self.settings)
        self.assertEqual(calculate.call_count, 1)
        self.assertIn("AVG_VOLUME", result.context.indicators)
        self.assertIn("RVOL", result.context.indicators)

    def test_fixture_technical_score_is_stable(self):
        result = run_lightweight_screen(
            "TEST", self.frame, live_from(self.frame), self.settings)
        self.assertEqual(result.technical_score, 68)

    def test_support_signal_cannot_reach_advanced_support_engine(self):
        from src.indicators.pipeline import IndicatorPipeline
        from src.signals.support_signal import SupportSignal
        prepared = IndicatorPipeline.run(self.frame.copy())
        with patch("src.market_structure.support_resistance.SupportResistanceEngine.analyze",
                   side_effect=AssertionError("advanced support called")):
            signal = SupportSignal.generate(prepared)
        self.assertIn(signal.strength, {20, 40, 60, 90})

    def test_existing_score_weights_are_preserved(self):
        self.assertEqual(SCORE_WEIGHTS, {
            "trend": .30, "momentum": .20, "volume": .15,
            "volatility": .15, "support": .20,
        })
        result = run_lightweight_screen(
            "TEST", self.frame, live_from(self.frame), self.settings)
        expected = round(
            result.trend_score * .30 + result.momentum_score * .20
            + result.volume_score * .15 + result.volatility_score * .15
            + result.support_location_score * .20
        )
        self.assertEqual(result.technical_score, expected)

    def test_liquidity_and_trust_formulas_are_preserved(self):
        result = run_lightweight_screen(
            "TEST", self.frame, live_from(self.frame), self.settings)
        latest = result.context.historical_data.iloc[-1]
        turnover = float(latest["Close"]) * float(latest["AVG_VOLUME"]) / 10_000_000
        self.assertGreaterEqual(turnover, 20)
        self.assertEqual(result.liquidity_score, 80)
        self.assertEqual(result.trust_score, 85)

    def test_stage_timings_and_required_output_exist(self):
        result = run_lightweight_screen(
            "TEST", self.frame, live_from(self.frame), self.settings)
        self.assertIn("lightweight_indicator_seconds", result.timings)
        self.assertIn("lightweight_support_seconds", result.timings)
        self.assertIn("setup_decision_seconds", result.timings)
        self.assertIn("liquidity_trust_seconds", result.timings)
        self.assertNotIn("price_action", result.candidate["lightweight_screen"])

    def test_missing_live_candle_is_structured_failure(self):
        with self.assertRaises(LightweightDataError) as caught:
            run_lightweight_screen("TEST", self.frame, None, self.settings)
        self.assertEqual(caught.exception.code, "MISSING_LIVE_CANDLE")

    def test_stale_live_candle_is_structured_failure(self):
        stale = datetime.now(timezone.utc) - timedelta(hours=1)
        with self.assertRaises(LightweightDataError) as caught:
            run_lightweight_screen(
                "TEST", self.frame, live_from(self.frame, stale), self.settings)
        self.assertEqual(caught.exception.code, "STALE_LIVE_CANDLE")

    def test_invalid_ohlc_is_structured_failure(self):
        broken = self.frame.copy()
        broken.loc[broken.index[-1], "High"] = 1
        with self.assertRaises(LightweightDataError) as caught:
            run_lightweight_screen("TEST", broken, live_from(broken), self.settings)
        self.assertEqual(caught.exception.code, "INVALID_OHLC")

    def test_duplicate_timestamp_is_structured_failure(self):
        duplicate = pd.concat([self.frame, self.frame.iloc[[-1]]])
        with self.assertRaises(LightweightDataError) as caught:
            run_lightweight_screen(
                "TEST", duplicate, live_from(self.frame), self.settings)
        self.assertEqual(caught.exception.code, "DUPLICATE_TIMESTAMP")

    def test_live_row_is_not_used_as_completed_support(self):
        frame = self.frame.copy()
        frame.loc[frame.index[-1], "Low"] = 1
        result = run_lightweight_screen("TEST", frame, live_from(frame), self.settings)
        self.assertNotEqual(result.lightweight_support["support"], 1)
        self.assertEqual(result.context.latest_completed_candle_time, frame.index[-2])


class RankingCompatibilityTests(unittest.TestCase):
    def test_existing_lexicographic_order(self):
        candidates = [
            {"symbol": "WATCH_HIGH", "action": "WATCH", "technical_score": 99,
             "stock_liquidity": {"score": 100}, "risk_reward": 5},
            {"symbol": "BUY_LOW", "action": "BUY", "technical_score": 55,
             "stock_liquidity": {"score": 50}, "risk_reward": 1},
            {"symbol": "BUY_HIGH", "action": "BUY", "technical_score": 80,
             "stock_liquidity": {"score": 70}, "risk_reward": 2},
        ]
        rank = {"BUY": 2, "BUY ON DIP": 1, "WATCH": 0}
        candidates.sort(key=lambda item: (
            rank[item["action"]], item["technical_score"],
            item["stock_liquidity"]["score"], item["risk_reward"],
        ), reverse=True)
        self.assertEqual(
            [item["symbol"] for item in candidates],
            ["BUY_HIGH", "BUY_LOW", "WATCH_HIGH"],
        )

    def test_every_buy_ranks_before_every_watch(self):
        action_rank = {"BUY": 2, "BUY ON DIP": 1, "WATCH": 0}
        rows = [
            {"action": "WATCH", "technical_score": 100,
             "stock_liquidity": {"score": 100}, "risk_reward": 10},
            {"action": "BUY", "technical_score": 1,
             "stock_liquidity": {"score": 1}, "risk_reward": 0},
        ]
        rows.sort(key=lambda item: (
            action_rank[item["action"]], item["technical_score"],
            item["stock_liquidity"]["score"], item["risk_reward"],
        ), reverse=True)
        self.assertEqual([item["action"] for item in rows], ["BUY", "WATCH"])

    def test_deterministic_shortlist_is_unchanged(self):
        rows = [
            ("A", "BUY", 80, 70, 2), ("B", "WATCH", 99, 100, 5),
            ("C", "BUY ON DIP", 90, 90, 3), ("D", "BUY", 75, 80, 4),
        ]
        expected = ["A", "D", "C"]
        rank = {"BUY": 2, "BUY ON DIP": 1, "WATCH": 0}
        actual = sorted(rows, key=lambda row: (
            rank[row[1]], row[2], row[3], row[4]), reverse=True)[:3]
        self.assertEqual([row[0] for row in actual], expected)


class AdvancedReuseTests(unittest.TestCase):
    def test_advanced_analysis_reuses_stage_one_indicator_frame(self):
        from src.indicators.pipeline import IndicatorPipeline
        from src.trading_engine.engine import TradingEngine
        prepared = IndicatorPipeline.run(fixture_frame())
        with patch("src.trading_engine.engine.IndicatorPipeline.run",
                   side_effect=AssertionError("Indicators recalculated")):
            report = TradingEngine(settings=PlatformSettings(
                market_data_source="cache"
            )).analyze_advanced("TEST", prepared)
        self.assertIn("price_action", report)


class ShortlistBoundaryTests(unittest.TestCase):
    class Provider:
        def __init__(self, frame):
            self.frame = frame

        def get_data(self, symbol):
            return self.frame.copy()

    @staticmethod
    def candidate(symbol, score):
        return {
            "symbol": symbol, "action": "BUY", "confidence": 90,
            "technical_score": score, "recommendation": "BUY",
            "current_price": 100, "stock_liquidity": {"score": 100},
            "trust": {"score": 100}, "historical_gap_factor": 1,
            "risk_reward": 2, "candlestick": {}, "setup_evaluation": {},
            "reason": "fixture", "trade_plan": {"risk_reward": 2},
            "entry_report": {}, "analysis_report": {}, "position_size": {},
        }

    def platform(self):
        platform = TradingPlatform.__new__(TradingPlatform)
        platform.settings = PlatformSettings(
            market_data_source="cache", ranking_shortlist_size=2,
            advanced_analysis_max_candidates=2,
        )
        platform.provider = self.Provider(fixture_frame())
        platform._universe_symbols = lambda: ["A", "B", "C"]
        return platform

    def test_advanced_calls_start_after_all_lightweight_calls_and_slice(self):
        platform = self.platform()
        events = []

        def screen(symbol, history, live, settings):
            events.append(("lightweight", symbol))
            score = {"A": 70, "B": 90, "C": 80}[symbol]
            return SimpleNamespace(candidate=self.candidate(symbol, score), timings={
                "lightweight_indicator_seconds": 0,
                "lightweight_support_seconds": 0,
                "setup_decision_seconds": 0,
                "liquidity_trust_seconds": 0,
            })

        def advanced(candidate):
            events.append(("advanced", candidate["symbol"]))
            return candidate

        platform.enrich_candidate = advanced
        with patch("src.application.platform.run_lightweight_screen", side_effect=screen):
            result = platform._suggest_stocks(limit=2, minimum_score=40, enrich=True)
        first_advanced = next(i for i, item in enumerate(events) if item[0] == "advanced")
        self.assertTrue(all(item[0] == "lightweight" for item in events[:first_advanced]))
        self.assertEqual(len([item for item in events if item[0] == "advanced"]), 2)
        self.assertEqual([item["symbol"] for item in result["suggestions"]], ["B", "C"])

    def test_one_symbol_failure_does_not_abort_universe(self):
        platform = self.platform()

        def screen(symbol, history, live, settings):
            if symbol == "B":
                raise LightweightDataError("INVALID_OHLC", "fixture failure")
            return SimpleNamespace(candidate=self.candidate(symbol, 70), timings={
                "lightweight_indicator_seconds": 0,
                "lightweight_support_seconds": 0,
                "setup_decision_seconds": 0,
                "liquidity_trust_seconds": 0,
            })

        with patch("src.application.platform.run_lightweight_screen", side_effect=screen):
            result = platform._suggest_stocks(limit=2, minimum_score=40, enrich=False)
        self.assertEqual(result["statistics"]["analysis_failed"], 1)
        self.assertEqual(len(result["suggestions"]), 2)

    def test_funnel_counters_are_correct(self):
        platform = self.platform()

        def screen(symbol, history, live, settings):
            candidate = self.candidate(symbol, {"A": 30, "B": 70, "C": 80}[symbol])
            if symbol == "C":
                candidate["stock_liquidity"]["score"] = 10
            return SimpleNamespace(candidate=candidate, timings={
                "lightweight_indicator_seconds": 0,
                "lightweight_support_seconds": 0,
                "setup_decision_seconds": 0,
                "liquidity_trust_seconds": 0,
            })

        with patch("src.application.platform.run_lightweight_screen", side_effect=screen):
            result = platform._suggest_stocks(limit=2, minimum_score=40, enrich=False)
        self.assertEqual(result["statistics"]["lightweight_succeeded"], 3)
        self.assertEqual(result["statistics"]["technical_passed"], 2)
        self.assertEqual(result["statistics"]["liquidity_passed"], 1)
        self.assertEqual(result["statistics"]["trust_passed"], 1)
        self.assertEqual(result["statistics"]["shortlisted"], 1)
        self.assertEqual(result["rejection_counts"]["LOW_TECHNICAL_SCORE"], 1)
        self.assertEqual(result["rejection_counts"]["LOW_LIQUIDITY"], 1)

    def test_advanced_candidate_guard_reports_received_and_maximum(self):
        platform = self.platform()
        platform.settings = PlatformSettings(
            market_data_source="cache", ranking_shortlist_size=1,
            advanced_analysis_max_candidates=1,
        )

        def screen(symbol, history, live, settings):
            return SimpleNamespace(candidate=self.candidate(symbol, 70), timings={
                "lightweight_indicator_seconds": 0,
                "lightweight_support_seconds": 0,
                "setup_decision_seconds": 0,
                "liquidity_trust_seconds": 0,
            })

        with patch("src.application.platform.run_lightweight_screen", side_effect=screen):
            with self.assertRaisesRegex(
                ValidationError, "received 2 candidates; configured maximum is 1"):
                platform._suggest_stocks(limit=2, minimum_score=40, enrich=True)


if __name__ == "__main__":
    unittest.main()
