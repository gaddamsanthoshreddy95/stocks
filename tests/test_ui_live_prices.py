import unittest

from src.ui.live_prices import KiteLivePriceFeed


class FakeProvider:
    TOKENS = {"SBIN": 101, "RELIANCE": 202}

    def _instrument_token(self, symbol):
        return self.TOKENS[symbol]


class FakeTicker:
    MODE_LTP = "ltp"

    def __init__(self):
        self.subscribed = []
        self.unsubscribed = []
        self.modes = []
        self.connected = False

    def connect(self, threaded=False):
        self.connected = threaded

    def subscribe(self, tokens):
        self.subscribed.extend(tokens)

    def unsubscribe(self, tokens):
        self.unsubscribed.extend(tokens)

    def set_mode(self, mode, tokens):
        self.modes.append((mode, tokens))


class KiteLivePriceFeedTests(unittest.TestCase):
    def test_subscribes_and_caches_ticks(self):
        ticker = FakeTicker()
        feed = KiteLivePriceFeed(FakeProvider(), ticker_factory=lambda: ticker)
        feed.update_symbols(["SBIN"])
        self.assertTrue(ticker.connected)

        ticker.on_connect(ticker, {})
        self.assertEqual(ticker.subscribed, [101])
        ticker.on_ticks(ticker, [{"instrument_token": 101, "last_price": 812.5}])

        quote = feed.quote("SBIN")
        self.assertEqual(quote["price"], 812.5)
        self.assertTrue(feed.status()["connected"])

    def test_updates_subscriptions_for_active_positions(self):
        ticker = FakeTicker()
        feed = KiteLivePriceFeed(FakeProvider(), ticker_factory=lambda: ticker)
        feed.update_symbols(["SBIN"])
        ticker.on_connect(ticker, {})
        feed.update_symbols(["RELIANCE"])

        self.assertIn(101, ticker.unsubscribed)
        self.assertIn(202, ticker.subscribed)


if __name__ == "__main__":
    unittest.main()
