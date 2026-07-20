"""
Sector Strength Engine

Calculates today's strength for every sector.
"""

import math

import pandas as pd

from src.market_data.base_provider import BaseMarketProvider


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

    def __init__(self):

        self.indices = pd.read_csv("resources/sector_indices.csv")

    def analyze(self):

        report = {}

        requested = {
            str(row["Sector"]): self.SECTOR_INDEX_SYMBOLS.get(
                str(row["Sector"]), row["IndexSymbol"]
            )
            for _, row in self.indices.iterrows()
        }
        downloads = self.download_symbols(requested)

        for _, row in self.indices.iterrows():

            sector = row["Sector"]

            symbol = self.SECTOR_INDEX_SYMBOLS.get(sector, row["IndexSymbol"])

            data = downloads.get(sector)

            if data is None:

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

                "rating": None,

                "score": None

            }

        # Score sectors against one another instead of assigning the same
        # fixed number to every member of a broad daily-change bucket.
        available = [(name, row) for name, row in report.items()
                     if row.get("status") == "AVAILABLE"]
        changes = sorted({row["change_percent"] for _, row in available})
        activity = sum(abs(row["change_percent"]) for _, row in available)
        for _, row in available:
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

        return report
