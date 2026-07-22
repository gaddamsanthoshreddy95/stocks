import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.data_provider.kite_data_provider import KiteDataProvider


class FakeProvider:
    def __init__(self):
        self.calls = []

    def get_historical_data(self, symbol, period="1y", interval="day", from_date=None):
        self.calls.append((symbol, period, interval))
        return pd.DataFrame(
            {"Open": [100], "High": [102], "Low": [99], "Close": [101], "Volume": [1000]},
            index=pd.DatetimeIndex(["2026-07-21 09:15:00"], name="Date"),
        )


class IntradayHistoryCacheTests(unittest.TestCase):
    def test_six_month_15_minute_history_is_downloaded_once_and_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider()
            data = KiteDataProvider(
                provider, intraday_cache_directory=Path(directory),
                history_cache_directory=Path(directory) / "daily",
            )
            first = data.get_intraday_history("NESTLEIND")
            second = data.get_intraday_history("NESTLEIND")
            self.assertEqual(provider.calls, [("NESTLEIND", "6mo", "15minute")])
            pd.testing.assert_frame_equal(first, second)
            self.assertTrue((Path(directory) / "NESTLEIND_6mo_15minute.parquet").exists())


if __name__ == "__main__":
    unittest.main()
