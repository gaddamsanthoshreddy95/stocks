"""
Base Market Provider

Downloads market data using Yahoo Finance.

All market providers inherit from this class.
"""

import math

import pandas as pd
import yfinance as yf


class BaseMarketProvider:

    def download_symbol(self, symbol: str):

        try:

            df = yf.download(
                symbol,
                period="5d",
                progress=False,
                auto_adjust=False
            )

            if df is None or df.empty:
                return None

            # Flatten MultiIndex columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Need at least 2 trading days
            if len(df) < 2:
                return None

            close = float(df["Close"].iloc[-1])
            previous = float(df["Close"].iloc[-2])

            # yfinance can return rows whose Close values are NaN.  NaN does
            # not raise during arithmetic and would otherwise be mistaken for
            # a real negative sector move by downstream bucket logic.
            if not math.isfinite(close) or not math.isfinite(previous) or previous <= 0:
                return None

            change = round(close - previous, 2)

            change_percent = round(
                (change / previous) * 100,
                2
            )

            return {

                "price": close,

                "change": change,

                "change_percent": change_percent

            }

        except Exception as ex:

            print(f"Unable to download {symbol}: {ex}")

            return None
