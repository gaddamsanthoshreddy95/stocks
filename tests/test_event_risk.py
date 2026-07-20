from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
import pandas as pd

from src.application.settings import PlatformSettings
from src.event_risk.models import (
    EventCategory, EventDirection, EventImpactDuration, EventRiskItem,
    EventSeverity, EventSourceType, EventStatus,
)
from src.event_risk.repository import EventRepository
from src.event_risk.service import DailyEventContext, EventRiskService


NOW = datetime(2026, 7, 20, 8, tzinfo=timezone.utc)


def settings(**overrides):
    return PlatformSettings(market_data_source="cache", event_allow_test_overrides=True, **overrides)


def service(**overrides):
    return EventRiskService(settings(**overrides))


def event(event_type="IRAN_CONFLICT_ESCALATION", category=EventCategory.GEOPOLITICAL,
          severity=EventSeverity.HIGH, direction=EventDirection.VOLATILITY_ONLY,
          raw=75, gap=75, sectors=None, symbols=None, updated=NOW,
          scheduled=False, start=None, confirmed=True, status=EventStatus.ACTIVE):
    return EventRiskItem(
        event_id=event_type + "-1", title=event_type.replace("_", " ").title(),
        normalized_event_type=event_type, category=category, severity=severity,
        direction=direction, status=status, source_type=EventSourceType.MANUAL_OVERRIDE,
        detected_at=updated, last_updated_at=updated, event_start=start or updated,
        expected_duration=EventImpactDuration.ONE_TO_FOUR_WEEKS,
        raw_score=raw, decayed_score=raw, confidence=.9, materiality=1,
        freshness_score=100, gap_risk=gap, affected_sectors=sectors or [],
        affected_symbols=symbols or [], is_scheduled=scheduled,
        is_confirmed=confirmed, half_life_hours=72,
    )


def context(*events, state="FRESH"):
    return DailyEventContext(list(events), state, [], {})


