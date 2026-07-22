"""Historical OHLCV data adapter backed by Zerodha Kite Connect."""

from __future__ import annotations

from datetime import date, datetime, time as clock_time
import logging
from pathlib import Path
from threading import RLock
from time import time
from zoneinfo import ZoneInfo

import pandas as pd
from requests.exceptions import RequestException

from src.providers.kite_provider import KiteProvider


logger = logging.getLogger(__name__)


class KiteDataProvider:
    """Matches the ``DataProvider.get_data`` interface using live Kite data."""

    def __init__(
        self,
        provider: KiteProvider | None = None,
        long_history_cache_directory: str | Path = ".cache/kite_long_history",
        history_cache_directory: str | Path = ".cache/kite_history",
        instrument_cache_directory: str | Path = ".cache/kite_instruments",
        history_cache_ttl_seconds: int = 15 * 60,
        max_stale_history_days: int = 7,
    ):
        self.provider = provider or KiteProvider()
        self._history_cache = {}
        self._long_history_cache = {}
        self._long_history_cache_directory = Path(long_history_cache_directory)
        self._history_cache_directory = Path(history_cache_directory)
        self._instrument_cache_directory = Path(instrument_cache_directory)
        self._history_cache_ttl_seconds = history_cache_ttl_seconds
        self._max_stale_history_days = max_stale_history_days
        self._long_history_cache_lock = RLock()
        self._history_cache_lock = RLock()
        self._nfo_instruments = None
        self._live_refresh = False
        self._live_candles: dict[str, dict] = {}
        self._live_candles_prefetched = False

    def begin_live_refresh(self, symbols: list[str] | None = None) -> None:
        """Make subsequent reads include the current, still-forming NSE candle.

        The UI keeps this provider alive with ``st.cache_resource``.  Without
        explicitly starting a new snapshot, its in-memory history cache never
        expires and later daily reports can keep using the first report's data.
        Cached history remains reusable: only today's small live quote is read.
        """
        with self._history_cache_lock:
            self._history_cache.clear()
            self._live_refresh = True
            get_live_candles = getattr(self.provider, "get_live_candles", None)
            self._live_candles_prefetched = get_live_candles is not None and bool(symbols)
            self._live_candles = (
                get_live_candles(symbols) if get_live_candles is not None and symbols else {}
            )

    def end_live_refresh(self) -> None:
        with self._history_cache_lock:
            self._live_refresh = False
            self._live_candles = {}
            self._live_candles_prefetched = False

    @property
    def live_refresh_active(self) -> bool:
        """Whether reads are currently using a live intraday snapshot."""
        return self._live_refresh

    def has_live_candle(self, symbol: str) -> bool:
        """Return true only when the current snapshot contains this symbol."""
        key = symbol.upper().removesuffix(".NS")
        return self._live_refresh and key in self._live_candles

    def _with_live_candle(self, symbol: str, history: pd.DataFrame) -> pd.DataFrame:
        """Overlay today's quote without redownloading a year of candles."""
        if history is None or history.empty:
            return history
        candle = self._live_candles.get(symbol)
        if candle is None:
            if self._live_candles_prefetched:
                return history
            get_live_candle = getattr(self.provider, "get_live_candle", None)
            if get_live_candle is None:
                return history
            candle = get_live_candle(symbol)
        live = history.copy()
        timezone = getattr(live.index, "tz", None)
        today = pd.Timestamp.now(tz=timezone).normalize()
        try:
            for column in ("Open", "High", "Low", "Close", "Volume"):
                live.loc[today, column] = candle[column]
        except (KeyError, TypeError, ValueError):
            # One malformed/suspended instrument must not abort the universe.
            return history
        return live.sort_index()

    @staticmethod
    def _merge_history(cached, fresh):
        frames = [item for item in (cached, fresh) if item is not None and not item.empty]
        if not frames:
            return pd.DataFrame()
        merged = pd.concat(frames)
        return merged[~merged.index.duplicated(keep="last")].sort_index()

    def _history_path(self, symbol: str) -> Path:
        return self._history_cache_directory / f"{symbol}.parquet"

    def _history_cache_is_fresh(self, path: Path) -> bool:
        if time() - path.stat().st_mtime < self._history_cache_ttl_seconds:
            return True
        india = ZoneInfo("Asia/Kolkata")
        now = datetime.now(india)
        written = datetime.fromtimestamp(path.stat().st_mtime, india)
        # Once the daily NSE candle is complete, it cannot change again that
        # calendar day. Intraday runs continue to use the short TTL above.
        return written.date() == now.date() and now.time() >= clock_time(16, 0)

    def _long_history_path(self, symbol: str, period: str) -> Path:
        safe_period = period.lower().replace("/", "_").replace("\\", "_")
        return self._long_history_cache_directory / f"{symbol}_{safe_period}.parquet"

    def get_data(self, symbol: str):
        key = symbol.upper().removesuffix(".NS")
        with self._history_cache_lock:
            if key in self._history_cache:
                return self._history_cache[key].copy()
            path = self._history_path(key)
            cached = None
            if path.exists():
                try:
                    cached = pd.read_parquet(path)
                except (OSError, ValueError):
                    cached = None
            # Reuse fresh data across CLI/API restarts. The short TTL avoids
            # freezing today's still-forming daily candle for the whole day;
            # stale caches request only an overlap from their last candle.
            cache_is_fresh = (
                cached is not None and not cached.empty
                and self._history_cache_is_fresh(path)
            )
            if cache_is_fresh:
                history = cached
            else:
                if cached is not None and not cached.empty:
                    try:
                        try:
                            fresh = self.provider.get_historical_data(
                                key, from_date=cached.index.max().date()
                            )
                        except TypeError:  # Compatibility with small custom providers.
                            fresh = self.provider.get_historical_data(key)
                    except RequestException:
                        # A transient Kite outage must not erase a technically
                        # usable candidate from the scan.  The last completed
                        # daily candle is safer than silently treating the
                        # symbol as an analysis failure.  Authentication and
                        # malformed-data errors still propagate normally.
                        latest = cached.index.max()
                        latest_date = (
                            latest.date() if hasattr(latest, "date") else pd.Timestamp(latest).date()
                        )
                        age_days = (date.today() - latest_date).days
                        if age_days > self._max_stale_history_days:
                            raise
                        logger.warning(
                            "Kite refresh failed for %s; using cached history through %s (%d day(s) old)",
                            key, latest_date.isoformat(), age_days,
                        )
                        fresh = None
                    history = self._merge_history(cached, fresh)
                else:
                    history = self.provider.get_historical_data(key)
                if history is not None and not history.empty:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    history.to_parquet(path)
            if self._live_refresh:
                history = self._with_live_candle(key, history)
            self._history_cache[key] = history
            return history.copy()

    def get_long_history(self, symbol: str, period: str = "10y"):
        key = (symbol.upper().removesuffix(".NS"), period)
        with self._long_history_cache_lock:
            if key in self._long_history_cache:
                return self._long_history_cache[key].copy()

            cache_path = self._long_history_path(*key)
            if cache_path.exists():
                try:
                    cached = pd.read_parquet(cache_path)
                    if not cached.empty:
                        self._long_history_cache[key] = cached
                        return cached.copy()
                except (OSError, ValueError):
                    # A partial or incompatible cache file is treated as a miss.
                    pass

            history = self.provider.get_historical_data(key[0], period=period)
            self._long_history_cache[key] = history
            if history is not None and not history.empty:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                history.to_parquet(cache_path)
            return self._long_history_cache[key].copy()

    def get_symbols(self) -> list[str]:
        """Get the current F&O equity universe directly from Kite."""
        instruments = self.get_nfo_instruments()
        return sorted(
            {
                str(item["name"]).strip().upper()
                for item in instruments
                if item.get("instrument_type") == "FUT" and item.get("name")
            }
        )

    def get_nfo_instruments(self):
        """Share one daily NFO instrument master with universe/options code."""
        if self._nfo_instruments is not None:
            return self._nfo_instruments
        path = self._instrument_cache_directory / f"nfo_{date.today().isoformat()}.parquet"
        if path.exists():
            try:
                frame = pd.read_parquet(path)
                if "expiry" in frame:
                    frame["expiry"] = frame["expiry"].apply(
                        lambda value: value.date() if isinstance(value, pd.Timestamp) else value
                    )
                self._nfo_instruments = frame.to_dict("records")
                return self._nfo_instruments
            except (OSError, ValueError):
                pass
        self._nfo_instruments = self.provider.kite.instruments("NFO")
        if self._nfo_instruments:
            path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(self._nfo_instruments).to_parquet(path)
        return self._nfo_instruments
