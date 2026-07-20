"""
Sector Strength Engine

Calculates today's strength for every sector.
"""

import math
from statistics import median

import pandas as pd

from src.market_data.base_provider import BaseMarketProvider
from src.sector.sector_mapper import SectorMapper


class SectorStrength(BaseMarketProvider):

    SECTOR_INDEX_SYMBOLS = {
        "BANKING": "^NSEBANK", "IT": "^CNXIT", "PHARMA": "^CNXPHARMA",
        "AUTO": "^CNXAUTO", "FMCG": "^CNXFMCG", "METAL": "^CNXMETAL",
        "REALTY": "^CNXREALTY", "ENERGY": "^CNXENERGY", "INFRA": "^CNXINFRA",
        "MEDIA": "^CNXMEDIA", "PSU_BANK": "^CNXPSUBANK",
        "FINANCIAL_SERVICES": "NIFTY_FIN_SERVICE.NS", "POWER": "^CNXENERGY",
        "DIVERSIFIED": "^NSEI", "CONSUMER_DURABLES": "^CNXCONSUM",
        "CONSUMER": "^CNXFMCG", "BUILDING_MATERIALS": "^CNXINFRA",
        "HEALTHCARE": "^CNXPHARMA", "CEMENT": "^CNXINFRA",
        "CAPITAL_GOODS": "^CNXINFRA",
    }

    KITE_INDEX_SYMBOLS = {
        "BANKING": "NIFTY BANK", "IT": "NIFTY IT", "AUTO": "NIFTY AUTO",
        "PHARMA": "NIFTY PHARMA", "FMCG": "NIFTY FMCG", "METAL": "NIFTY METAL",
        "REALTY": "NIFTY REALTY", "ENERGY": "NIFTY ENERGY", "INFRA": "NIFTY INFRA",
        "MEDIA": "NIFTY MEDIA", "PSU_BANK": "NIFTY PSU BANK",
    }
    MAPPING_ALIASES = {"REALTY": "REAL_ESTATE"}

    def __init__(self, historical_provider=None):

        self.indices = pd.read_csv("resources/sector_indices.csv")
        self.historical_provider = historical_provider
        self.mapper = SectorMapper()

    @staticmethod
    def _quote_from_history(data):
        if data is None or data.empty or "Close" not in data or len(data) < 2:
            return None
        closes = data["Close"].dropna()
        if len(closes) < 2:
            return None
        close, previous = float(closes.iloc[-1]), float(closes.iloc[-2])
        if not math.isfinite(close) or not math.isfinite(previous) or previous <= 0:
            return None
        change = close - previous
        return {"price": round(close, 2), "change": round(change, 2),
                "change_percent": round(change / previous * 100, 2), "source": "KITE"}

    def _kite_quote(self, sector):
        symbol = self.KITE_INDEX_SYMBOLS.get(sector)
        if self.historical_provider is None or not symbol:
            return None
        try:
            return self._quote_from_history(self.historical_provider.get_data(symbol))
        except Exception:
            return None

    @staticmethod
    def _bounded_momentum_score(value, scale):
        return max(0.0, min(100.0, 50.0 + float(value) * scale))

    def _constituent_composite(self, sector):
        """Data-backed fallback when an official sector index is unavailable."""
        if self.historical_provider is None:
            return None
        mapped_sector = self.MAPPING_ALIASES.get(sector, sector)
        symbols = self.mapper.get_sector_stocks(mapped_sector)
        metrics = []
        for symbol in symbols:
            try:
                history = self.historical_provider.get_data(symbol)
                closes = pd.to_numeric(history["Close"], errors="coerce").dropna()
            except Exception:
                continue
            if len(closes) < 51 or float(closes.iloc[-21]) <= 0 or float(closes.iloc[-6]) <= 0:
                continue
            current = float(closes.iloc[-1])
            metrics.append({
                "above_20": current > float(closes.tail(20).mean()),
                "above_50": current > float(closes.tail(50).mean()),
                "return_20": (current / float(closes.iloc[-21]) - 1) * 100,
                "return_5": (current / float(closes.iloc[-6]) - 1) * 100,
            })
        if not metrics:
            return None
        breadth_20 = 100 * sum(item["above_20"] for item in metrics) / len(metrics)
        breadth_50 = 100 * sum(item["above_50"] for item in metrics) / len(metrics)
        return_20 = median(item["return_20"] for item in metrics)
        return_5 = median(item["return_5"] for item in metrics)
        momentum_20 = self._bounded_momentum_score(return_20, 5.0)
        momentum_5 = self._bounded_momentum_score(return_5, 10.0)
        score = .30 * breadth_20 + .20 * breadth_50 + .30 * momentum_20 + .20 * momentum_5
        return {
            "available": True, "status": "AVAILABLE", "score": round(score, 2),
            "score_model": "CONSTITUENT_TECHNICAL_COMPOSITE", "source": "KITE_CONSTITUENTS",
            "sample_count": len(metrics), "breadth_above_20dma_percent": round(breadth_20, 2),
            "breadth_above_50dma_percent": round(breadth_50, 2),
            "median_return_20d_percent": round(return_20, 2),
            "median_return_5d_percent": round(return_5, 2),
            "price": None, "change_percent": None,
        }

    def analyze(self):

        report = {}

        requested = {
            str(row["Sector"]): self.SECTOR_INDEX_SYMBOLS.get(
                str(row["Sector"]), row["IndexSymbol"]
            )
            for _, row in self.indices.iterrows()
        }
        kite_downloads = {sector: self._kite_quote(sector) for sector in requested}
        yahoo_requested = {sector: symbol for sector, symbol in requested.items()
                           if kite_downloads.get(sector) is None}
        yahoo_downloads = self.download_symbols(yahoo_requested)
        downloads = {sector: (kite_downloads.get(sector) or yahoo_downloads.get(sector))
                     for sector in requested}

        for _, row in self.indices.iterrows():

            sector = row["Sector"]

            symbol = self.SECTOR_INDEX_SYMBOLS.get(sector, row["IndexSymbol"])

            data = downloads.get(sector)

            if data is None:

                composite = self._constituent_composite(sector)
                if composite is not None:
                    report[sector] = composite
                    continue

                report[sector] = {"available": False, "status": "UNAVAILABLE", "score": None,
                                  "rating": "UNAVAILABLE", "reason": f"No index data for {symbol}"}

                continue

            try:
                price = float(data["price"])
                cp = float(data["change_percent"])
            except (KeyError, TypeError, ValueError):
                price = cp = float("nan")

            if not math.isfinite(price) or not math.isfinite(cp):
                report[sector] = {"available": False, "status": "UNAVAILABLE", "score": None,
                                  "rating": "UNAVAILABLE", "price": None,
                                  "change_percent": None,
                                  "reason": f"Invalid index data for {symbol}"}
                continue

            report[sector] = {

                "available": True,
                "status": "AVAILABLE",

                "price": price,

                "change_percent": cp,

                "source": data.get("source", "YAHOO"),

                "rating": None,

                "score": None

            }

        # Score sectors against one another instead of assigning the same
        # fixed number to every member of a broad daily-change bucket.
        index_rows = [(name, row) for name, row in report.items()
                      if row.get("status") == "AVAILABLE"
                      and row.get("change_percent") is not None]
        changes = sorted({row["change_percent"] for _, row in index_rows})
        activity = sum(abs(row["change_percent"]) for _, row in index_rows)
        for _, row in index_rows:
            percentile = (50.0 if len(changes) == 1 else
                          100.0 * changes.index(row["change_percent"]) / (len(changes) - 1))
            row["score"] = round(percentile, 2)
            row["score_model"] = "SECTOR_RETURN_CROSS_SECTIONAL_PERCENTILE"
            row["contribution_proxy_percent"] = (
                round(100.0 * row["change_percent"] / activity, 2) if activity else 0.0
            )
            row["contribution_model"] = "EQUAL_WEIGHT_SECTOR_INDEX_MOVE_PROXY"
            if percentile >= 75:
                row["rating"] = "STRONG"
            elif percentile >= 55:
                row["rating"] = "BULLISH"
            elif percentile >= 40:
                row["rating"] = "NEUTRAL"
            else:
                row["rating"] = "WEAK"

        for _, row in report.items():
            if row.get("status") != "AVAILABLE" or row.get("rating"):
                continue
            score = float(row["score"])
            row["rating"] = ("STRONG" if score >= 75 else "BULLISH" if score >= 55
                             else "NEUTRAL" if score >= 40 else "WEAK")

        return report
