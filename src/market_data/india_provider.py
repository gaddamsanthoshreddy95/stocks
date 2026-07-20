"""
India Market Provider

Downloads Indian market indices.
"""

from src.market_data.base_provider import BaseMarketProvider


class IndiaMarketProvider(BaseMarketProvider):

    def __init__(self):

        self.symbols = {

            "nifty": "^NSEI",

            "banknifty": "^NSEBANK",

            "finnifty": "NIFTY_FIN_SERVICE.NS"

        }

    def get_snapshot(self):
        return self.download_symbols(self.symbols)