class EventRiskTests(unittest.TestCase):
    def crude_context(self):
        engine = service()
        crude = engine._commodity_events({"crude": {
            "change_percent": 6, "three_day_change_pct": 7, "z_score": 2.4,
        }}, NOW)[0]
        geopolitical = event(sectors=["ENERGY", "AVIATION"])
        return engine, context(crude, geopolitical)

    def test_oil_positive_direction_but_high_risk(self):
        engine, daily = self.crude_context()
        candidate = {"symbol": "OIL", "sector": "ENERGY", "technical_score": 92}
        assessment = engine.assess_candidate(candidate, daily, base_readiness=88, as_of=NOW)
        self.assertEqual(candidate["technical_score"], 92)
        self.assertEqual(assessment.company_type, "UPSTREAM_PRODUCER")
        self.assertEqual(assessment.event_direction, "POSITIVE_BUT_VOLATILE")
        self.assertIn(assessment.event_risk_level, {"HIGH", "EXTREME"})
        self.assertLess(assessment.adjusted_readiness, assessment.base_readiness)
        self.assertEqual(assessment.position_size_multiplier, .5)
        self.assertFalse(assessment.overnight_hold_allowed)
        self.assertIn("DEFINED_RISK_ONLY", assessment.strategy_restrictions)

    def test_hpcl_and_airline_are_hurt_more_than_upstream(self):
        engine, daily = self.crude_context()
        oil = engine.assess_candidate({"symbol": "OIL", "sector": "ENERGY"}, daily, base_readiness=88, as_of=NOW)
        hpcl = engine.assess_candidate({"symbol": "HPCL", "sector": "ENERGY"}, daily, base_readiness=88, as_of=NOW)
        airline = engine.assess_candidate({"symbol": "INDIGO", "sector": "AVIATION"}, daily, base_readiness=88, as_of=NOW)
        self.assertEqual(hpcl.event_direction, "NEGATIVE_AND_VOLATILE")
        self.assertEqual(airline.event_direction, "NEGATIVE_AND_VOLATILE")
        self.assertLess(hpcl.adjusted_readiness, oil.adjusted_readiness)
        self.assertLess(airline.adjusted_readiness, oil.adjusted_readiness)

    def test_earnings_proximity_and_hard_block(self):
        engine = service()
        today = event("EARNINGS", EventCategory.EARNINGS, EventSeverity.EXTREME,
                      raw=95, gap=95, symbols=["SBIN"], scheduled=True, start=NOW + timedelta(hours=2))
        tomorrow = event("EARNINGS", EventCategory.EARNINGS, EventSeverity.HIGH,
                         raw=75, gap=80, symbols=["SBIN"], scheduled=True, start=NOW + timedelta(hours=24))
        later = event("EARNINGS", EventCategory.EARNINGS, EventSeverity.HIGH,
                      raw=75, gap=80, symbols=["SBIN"], scheduled=True, start=NOW + timedelta(days=8))
        same_day = engine.assess_candidate({"symbol": "SBIN", "sector": "BANKING"}, context(today), base_readiness=90, as_of=NOW)
        next_day = engine.assess_candidate({"symbol": "SBIN", "sector": "BANKING"}, context(tomorrow), base_readiness=90, as_of=NOW)
        distant = engine.assess_candidate({"symbol": "SBIN", "sector": "BANKING"}, context(later), base_readiness=90, as_of=NOW)
        self.assertTrue(same_day.hard_block)
        self.assertFalse(same_day.overnight_hold_allowed)
        self.assertFalse(next_day.hard_block)
        self.assertLess(next_day.position_size_multiplier, 1)
        self.assertLess(distant.event_risk_score, next_day.event_risk_score)

    def test_decay_deescalation_and_expiry(self):
        engine = service()
        active = event(updated=NOW - timedelta(hours=24))
        deescalating = event(updated=NOW - timedelta(hours=24), status=EventStatus.DE_ESCALATING)
        resolved = event(updated=NOW - timedelta(hours=24), status=EventStatus.RESOLVED)
        self.assertGreater(engine.decay(active, NOW)[0], engine.decay(deescalating, NOW)[0])
        self.assertGreater(engine.decay(deescalating, NOW)[0], engine.decay(resolved, NOW)[0])
        active.expiry_time = NOW - timedelta(minutes=1)
        self.assertEqual(engine.decay(active, NOW)[0], 0)

    def test_escalation_refresh_increases_effective_score(self):
        engine = service()
        old = event(updated=NOW - timedelta(hours=24))
        escalation = event(updated=NOW, status=EventStatus.ESCALATING)
        self.assertGreater(engine.decay(escalation, NOW)[0], engine.decay(old, NOW)[0])

    def test_duplicate_events_cluster_without_double_counting(self):
        engine = service()
        first, second = event(), event()
        second.event_id = "second-source"
        second.source_type = EventSourceType.NEWS
        clusters = engine.deduplicate([first, second])
        self.assertEqual(len(clusters), 1)
        self.assertTrue(clusters[0].is_confirmed)
        self.assertEqual(len(clusters[0].related_article_ids), 2)

    def test_multisource_confirmation_is_more_confident(self):
        engine = service()
        single = engine.deduplicate([event(confirmed=False)])[0]
        first, second = event(confirmed=False), event(confirmed=False)
        second.event_id, second.source_type = "news-2", EventSourceType.NEWS
        multi = engine.deduplicate([first, second])[0]
        self.assertGreater(multi.confidence, single.confidence)

    def test_unavailable_and_stale_states_are_explicit(self):
        engine = service()
        unavailable = engine.assess_candidate({"symbol": "SBIN", "sector": "BANKING"},
                                              context(state="UNAVAILABLE"), base_readiness=80, as_of=NOW)
        stale_event = event()
        stale_event.freshness_score = 25
        stale = engine.assess_candidate({"symbol": "SBIN", "sector": "BANKING"},
                                        context(stale_event), base_readiness=80, as_of=NOW)
        self.assertEqual(unavailable.data_state, "UNAVAILABLE")
        self.assertEqual(unavailable.event_data_uncertainty_penalty, 5)
        self.assertEqual(unavailable.readiness_penalty, 0)
        self.assertEqual(stale.data_state, "STALE")
        self.assertEqual(stale.event_data_uncertainty_penalty,
                         engine.settings.event_data_partial_coverage_penalty)

    def test_zscore_can_trigger_commodity_event_below_absolute_threshold(self):
        engine = service()
        events = engine._commodity_events({"crude": {
            "change_percent": 1.5, "z_score": 2.4, "three_day_change_pct": 2,
        }}, NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].category, EventCategory.COMMODITY)

    def test_sector_sensitivity_differs_for_rbi_event(self):
        engine = service()
        rbi = event("RBI_POLICY", EventCategory.CENTRAL_BANK, EventSeverity.HIGH,
                    raw=75, gap=60, sectors=[])
        bank = engine.assess_candidate({"symbol": "SBIN", "sector": "BANKING"}, context(rbi), base_readiness=85, as_of=NOW)
        pharma = engine.assess_candidate({"symbol": "CIPLA", "sector": "PHARMA"}, context(rbi), base_readiness=85, as_of=NOW)
        self.assertGreater(bank.event_risk_score, pharma.event_risk_score)

    def test_extreme_event_requires_defined_risk_and_blocks_short_premium(self):
        engine = service(event_risk_hard_block_score=80)
        extreme = event(severity=EventSeverity.EXTREME, raw=100, gap=95, symbols=["OIL"])
        result = engine.assess_candidate({"symbol": "OIL", "sector": "ENERGY"}, context(extreme), base_readiness=95, as_of=NOW)
        self.assertTrue(result.hard_block)
        self.assertIn("BLOCK_SHORT_PREMIUM", result.strategy_restrictions)
        self.assertIn("DEFINED_RISK_ONLY", result.strategy_restrictions)

    def test_manual_override_and_atomic_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = EventRepository(directory)
            repository.root.mkdir(parents=True, exist_ok=True)
            repository.override_path.write_text(json.dumps({"events": [{
                "enabled": True, "event_type": "IRAN_CONFLICT_ESCALATION",
                "severity": "HIGH", "affected_sectors": ["ENERGY"],
                "start_time": NOW.isoformat(), "expiry_time": (NOW + timedelta(days=2)).isoformat(),
            }]}))
            engine = EventRiskService(settings(), repository=repository)
            daily = engine.build_daily_context(as_of=NOW)
            result = engine.assess_candidate({"symbol": "OIL", "sector": "ENERGY"}, daily, base_readiness=85, as_of=NOW)
            self.assertTrue(result.manual_override_applied)
            repository.write_events([{"event_id": "one"}], NOW)
            self.assertEqual(repository.read_events()[0]["event_id"], "one")

    def test_disabled_engine_is_noop(self):
        engine = service(event_risk_enabled=False)
        daily = engine.build_daily_context(as_of=NOW)
        result = engine.assess_candidate({"symbol": "OIL", "sector": "ENERGY"}, daily, base_readiness=88, as_of=NOW)
        self.assertEqual(daily.data_state, "DISABLED")
        self.assertEqual(result.adjusted_readiness, 88)
        self.assertEqual(result.position_size_multiplier, 1)

    def test_shared_commodity_source_is_fetched_once(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            engine = EventRiskService(settings(), repository=EventRepository(directory),
                                      commodity_fetcher=lambda: calls.append(1) or {
                                          "crude": {"change_percent": 6}
                                      })
            daily = engine.build_daily_context(as_of=NOW)
            for symbol in ("OIL", "HPCL", "INDIGO"):
                engine.assess_candidate({"symbol": symbol, "sector": "ENERGY"}, daily,
                                        base_readiness=80, as_of=NOW)
            self.assertEqual(len(calls), 1)

    def test_shared_context_accepts_market_snapshot_vix_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = EventRiskService(settings(), repository=EventRepository(directory))
            daily = engine.build_daily_context(
                {"regime": "NEUTRAL", "vix": {"price": 26.5, "change_percent": 4.2}},
                as_of=NOW,
            )
            market_events = [item for item in daily.events if item.category == EventCategory.MARKET_WIDE]
            self.assertEqual(len(market_events), 1)
            self.assertIn("VIX 26.5", market_events[0].reasons[0])

    def test_invalid_market_snapshot_vix_does_not_break_shared_context(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = EventRiskService(settings(), repository=EventRepository(directory))
            daily = engine.build_daily_context(
                {"regime": "HIGH_VOLATILITY", "vix": {"price": None}},
                as_of=NOW,
            )
            market_events = [item for item in daily.events if item.category == EventCategory.MARKET_WIDE]
            self.assertEqual(len(market_events), 1)
            self.assertIn("VIX 0.0", market_events[0].reasons[0])

    def test_every_mapped_sector_has_an_explicit_event_profile(self):
        engine = service()
        mapped = set(pd.read_csv("resources/sector_mapping.csv")["Sector"].str.upper())
        configured = set(engine.mapping["sector_sensitivity"])
        self.assertEqual(mapped - configured, set())

    def test_every_cached_universe_symbol_has_an_explicit_sector(self):
        cached = {path.stem for path in Path(".cache/kite_history").glob("*.parquet")}
        mapped = set(pd.read_csv("resources/sector_mapping.csv")["Symbol"].str.upper())
        if cached:
            self.assertEqual(cached - mapped, set())

    def test_all_required_commodities_generate_statistical_shocks(self):
        engine = service()
        snapshot = {
            name: {"change_percent": 1, "z_score": 2.5}
            for name in engine.mapping["commodity_event_types"]
        }
        events = engine._commodity_events(snapshot, NOW)
        event_types = {item.normalized_event_type.removesuffix("_UP") for item in events}
        self.assertEqual(event_types, set(engine.mapping["commodity_event_types"].values()))

    def test_sector_direction_changes_same_commodity_event(self):
        engine = service()
        steel = engine._commodity_events({"steel": {"change_percent": 6}}, NOW)[0]
        metal = engine.assess_candidate({"symbol": "JSWSTEEL", "sector": "METAL"},
                                       context(steel), base_readiness=85, as_of=NOW)
        auto = engine.assess_candidate({"symbol": "MARUTI", "sector": "AUTO"},
                                      context(steel), base_readiness=85, as_of=NOW)
        self.assertEqual(metal.event_direction, "POSITIVE")
        self.assertEqual(auto.event_direction, "NEGATIVE")
        self.assertLess(auto.adjusted_readiness, metal.adjusted_readiness)

    def test_company_type_overrides_sector_for_power_business_models(self):
        engine = service()
        coal = engine._commodity_events({"coal": {"change_percent": 6}}, NOW)[0]
        renewable = engine.assess_candidate({"symbol": "ADANIGREEN", "sector": "POWER"},
                                           context(coal), base_readiness=85, as_of=NOW)
        thermal = engine.assess_candidate({"symbol": "ADANIPOWER", "sector": "POWER"},
                                         context(coal), base_readiness=85, as_of=NOW)
        self.assertEqual(renewable.event_direction, "MIXED")
        self.assertEqual(thermal.event_direction, "NEGATIVE_AND_VOLATILE")
        self.assertLess(thermal.adjusted_readiness, renewable.adjusted_readiness)


if __name__ == "__main__":
    unittest.main()
