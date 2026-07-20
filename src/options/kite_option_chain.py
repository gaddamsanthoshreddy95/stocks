"""Build a live equity option chain from Zerodha Kite quotes."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from src.options.models.option_chain import OptionChain
from src.options.models.option_contract import OptionContract
from src.options.black_scholes import implied_volatility, price_and_greeks


class OptionInstrumentLookupError(ValueError):
    pass


class OptionQuoteUnavailableError(ValueError):
    pass


class KiteOptionChainProvider:
    """Loads the nearest expiry's contracts and quote/OI data from Kite."""

    def __init__(self, kite, instruments=None):
        self.kite = kite
        self.oi_cache_path = Path("data/cache/option_chain/oi_snapshots.json")
        self._instruments = instruments
        self._chain_cache = {}

    def _load_instruments(self):
        if self._instruments is None:
            self._instruments = self.kite.instruments("NFO")
        return self._instruments

    def _previous_oi(self):
        try:
            return json.loads(self.oi_cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _matches_month(expiry: date, expiry_month: str | None) -> bool:
        return expiry_month is None or expiry.strftime("%Y-%m") == expiry_month

    def _save_oi(self, values):
        self.oi_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.oi_cache_path.write_text(json.dumps(values))

    def get_chains(self, symbol: str, spot_price: float, min_dte: int = 7,
                   max_dte: int = 35, width: float = 0.15,
                   expiry_month: str | None = None) -> list[OptionChain]:
        today = date.today()
        expiries = sorted({
            item["expiry"] for item in self._load_instruments()
            if item.get("name", "").upper() == symbol.upper()
            and item.get("instrument_type") in {"CE", "PE"}
            and item.get("expiry")
            and self._matches_month(item["expiry"], expiry_month)
            and today + timedelta(days=min_dte) <= item["expiry"] <= today + timedelta(days=max_dte)
        })
        if expiry_month and not expiries:
            raise OptionInstrumentLookupError(
                f"No {symbol} option expiries in requested month {expiry_month} within the configured DTE range"
            )
        return [self.get_chain(symbol, spot_price, width, expiry) for expiry in expiries]

    def get_chain(self, symbol: str, spot_price: float, width: float = 0.15,
                  selected_expiry=None, expiry_month: str | None = None) -> OptionChain:
        symbol = symbol.upper()
        today = date.today()
        instruments = [
            item for item in self._load_instruments()
            if item.get("name", "").upper() == symbol
            and item.get("instrument_type") in {"CE", "PE"}
            and item.get("expiry")
            and item["expiry"] >= today
            and self._matches_month(item["expiry"], expiry_month)
        ]
        if not instruments:
            suffix = f" in requested month {expiry_month}" if expiry_month else ""
            raise OptionInstrumentLookupError(f"No live option instruments found for {symbol}{suffix}")
        expiry = selected_expiry or min(item["expiry"] for item in instruments)
        cache_key = (symbol, round(float(spot_price), 2), width, str(expiry))
        if cache_key in self._chain_cache:
            return self._chain_cache[cache_key]
        minimum, maximum = spot_price * (1 - width), spot_price * (1 + width)
        contracts = [
            item for item in instruments
            if item["expiry"] == expiry and minimum <= float(item["strike"]) <= maximum
        ]
        quote_symbols = [f"NFO:{item['tradingsymbol']}" for item in contracts]
        if not quote_symbols:
            raise OptionInstrumentLookupError(f"No contracts for {symbol} in the requested strike/expiry range")
        quotes = {}
        for offset in range(0, len(quote_symbols), 200):
            quotes.update(self.kite.quote(quote_symbols[offset:offset + 200]))
        if not quotes:
            raise OptionQuoteUnavailableError(f"No option quotes returned for {symbol} {expiry}")

        previous_oi = self._previous_oi()
        current_oi = {}
        years = max((expiry - today).days, 1) / 365

        calls, puts = [], []
        for item in contracts:
            quote = quotes.get(f"NFO:{item['tradingsymbol']}", {})
            quote_time = quote.get("timestamp")
            stale = False
            if isinstance(quote_time, datetime):
                normalized_time = quote_time if quote_time.tzinfo else quote_time.replace(tzinfo=timezone.utc)
                stale = datetime.now(timezone.utc) - normalized_time.astimezone(timezone.utc) > timedelta(minutes=30)
            depth = quote.get("depth", {})
            buy, sell = depth.get("buy", []), depth.get("sell", [])
            bid, ask = (float(buy[0]["price"]) if buy else 0.0), (float(sell[0]["price"]) if sell else 0.0)
            market_price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(quote.get("last_price") or 0)
            iv = implied_volatility(market_price, spot_price, float(item["strike"]), years, 0.07, item["instrument_type"])
            greeks = price_and_greeks(spot_price, float(item["strike"]), years, 0.07, iv / 100, item["instrument_type"]) if iv else None
            oi = int(quote.get("oi") or 0)
            key = item["tradingsymbol"]
            current_oi[key] = oi
            contract = OptionContract(
                symbol=item["tradingsymbol"], expiry=str(expiry), strike=float(item["strike"]),
                option_type=item["instrument_type"], last_price=float(quote.get("last_price") or 0),
                bid=bid, ask=ask, volume=int(quote.get("volume") or 0), open_interest=oi,
                change_in_oi=oi - int(previous_oi.get(key, oi)), implied_volatility=iv,
                delta=greeks["delta"] if greeks else None, gamma=greeks["gamma"] if greeks else None,
                theta=greeks["theta"] if greeks else None, vega=greeks["vega"] if greeks else None,
                rho=greeks["rho"] if greeks else None,
                price_change=float(quote.get("net_change")) if quote.get("net_change") is not None else None,
                lot_size=int(item.get("lot_size") or 1),
                greeks_source="BLACK_SCHOLES" if greeks else None,
                change_in_oi_reliable=key in previous_oi,
                quote_timestamp=quote_time.isoformat() if isinstance(quote_time, datetime) else None,
                quote_is_stale=stale,
            )
            (calls if contract.option_type == "CE" else puts).append(contract)
        self._save_oi(current_oi)
        chain = OptionChain(symbol=symbol, spot_price=spot_price, expiry=str(expiry), calls=calls, puts=puts)
        self._chain_cache[cache_key] = chain
        return chain
