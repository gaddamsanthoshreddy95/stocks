import os
import unittest
from unittest.mock import patch

from src.application.settings import PlatformSettings


class PlatformSettingsEnvironmentTests(unittest.TestCase):
    def test_strategy_mode_ignores_windows_quotes_and_whitespace(self):
        for value in ('"both"', " 'SHORT_PUT' ", " EQUITY "):
            with self.subTest(value=value), patch.dict(
                    os.environ, {"TRADING_STRATEGY_MODE": value}, clear=False):
                settings = PlatformSettings.from_environment()
                self.assertEqual(settings.trading_strategy_mode,
                                 value.strip().strip("'\"").strip().lower())

    def test_invalid_strategy_mode_error_includes_received_value(self):
        with patch.dict(os.environ, {"TRADING_STRATEGY_MODE": "options"}, clear=False):
            with self.assertRaisesRegex(ValueError, "received 'options'"):
                PlatformSettings.from_environment()


if __name__ == "__main__":
    unittest.main()
