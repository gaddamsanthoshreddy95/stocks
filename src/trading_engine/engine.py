"""
Trading Engine

Central orchestrator for the AI Trading Assistant.
"""

from src.analysis.analyzer import StockAnalyzer
from src.breakout.breakout_detector import BreakoutDetector
from src.candlestick.pattern_detector import PatternDetector
from src.data_provider.provider import DataProvider
from src.indicators.pipeline import IndicatorPipeline
from src.trade_setup.entry_analyzer import EntryAnalyzer
from src.decision.decision_engine import DecisionEngine
from src.decision.setup_entry_evaluator import SetupEntryEvaluator
from src.analysis.price_action_engine import PriceActionEngine
from src.scoring.score_engine import ScoreEngine


class TradingEngine:

    @staticmethod
    def _market_quality(df):
        """Return objective data-quality flags used to avoid fragile setups."""
        closes = df["Close"].astype(float)
        returns = closes.pct_change().dropna()
        gaps = (df["Open"].astype(float) / closes.shift(1) - 1).abs().dropna()
        return {
            "history_days": int(len(df)),
            "large_return_days": int((returns.abs() >= 0.10).sum()),
            "large_gap_days": int((gaps >= 0.07).sum()),
            "realized_volatility_percent": round(float(returns.std() * (252 ** .5) * 100), 2) if not returns.empty else 0,
        }

    def __init__(self, provider=None, settings=None):

        self.provider = provider or DataProvider()
        self.settings = settings

    def analyze(self, symbol: str):

        # --------------------------------------------------
        # Load Historical Data
        # --------------------------------------------------

        df = self.provider.get_data(symbol)

        if df is None:
            return None

        # --------------------------------------------------
        # Calculate Indicators
        # --------------------------------------------------

        df = IndicatorPipeline.run(df)

        # --------------------------------------------------
        # Technical Analysis
        # --------------------------------------------------

        analysis = StockAnalyzer.analyze(symbol, df)

        # --------------------------------------------------
        # Entry Analysis
        # --------------------------------------------------

        entry = EntryAnalyzer.analyze(df)

        # --------------------------------------------------
        # Breakout Analysis
        # --------------------------------------------------

        breakout = BreakoutDetector.analyze(df)

        # --------------------------------------------------
        # Candlestick Confirmation
        # --------------------------------------------------

        candlestick = PatternDetector.detect(df)

        price_action = PriceActionEngine().analyze(df)
        integrated_score = ScoreEngine.integrate_setup_score(analysis.score, price_action)
        analysis.score = integrated_score["score"]
        analysis.recommendation = integrated_score["recommendation"]

        setup_evaluation = SetupEntryEvaluator.evaluate(
            df, analysis, entry, breakout, candlestick, settings=self.settings
        )

        # --------------------------------------------------
        # Final Decision
        # --------------------------------------------------

        decision = DecisionEngine.decide(
            {
                "analysis": analysis,
                "entry": entry,
                "breakout": breakout,
                "setup_evaluation": setup_evaluation,
            }
        )

        # --------------------------------------------------
        # Final Trading Report
        # --------------------------------------------------

        return {

            "analysis": analysis,

            "entry": entry,

            "breakout": breakout,

            "candlestick": candlestick,

            "price_action": price_action,

            "market_quality": self._market_quality(df),

            "decision": decision

            ,"setup_evaluation": setup_evaluation

        }
