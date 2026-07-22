"""
Console Presenter

Responsible only for displaying stock analysis.

It should NEVER calculate anything.
"""

from src.models.stock_analysis import StockAnalysis


class ConsolePresenter:

    @staticmethod
    def display(analysis: StockAnalysis):

        print()

        print("=" * 90)
        print(f"{analysis.symbol} ANALYSIS REPORT")
        print("=" * 90)

        print(f"Current Price : ₹{analysis.current_price:.2f}")

        # ==========================================================
        # Trend
        # ==========================================================

        print("\nTREND")
        print("-" * 90)

        print(f"EMA20              : ₹{analysis.ema20:.2f}")
        print(f"EMA50              : ₹{analysis.ema50:.2f}")
        print(f"EMA200             : ₹{analysis.ema200:.2f}")

        print(f"\nOverall Trend      : {analysis.trend}")

        # ==========================================================
        # RSI
        # ==========================================================

        print("\nMOMENTUM (RSI)")
        print("-" * 90)

        print(f"RSI Value          : {analysis.rsi:.2f}")
        print(f"RSI Signal         : {analysis.rsi_signal}")

        # ==========================================================
        # MACD
        # ==========================================================

        print("\nMACD")
        print("-" * 90)

        print(f"MACD Line          : {analysis.macd:.2f}")
        print(f"Signal Line        : {analysis.macd_signal_line:.2f}")
        print(f"Histogram          : {analysis.macd_histogram:.2f}")
        print(f"MACD Signal        : {analysis.macd_signal}")

        # ==========================================================
        # ATR
        # ==========================================================

        print("\nVOLATILITY (ATR)")
        print("-" * 90)

        print(f"ATR                : ₹{analysis.atr:.2f}")
        print(f"Expected Low       : ₹{analysis.expected_low:.2f}")
        print(f"Expected High      : ₹{analysis.expected_high:.2f}")

        # ==========================================================
        # Volume
        # ==========================================================

        print("\nVOLUME ANALYSIS")
        print("-" * 90)

        print(f"Today's Volume     : {analysis.volume:,}")
        print(f"20 Day Avg Volume  : {analysis.average_volume:,.0f}")
        print(f"Relative Volume    : {analysis.relative_volume:.2f}")
        print(f"Volume Signal      : {analysis.volume_signal}")

        # ==========================================================
        # Score
        # ==========================================================

        print("\nSTOCK SCORE")
        print("-" * 90)

        print(f"Overall Score      : {analysis.score}/{analysis.max_score}")
        print(f"Recommendation     : {analysis.recommendation}")

        print("=" * 90)