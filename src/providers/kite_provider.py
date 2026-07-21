"""
Kite Market Data Provider
"""

from datetime import date, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic, sleep

import pandas as pd
from kiteconnect import KiteConnect

from src.config.secrets import Secrets

from src.providers.base_provider import BaseProvider


class KiteProvider(BaseProvider):

    _instrument_tokens: dict[str, int] | None = None
    _instrument_tokens_lock = Lock()

    def __init__(self):

        self.kite = KiteConnect(
            api_key=Secrets.KITE_API_KEY
        )

        self.kite.set_access_token(
            Secrets.KITE_ACCESS_TOKEN
        )
        self._historical_lock = Lock()
        self._last_historical_request = 0.0
        self._historical_min_interval = 0.34

    # -----------------------------------------------------

    def get_profile(self):

        return self.kite.profile()

    # -----------------------------------------------------

    def get_ltp(self, symbol):

        exchange_symbol = f"NSE:{symbol}"

        data = self.kite.ltp([exchange_symbol])

        return data[exchange_symbol]["last_price"]

    def get_live_candle(self, symbol: str) -> dict:
        """Return today's live NSE OHLCV snapshot for one equity."""
        return self.get_live_candles([symbol])[symbol.upper().removesuffix(".NS")]

    def get_live_candles(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch live snapshots in Kite's bulk-quote batches."""
        clean_symbols = list(dict.fromkeys(
            symbol.upper().removesuffix(".NS") for symbol in symbols
        ))
        quotes = {}
        for offset in range(0, len(clean_symbols), 500):
            exchange_symbols = [f"NSE:{symbol}" for symbol in clean_symbols[offset:offset + 500]]
            quotes.update(self.kite.quote(exchange_symbols))
        candles = {}
        for symbol in clean_symbols:
            quote = quotes[f"NSE:{symbol}"]
            ohlc = quote.get("ohlc") or {}
            last_price = float(quote["last_price"])
            candles[symbol] = {
                "Open": float(ohlc.get("open") or last_price),
                "High": float(ohlc.get("high") or last_price),
                "Low": float(ohlc.get("low") or last_price),
                "Close": last_price,
                "Volume": int(quote.get("volume") or 0),
            }
        return candles

    # -----------------------------------------------------

    def get_historical_data(

        self,

        symbol,

        period="1y",
        interval="day",
        from_date=None,

    ):

        instrument_token = self._instrument_token(symbol)
        from_date = from_date or self._from_date(period)
        kite_interval = {
            "1d": "day",
            "day": "day",
            "1h": "60minute",
            "60minute": "60minute",
            "30m": "30minute",
            "30minute": "30minute",
            "15m": "15minute",
            "15minute": "15minute",
            "5m": "5minute",
            "5minute": "5minute",
        }.get(interval)
        if kite_interval is None:
            raise ValueError(f"Unsupported Kite interval: {interval}")

        # Kite limits the date span per historical request. Daily ten-year
        # history is fetched in bounded chunks and then de-duplicated.
        candles = []
        chunk_start = from_date
        today = date.today()
        max_days = 1999 if kite_interval == "day" else 100
        while chunk_start <= today:
            chunk_end = min(chunk_start + timedelta(days=max_days), today)
            # Pace the actual request rather than sleeping a fixed amount after
            # an entire symbol has completed. Slow requests therefore consume
            # their own rate-limit interval and add no unnecessary delay.
            with self._historical_lock:
                remaining = self._historical_min_interval - (
                    monotonic() - self._last_historical_request
                )
                if remaining > 0:
                    sleep(remaining)
                self._last_historical_request = monotonic()
                candles.extend(self.kite.historical_data(
                    instrument_token, chunk_start, chunk_end, kite_interval,
                ))
            chunk_start = chunk_end + timedelta(days=1)
        dataframe = pd.DataFrame(candles)
        if dataframe.empty:
            return dataframe
        dataframe = dataframe.rename(
            columns={
                "date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
        dataframe["Date"] = pd.to_datetime(dataframe["Date"])
        dataframe = dataframe.drop_duplicates(subset=["Date"]).sort_values("Date")
        return dataframe.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]

    @staticmethod
    def _from_date(period: str) -> date:
        """Convert a small, explicit period vocabulary to a calendar date."""
        offsets = {
            "1mo": pd.DateOffset(months=1),
            "3mo": pd.DateOffset(months=3),
            "6mo": pd.DateOffset(months=6),
            "1y": pd.DateOffset(years=1),
            "2y": pd.DateOffset(years=2),
            "5y": pd.DateOffset(years=5),
            "10y": pd.DateOffset(years=10),
        }
        if period not in offsets:
            raise ValueError(f"Unsupported Kite period: {period}")
        return (pd.Timestamp.today().normalize() - offsets[period]).date()

    @classmethod
    def _instrument_token(cls, symbol: str) -> int:
        """Resolve an NSE equity token from the instrument-master reference file."""
        clean_symbol = symbol.upper().removesuffix(".NS")
        instrument_file = Path(__file__).resolve().parents[2] / "data" / "instruments.csv"
        if not instrument_file.exists():
            raise FileNotFoundError(
                "data/instruments.csv is required to resolve Kite instrument tokens"
            )
        if cls._instrument_tokens is None:
            with cls._instrument_tokens_lock:
                if cls._instrument_tokens is None:
                    instruments = pd.read_csv(
                        instrument_file,
                        usecols=["instrument_token", "tradingsymbol", "exchange", "instrument_type"],
                    )
                    equities = instruments[
                        (instruments["exchange"] == "NSE")
                        & (instruments["instrument_type"] == "EQ")
                    ]
                    cls._instrument_tokens = dict(zip(
                        equities["tradingsymbol"].astype(str).str.upper(),
                        equities["instrument_token"].astype(int),
                    ))
        token = cls._instrument_tokens.get(clean_symbol)
        if token is None:
            raise ValueError(f"No NSE equity instrument token found for {clean_symbol}")
        return token

    # -----------------------------------------------------

    def get_option_chain(

        self,

        symbol

    ):

        raise NotImplementedError(
            "Option Chain implementation coming next."
        )
