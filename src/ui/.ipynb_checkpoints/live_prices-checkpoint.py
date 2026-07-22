"""Thread-safe Kite WebSocket price cache for the Streamlit UI."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable


class KiteLivePriceFeed:
    """Maintain one reconnecting ticker and expose its latest prices safely."""

    def __init__(self, provider: Any, ticker_factory: Callable | None = None):
        self.provider = provider
        self._ticker_factory = ticker_factory
        self._ticker = None
        self._lock = RLock()
        self._wanted_tokens: set[int] = set()
        self._token_symbols: dict[int, str] = {}
        self._prices: dict[str, dict[str, Any]] = {}
        self._started = False
        self._connected = False
        self._error: str | None = None

    def _build_ticker(self):
        if self._ticker_factory:
            return self._ticker_factory()
        from kiteconnect import KiteTicker
        from src.config.secrets import Secrets

        if not Secrets.KITE_API_KEY or not Secrets.KITE_ACCESS_TOKEN:
            raise ValueError("Kite API key and daily access token are required for live prices.")
        return KiteTicker(
            Secrets.KITE_API_KEY,
            Secrets.KITE_ACCESS_TOKEN,
            reconnect=True,
            reconnect_max_tries=50,
            reconnect_max_delay=60,
        )

    def update_symbols(self, symbols: list[str]) -> None:
        clean = {str(symbol).upper().removesuffix(".NS") for symbol in symbols if symbol}
        token_symbols = {}
        for symbol in clean:
            try:
                token_symbols[int(self.provider._instrument_token(symbol))] = symbol
            except (FileNotFoundError, TypeError, ValueError):
                continue
        with self._lock:
            previous = set(self._wanted_tokens)
            self._wanted_tokens = set(token_symbols)
            self._token_symbols = token_symbols
            connected, ticker = self._connected, self._ticker
        if not self._started and token_symbols:
            self.start()
            return
        if connected and ticker:
            added, removed = list(set(token_symbols) - previous), list(previous - set(token_symbols))
            if removed:
                ticker.unsubscribe(removed)
            if added:
                ticker.subscribe(added)
                ticker.set_mode(ticker.MODE_LTP, added)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        try:
            ticker = self._build_ticker()
            ticker.on_connect = self._on_connect
            ticker.on_ticks = self._on_ticks
            ticker.on_close = self._on_close
            ticker.on_error = self._on_error
            with self._lock:
                self._ticker = ticker
            ticker.connect(threaded=True)
        except Exception as exc:
            with self._lock:
                self._started = False
                self._error = str(exc)

    def _on_connect(self, ws, response) -> None:
        with self._lock:
            self._connected = True
            self._error = None
            tokens = list(self._wanted_tokens)
        if tokens:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_LTP, tokens)

    def _on_ticks(self, ws, ticks) -> None:
        received_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            for tick in ticks or []:
                symbol = self._token_symbols.get(int(tick.get("instrument_token", 0)))
                price = tick.get("last_price")
                if symbol and price is not None:
                    self._prices[symbol] = {"price": float(price), "received_at": received_at}

    def _on_close(self, ws, code, reason) -> None:
        with self._lock:
            self._connected = False
            self._error = f"WebSocket closed ({code}): {reason}"

    def _on_error(self, ws, code, reason) -> None:
        with self._lock:
            self._error = f"WebSocket error ({code}): {reason}"

    def quote(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            quote = self._prices.get(str(symbol).upper().removesuffix(".NS"))
            return dict(quote) if quote else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"connected": self._connected, "error": self._error,
                    "symbols": len(self._wanted_tokens)}
