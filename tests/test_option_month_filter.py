"""Regression tests for explicit option expiry-month selection."""

from datetime import date
import unittest
from unittest.mock import Mock, patch

from src.application.errors import ValidationError
from src.application.platform import TradingPlatform
from src.application.settings import PlatformSettings
from src.options.kite_option_chain import KiteOptionChainProvider, OptionInstrumentLookupError


def _instruments():
    today = date.today()
    first_year = today.year + (today.month == 12)
    first_month = 1 if today.month == 12 else today.month + 1
    second_year = first_year + (first_month == 12)
    second_month = 1 if first_month == 12 else first_month + 1
    first = date(first_year, first_month, 20)
    second = date(second_year, second_month, 20)
    rows = []
    for expiry, tag in ((first, "FIRST"), (second, "SECOND")):
        for option_type in ("CE", "PE"):
            rows.append({
                "name": "TEST", "instrument_type": option_type, "expiry": expiry,
                "strike": 100.0, "tradingsymbol": f"TEST{tag}{option_type}", "lot_size": 1,
            })
    return rows, first, second


class OptionMonthFilterTests(unittest.TestCase):
    def test_get_chain_uses_only_requested_expiry_month(self):
        instruments, first, second = _instruments()
        kite = Mock()
        kite.quote.side_effect = lambda symbols: {
            symbol: {"last_price": 5, "oi": 10, "volume": 20, "depth": {}}
            for symbol in symbols
        }
        provider = KiteOptionChainProvider(kite, instruments=instruments)
        provider._previous_oi = Mock(return_value={})
        provider._save_oi = Mock()

        automatic = provider.get_chain("TEST", 100)
        requested = provider.get_chain("TEST", 100, expiry_month=second.strftime("%Y-%m"))

        self.assertEqual(automatic.expiry, str(first))
        self.assertEqual(requested.expiry, str(second))
        self.assertTrue(all("SECOND" in item.symbol for item in requested.calls + requested.puts))

    def test_missing_requested_month_does_not_fall_back(self):
        instruments, _, _ = _instruments()
        provider = KiteOptionChainProvider(Mock(), instruments=instruments)
        with self.assertRaises(OptionInstrumentLookupError):
            provider.get_chain("TEST", 100, expiry_month="2099-12")

    @patch("src.application.platform.DailyTradingAssistant")
    def test_platform_validates_and_propagates_month(self, assistant):
        assistant.return_value.generate.return_value = {"report_type": "daily_trading_assistant"}
        platform = TradingPlatform(settings=PlatformSettings(market_data_source="cache"))

        platform.daily_report(option_month="2026-08")
        assistant.assert_called_once_with(platform, option_month="2026-08")
        with self.assertRaises(ValidationError):
            platform.daily_report(option_month="August-2026")


if __name__ == "__main__":
    unittest.main()
