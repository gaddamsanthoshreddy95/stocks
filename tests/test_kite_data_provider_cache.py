import pandas as pd

from src.data_provider.kite_data_provider import KiteDataProvider


class FakeKiteProvider:
    def __init__(self):
        self.calls = []
        self.live_calls = []
        self.bulk_live_calls = []

    def get_historical_data(self, symbol, period="1y", from_date=None):
        self.calls.append((symbol, period) if from_date is None else (symbol, period, from_date))
        return pd.DataFrame(
            {"Open": [100.0], "High": [102.0], "Low": [99.0],
             "Close": [101.0], "Volume": [1000]},
            index=pd.DatetimeIndex(["2026-07-17"], name="Date"),
        )

    def get_live_candle(self, symbol):
        self.live_calls.append(symbol)
        return {"Open": 102.0, "High": 105.0, "Low": 101.0,
                "Close": 104.0, "Volume": 2000}

    def get_live_candles(self, symbols):
        self.bulk_live_calls.append(symbols)
        return {symbol: {"Open": 102.0, "High": 105.0, "Low": 101.0,
                         "Close": 104.0, "Volume": 2000} for symbol in symbols}


def test_long_history_is_reused_from_disk_after_restart(tmp_path):
    provider = FakeKiteProvider()
    first = KiteDataProvider(provider, long_history_cache_directory=tmp_path)

    downloaded = first.get_long_history("RELIANCE", period="10y")

    restarted = KiteDataProvider(provider, long_history_cache_directory=tmp_path)
    cached = restarted.get_long_history("RELIANCE", period="10y")

    assert provider.calls == [("RELIANCE", "10y")]
    pd.testing.assert_frame_equal(cached, downloaded)


def test_long_history_fetches_each_new_symbol_once(tmp_path):
    provider = FakeKiteProvider()
    data = KiteDataProvider(provider, long_history_cache_directory=tmp_path)

    data.get_long_history("INFY", period="10y")
    data.get_long_history("INFY.NS", period="10y")
    data.get_long_history("TCS", period="10y")

    assert provider.calls == [("INFY", "10y"), ("TCS", "10y")]


def test_daily_history_is_reused_from_disk_on_same_day(tmp_path):
    provider = FakeKiteProvider()
    first = KiteDataProvider(provider, history_cache_directory=tmp_path)
    downloaded = first.get_data("RELIANCE")

    restarted = KiteDataProvider(provider, history_cache_directory=tmp_path)
    cached = restarted.get_data("RELIANCE")

    assert provider.calls == [("RELIANCE", "1y")]
    pd.testing.assert_frame_equal(cached, downloaded)


def test_live_refresh_overlays_quote_without_redownloading_history(tmp_path):
    provider = FakeKiteProvider()
    data = KiteDataProvider(provider, history_cache_directory=tmp_path)

    data.get_data("RELIANCE")
    data.begin_live_refresh(["RELIANCE"])
    refreshed = data.get_data("RELIANCE")
    data.end_live_refresh()

    assert provider.calls == [("RELIANCE", "1y")]
    assert provider.bulk_live_calls == [["RELIANCE"]]
    assert provider.live_calls == []
    assert refreshed.iloc[-1]["Close"] == 104.0
