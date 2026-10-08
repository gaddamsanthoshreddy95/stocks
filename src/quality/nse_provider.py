"""Small, cached client for NSE's public equity quote endpoints."""

from __future__ import annotations

from math import isfinite
import re
from threading import RLock
from time import monotonic
from typing import Any

import requests

from src.quality.models import FundamentalSnapshot


class NseFundamentalProvider:
    """Fetch quote valuation and delivery data from NSE's website API."""

    base_url = "https://www.nseindia.com"

    def __init__(self, *, timeout: float = 5.0, cache_ttl: float = 300.0,
                 session: requests.Session | None = None):
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self.base_url}/",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        })
        self._lock = RLock()
        self._session_initialized = False
        self._unavailable_until = 0.0
        self._cache: dict[str, tuple[float, FundamentalSnapshot | None]] = {}

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None:
            return None
        try:
            number = float(str(value).replace(",", "").replace("%", "").strip())
        except (TypeError, ValueError):
            return None
        return number if isfinite(number) else None

    @classmethod
    def _parse_snapshot(
        cls, symbol: str, quote: dict[str, Any], trade_info: dict[str, Any],
    ) -> FundamentalSnapshot:
        metadata = quote.get("metadata") or {}
        dp = trade_info.get("securityWiseDP") or {}
        delivery = cls._number(
            dp.get("deliveryToTradedQuantity")
            or trade_info.get("deliveryToTradedQuantity")
        )
        if delivery is None:
            delivered = cls._number(dp.get("deliveryQuantity"))
            traded = cls._number(dp.get("quantityTraded"))
            if delivered is not None and traded:
                delivery = delivered * 100 / traded
        return FundamentalSnapshot(
            symbol=symbol,
            pe_ratio=cls._number(metadata.get("pdSymbolPe")),
            sector_pe=cls._number(metadata.get("pdSectorPe")),
            delivery_percent=delivery,
            source="NSE",
            as_of=metadata.get("lastUpdateTime") or dp.get("secWiseDelPosDate"),
        )

    def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self._session_initialized:
            response = self.session.get(self.base_url, timeout=self.timeout)
            response.raise_for_status()
            self._session_initialized = True
        response = self.session.get(
            f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("NSE returned a non-object response")
        return payload

    def get_fundamentals(self, symbol: str) -> FundamentalSnapshot | None:
        clean_symbol = str(symbol).strip().upper()
        if not re.fullmatch(r"[A-Z0-9.&-]{1,30}", clean_symbol):
            raise ValueError("Invalid NSE symbol")
        with self._lock:
            cached = self._cache.get(clean_symbol)
            if cached and monotonic() - cached[0] < self.cache_ttl:
                return cached[1]
            now = monotonic()
            if now < self._unavailable_until:
                self._cache[clean_symbol] = (now, None)
                return None
            try:
                quote = self._get_json(
                    "/api/quote-equity", {"symbol": clean_symbol})
                trade_info = self._get_json(
                    "/api/quote-equity",
                    {"symbol": clean_symbol, "section": "trade_info"},
                )
            except (requests.RequestException, ValueError):
                self._unavailable_until = monotonic() + min(self.cache_ttl, 60)
                self._cache[clean_symbol] = (monotonic(), None)
                return None
            snapshot = self._parse_snapshot(clean_symbol, quote, trade_info)
            self._cache[clean_symbol] = (monotonic(), snapshot)
            return snapshot