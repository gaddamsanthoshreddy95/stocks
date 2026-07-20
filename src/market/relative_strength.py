"""
Relative Strength Analyzer

Compares stock performance against NIFTY.
"""

import yfinance as yf
import pandas as pd
import math


class RelativeStrength:

    @staticmethod
    def _prepare(df: pd.DataFrame) -> pd.DataFrame:
        """
        Flattens MultiIndex columns if present.
        """

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df

    @classmethod
    def analyze(cls, symbol, lookback_sessions: int = 120, minimum_sessions: int = 60):

        stock = yf.download(
            symbol + ".NS",
            period="6mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )

        nifty = yf.download(
            "^NSEI",
            period="6mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )

        if stock.empty or nifty.empty:
            return {"status": "UNAVAILABLE", "score": None,
                    "reason": "Stock or NIFTY benchmark download returned no rows."}

        stock = cls._prepare(stock)
        nifty = cls._prepare(nifty)

        aligned_prices = pd.concat(
            [pd.to_numeric(stock["Close"], errors="coerce").rename("stock"),
             pd.to_numeric(nifty["Close"], errors="coerce").rename("market")],
            axis=1, join="inner",
        ).dropna().sort_index()
        aligned_prices = aligned_prices[~aligned_prices.index.duplicated(keep="last")].tail(lookback_sessions)
        if len(aligned_prices) < minimum_sessions:
            return {"status": "UNAVAILABLE", "score": None,
                    "reason": f"Only {len(aligned_prices)} aligned sessions; {minimum_sessions} required."}

        stock_start = float(aligned_prices["stock"].iloc[0])
        stock_end = float(aligned_prices["stock"].iloc[-1])
        nifty_start = float(aligned_prices["market"].iloc[0])
        nifty_end = float(aligned_prices["market"].iloc[-1])
        if not all(math.isfinite(value) and value > 0 for value in
                   (stock_start, stock_end, nifty_start, nifty_end)):
            return {"status": "FAILED", "score": None,
                    "reason": "Aligned stock or benchmark prices are invalid."}

        stock_return = ((stock_end - stock_start) / stock_start) * 100

        market_return = ((nifty_end - nifty_start) / nifty_start) * 100

        rs = stock_return - market_return
        aligned = aligned_prices.pct_change().dropna()
        market_variance = float(aligned["market"].var()) if not aligned.empty else 0
        beta = (float(aligned["stock"].cov(aligned["market"])) / market_variance
                if market_variance > 0 else None)

        if rs >= 10:
            rating = "VERY STRONG"
        elif rs >= 5:
            rating = "STRONG"
        elif rs >= 0:
            rating = "OUTPERFORM"
        elif rs >= -5:
            rating = "INLINE"
        else:
            rating = "UNDERPERFORM"

        return {
            "status": "AVAILABLE",
            "sample_count": len(aligned),
            "lookback_sessions": len(aligned_prices),
            "start_date": str(aligned_prices.index[0].date()),
            "end_date": str(aligned_prices.index[-1].date()),
            "stock_return": round(stock_return, 2),
            "market_return": round(market_return, 2),
            "relative_strength": round(rs, 2),
            "rating": rating,
            "beta": round(beta, 3) if beta is not None else None,
            "score": None,
            "score_model": "CROSS_SECTIONAL_PERCENTILE_PENDING",
        }
