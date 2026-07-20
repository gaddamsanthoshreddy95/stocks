"""
Forex Provider
"""

import yfinance as yf
from src.market_data.base_provider import BaseMarketProvider

class ForexProvider(BaseMarketProvider):

    SYMBOLS = {

        "usd_inr": "INR=X",

        "dollar_index": "DX-Y.NYB"

    }

    def _download(self, symbol):

        df = yf.download(

            symbol,

            period="5d",

            interval="1d",

            progress=False,

            auto_adjust=True

        )

        if df.empty:
            return None

        close = float(df["Close"].iloc[-1])

        previous = float(df["Close"].iloc[-2])

        change = close - previous

        change_percent = (change / previous) * 100

        return {

            "price": round(close, 2),

            "change": round(change, 2),

            "change_percent": round(change_percent, 2)

        }

    def get_snapshot(self):
        return self.download_symbols(self.SYMBOLS)
