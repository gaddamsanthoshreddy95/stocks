"""
Sector Strength Engine

Calculates today's strength for every sector.
"""

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

        for _, row in self.indices.iterrows():

            sector = row["Sector"]

            symbol = self.SECTOR_INDEX_SYMBOLS.get(sector, row["IndexSymbol"])

            data = self.download_symbol(symbol)

            if data is None:

                report[sector] = {"available": False, "status": "UNAVAILABLE", "score": None,
                                  "rating": "UNAVAILABLE", "reason": f"No index data for {symbol}"}

                continue

            cp = data["change_percent"]

            if cp >= 1:

                rating = "STRONG"

                score = 90

            elif cp > 0:

                rating = "BULLISH"

                score = 75

            elif cp > -1:

                rating = "NEUTRAL"

                score = 55

            else:

                rating = "WEAK"

                score = 30

            report[sector] = {

                "available": True,
                "status": "AVAILABLE",

                "price": data["price"],

                "change_percent": cp,

                "rating": rating,

                "score": score

            }

        return report
