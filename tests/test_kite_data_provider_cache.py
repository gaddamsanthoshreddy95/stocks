import pandas as pd

from src.data_provider.kite_data_provider import KiteDataProvider
from src.providers.kite_provider import KiteProvider


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


def test_missing_bulk_quote_keeps_cached_history(tmp_path):
    provider = FakeKiteProvider()
    provider.get_live_candles = lambda symbols: {}
    data = KiteDataProvider(provider, history_cache_directory=tmp_path)
    original = data.get_data("BANKNIFTY")

    data.begin_live_refresh(["BANKNIFTY"])
    refreshed = data.get_data("BANKNIFTY")
    data.end_live_refresh()

    pd.testing.assert_frame_equal(refreshed, original)
    assert provider.live_calls == []


def test_bulk_quotes_filter_indices_and_skip_bad_equity_quotes():
    class FakeKite:
        requested = []

        def quote(self, keys):
            self.requested.extend(keys)
            return {
                "NSE:RELIANCE": {"last_price": 150, "ohlc": {"open": 148,
                                  "high": 151, "low": 147}, "volume": 100},
                "NSE:BADQUOTE": {"last_price": "not-a-price"},
            }

    provider = KiteProvider.__new__(KiteProvider)
    provider.kite = FakeKite()
    provider._instrument_token = lambda symbol: (
        1 if symbol in {"RELIANCE", "BADQUOTE"}
        else (_ for _ in ()).throw(ValueError("not an equity"))
    )

    candles = provider.get_live_candles(
        ["RELIANCE", "BANKNIFTY", "NIFTY", "FINNIFTY", "MIDCPNIFTY", "BADQUOTE"]
    )

    assert provider.kite.requested == ["NSE:RELIANCE", "NSE:BADQUOTE"]
    assert list(candles) == ["RELIANCE"]
