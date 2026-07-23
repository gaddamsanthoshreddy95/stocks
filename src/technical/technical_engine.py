"""
Technical Engine

Loads historical data, calculates indicators,
runs StockAnalyzer and converts the output
into DecisionContext.
"""

from src.analysis.analyzer import StockAnalyzer
from src.data_provider.provider import DataProvider
from src.indicators.pipeline import IndicatorPipeline
from src.models.decision_context import DecisionContext
from src.analysis.price_action_engine import PriceActionEngine


class TechnicalEngine:

    def __init__(self):

        self.provider = DataProvider()

    def analyze(self, symbol: str):

        # --------------------------------------------------
        # Load Historical Data
        # --------------------------------------------------

        df = self.provider.get_data(symbol)

        if df is None:

            return DecisionContext(

                engine="TECHNICAL",

                status="UNKNOWN",

                score=0,

                confidence=0,

                reasons=["Historical data unavailable."],

                warnings=["Unable to analyze technical indicators."],

                metadata={}

            )

        # --------------------------------------------------
        # Calculate Indicators
        # --------------------------------------------------

        df = IndicatorPipeline.run(df)

        # --------------------------------------------------
        # Analyze
        # --------------------------------------------------

        report = StockAnalyzer.analyze(symbol, df)
        price_action = PriceActionEngine().analyze(df)

        reasons = []

        # -----------------------------
        # Trend
        # -----------------------------

        if report.trend.upper() == "BUY":

            reasons.append("Trend is bullish.")

        elif report.trend.upper() == "SELL":

            reasons.append("Trend is bearish.")

        # -----------------------------
        # RSI
        # -----------------------------

        if report.rsi_signal.upper() == "BUY":

            reasons.append("RSI supports bullish momentum.")

        elif report.rsi_signal.upper() == "SELL":

            reasons.append("RSI indicates bearish momentum.")

        # -----------------------------
        # MACD
        # -----------------------------

        if "BUY" in report.macd_signal.upper():

            reasons.append("MACD confirms bullish trend.")

        elif "SELL" in report.macd_signal.upper():

            reasons.append("MACD confirms bearish trend.")

        # -----------------------------
        # Volume
        # -----------------------------

        if str(report.volume_signal).upper() == "HIGH":

            reasons.append("High trading volume detected.")

        # -----------------------------
        # Status
        # -----------------------------

        score = report.score

        if score >= 80:

            status = "STRONG"

        elif score >= 60:

            status = "BULLISH"

        elif score >= 40:

            status = "NEUTRAL"

        else:

            status = "WEAK"

        return DecisionContext(

            engine="TECHNICAL",

            status=status,

            score=score,

            confidence=score,

            reasons=reasons,

            warnings=[],

            metadata={

                "current_price": report.current_price,

                "ema20": report.ema20,

                "ema50": report.ema50,

                "ema200": report.ema200,

                "rsi": report.rsi,

                "atr": report.atr,

                "recommendation": report.recommendation,
                "price_action": price_action,

            }

        )
