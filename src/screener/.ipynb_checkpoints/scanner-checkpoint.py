"""
Stock Scanner

Scans multiple stocks and returns a list of StockAnalysis objects.
"""

from typing import List

from src.analysis.analyzer import StockAnalyzer
from src.data_provider.provider import DataProvider
from src.indicators.pipeline import IndicatorPipeline
from src.models.stock_analysis import StockAnalysis


class StockScanner:

    def __init__(self):

        self.provider = DataProvider()

    def scan(self, symbols: List[str]) -> List[StockAnalysis]:

        results = []

        print()
        print("=" * 80)
        print("AI TRADING ASSISTANT - STOCK SCANNER")
        print("=" * 80)

        total = len(symbols)

        for index, symbol in enumerate(symbols, start=1):

            print(f"\n[{index}/{total}] Scanning {symbol}")

            try:

                # ------------------------------------------
                # Load Historical Data
                # ------------------------------------------

                df = self.provider.get_data(symbol)

                if df is None:
                    print("Unable to load data.")
                    continue

                # ------------------------------------------
                # Apply Indicators
                # ------------------------------------------

                df = IndicatorPipeline.run(df)

                # ------------------------------------------
                # Analyze Stock
                # ------------------------------------------

                analysis = StockAnalyzer.analyze(symbol, df)

                results.append(analysis)

                print(
                    f"Completed | "
                    f"Score : {analysis.score}/{analysis.max_score}"
                )

            except Exception as ex:

                print(f"Error : {symbol} -> {ex}")

        print()
        print("=" * 80)
        print("Scanning Completed")
        print("=" * 80)

        return results