import unittest

import pandas as pd

from src.application.settings import PlatformSettings
from src.market_structure.intraday_recovery import IntradayRecoveryEngine
from src.market_structure.supply_demand import SupplyDemandEngine
from src.workflow.daily_trading_assistant import DailyTradingAssistant


def candles(rows, frequency="D"):
    return pd.DataFrame(
        rows,
        index=pd.date_range("2026-01-01", periods=len(rows), freq=frequency),
        columns=["Open", "High", "Low", "Close", "Volume"],
    )


class SupplyDemandRecoveryTests(unittest.TestCase):
    def test_supply_and_demand_are_ranges_with_quality_metadata(self):
        frame = candles([
            (100, 102, 99, 101, 1000), (101, 103, 100, 102, 1000),
            (102, 103, 95, 97, 1800), (97, 104, 96, 103, 2600),
            (103, 108, 102, 107, 2400), (107, 110, 106, 108, 1700),
            (108, 112, 107, 111, 1600), (111, 113, 108, 109, 1500),
            (109, 110, 104, 105, 1900), (105, 107, 103, 106, 1400),
            (106, 109, 105, 108, 1500), (108, 110, 107, 109, 1600),
        ])
        result = SupplyDemandEngine.analyze(frame)
        self.assertTrue(result["available"])
        self.assertTrue(result["demand_zones"])
        zone = result["demand_zones"][0]
        self.assertLess(zone["lower"], zone["upper"])
        self.assertIn("retests", zone)
        self.assertIn("departure_atr", zone)
        self.assertIn("score", zone)

    def test_normal_day_does_not_require_recovery_confirmation(self):
        daily = candles([
            (100, 102, 99, 101, 1000),
            (101, 103, 100, 102, 1100),
        ])
        result = IntradayRecoveryEngine.analyze(
            daily, None, {"nearest_demand": None, "nearest_supply": None}
        )
        self.assertFalse(result["required"])
        self.assertEqual(result["state"], "NOT_REQUIRED")

    def test_sharp_fall_without_intraday_data_is_not_confirmed(self):
        daily = candles([
            (100, 102, 99, 101, 1000),
            (101, 101, 93, 94, 3000),
        ])
        result = IntradayRecoveryEngine.analyze(
            daily, None, {"nearest_demand": None, "nearest_supply": None}
        )
        self.assertTrue(result["required"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["state"], "DATA_UNAVAILABLE")

    def test_demand_hold_structure_break_and_volume_confirm_reversal(self):
        daily = candles([
            (100, 102, 99, 101, 1000),
            (101, 101, 93, 94, 3000),
        ])
        daily["ATR"] = 4.0
        intraday = candles([
            (100, 101, 96, 97, 3000),
            (97, 98, 93, 94, 3000),
            (94, 97, 94, 96, 1800),
            (96, 97, 95, 96.5, 2000),
            (96.5, 98, 95.5, 97.5, 2600),
            (97.5, 99, 96.5, 98.5, 3200),
            (98.5, 100, 98, 99.5, 4200),
            (99.5, 102, 99, 101.5, 6000),
        ], frequency="15min")
        result = IntradayRecoveryEngine.analyze(
            daily, intraday,
            {
                "nearest_demand": {"lower": 93, "upper": 96, "score": 80},
                "nearest_supply": {"lower": 116, "upper": 120, "score": 75},
            },
        )
        self.assertEqual(result["state"], "REVERSAL_CONFIRMED")
        self.assertTrue(result["confirmed"])
        self.assertTrue(result["checks"]["higher_low"])
        self.assertTrue(result["checks"]["previous_swing_high_broken"])
        self.assertGreaterEqual(result["recalculated_trade"]["risk_reward"], 1.5)

    def test_sharp_fall_filter_accepts_only_confirmed_recovery(self):
        settings = PlatformSettings(market_data_source="cache")
        common = {
            "plan": {"entry": 100, "stop_loss": 98, "risk_reward": 2},
            "setup": "PULLBACK",
            "technical": {"relative_volume": 1.3},
            "entry_quality": {"position_size_guidance": "NORMAL", "extension_band": "NORMAL"},
            "adverse": {
                "available": True, "sample_count": 50,
                "probability_target_before_adverse_barrier": 70,
                "probability_no_overnight_gap_beyond_barrier": 99,
            },
            "relative_strength": {"available": True, "score": 70},
            "sector": {"available": True, "score": 65},
            "liquidity": {"score": 85},
            "settings": settings,
        }
        waiting = DailyTradingAssistant._bullish_stock_selection_filters(
            **common,
            intraday_recovery={
                "required": True, "shock_detected": True, "confirmed": False,
                "state": "RECOVERY_BUILDING", "reason": "Swing high is not broken.",
            },
        )
        confirmed = DailyTradingAssistant._bullish_stock_selection_filters(
            **common,
            intraday_recovery={
                "required": True, "shock_detected": True, "confirmed": True,
                "state": "REVERSAL_CONFIRMED", "reason": "Recovery confirmed.",
            },
        )
        self.assertIn("sharp_fall_recovery_confirmation", waiting["failed_checks"])
        self.assertNotIn("sharp_fall_recovery_confirmation", confirmed["failed_checks"])


if __name__ == "__main__":
    unittest.main()
