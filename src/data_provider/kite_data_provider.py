"""Historical OHLCV data adapter backed by Zerodha Kite Connect."""

from __future__ import annotations

from datetime import date, datetime, time as clock_time
from pathlib import Path
from threading import RLock
from time import time
from zoneinfo import ZoneInfo

import pandas as pd

from src.providers.kite_provider import KiteProvider


class KiteDataProvider:
    """Matches the ``DataProvider.get_data`` interface using live Kite data."""

    def __init__(
        self,
        provider: KiteProvider | None = None,
        long_history_cache_directory: str | Path = ".cache/kite_long_history",
        history_cache_directory: str | Path = ".cache/kite_history",
        instrument_cache_directory: str | Path = ".cache/kite_instruments",
        history_cache_ttl_seconds: int = 15 * 60,
    ):
        self.provider = provider or KiteProvider()
        self._history_cache = {}
        self._long_history_cache = {}
        self._long_history_cache_directory = Path(long_history_cache_directory)
        self._history_cache_directory = Path(history_cache_directory)
        self._instrument_cache_directory = Path(instrument_cache_directory)
        self._history_cache_ttl_seconds = history_cache_ttl_seconds
        self._long_history_cache_lock = RLock()
        self._history_cache_lock = RLock()
        self._nfo_instruments = None
        self._live_refresh = False

    def begin_live_refresh(self) -> None:
        """Make subsequent reads fetch the current, still-forming NSE candle.

        The UI keeps this provider alive with ``st.cache_resource``.  Without
        explicitly starting a new snapshot, its in-memory history cache never
        expires and later daily reports can keep using the first report's data.
        """
        with self._history_cache_lock:
            self._history_cache.clear()
            self._live_refresh = True

    def end_live_refresh(self) -> None:
        with self._history_cache_lock:
            self._live_refresh = False

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
                and not self._live_refresh
            )
            if cache_is_fresh:
                history = cached
            else:
                if cached is not None and not cached.empty:
                    try:
                        fresh = self.provider.get_historical_data(
                            key, from_date=cached.index.max().date()
                        )
                    except TypeError:  # Compatibility with small custom providers.
                        fresh = self.provider.get_historical_data(key)
                    history = self._merge_history(cached, fresh)
                else:
                    history = self.provider.get_historical_data(key)
                if history is not None and not history.empty:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    history.to_parquet(path)
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
