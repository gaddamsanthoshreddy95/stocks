"""
AI Trading Pipeline

Central orchestration layer for Alphatrace.

Current Pipeline
----------------
1. Historical Data
2. Historical Statistics
3. Historical Intelligence
4. Qualification
5. Market Regime
6. Strategy Recommendation
7. Options Intelligence
8. Strategy Selection
9. Strike Optimization
10. Trade Planning
11. Decision Engine
12. Confidence Engine
13. Portfolio Optimization
14. Trade Explanation

Future Engines
--------------
• Market Data
• Technical
• Candlestick
• Breakout
• Volume
• Market Structure
• Global Markets
• News
• Sentiment
• Sector
• Candidate Selection
• Ranking
• Evidence
"""

from __future__ import annotations

from typing import Any
from typing import Dict

from src.ai.confidence_engine import ConfidenceEngine
from src.ai.decision_engine import DecisionEngine
from src.ai.market_regime import MarketRegimeEngine
from src.ai.portfolio_optimizer import PortfolioOptimizer
from src.ai.trade_explainer import TradeExplainer

from src.historical.intelligence import HistoricalIntelligenceEngine
from src.historical.qualification import HistoricalQualificationEngine
from src.historical.repository import HistoricalRepository
from src.historical.statistics.statistics_engine import (
    HistoricalStatisticsEngine,
)

from src.options.intelligence import OptionsIntelligenceEngine
from src.options.strategy_selector import OptionsStrategySelector
from src.options.strike_optimizer import StrikeOptimizer
from src.options.trade_planner import TradePlanner

from src.strategy.recommender import StrategyRecommendationEngine


class AITradingPipeline:

    def __init__(self):

        self.repository = HistoricalRepository()

        self.statistics = HistoricalStatisticsEngine()

        self.intelligence = HistoricalIntelligenceEngine()

        self.qualification = HistoricalQualificationEngine()

        self.market = MarketRegimeEngine()

        self.strategy = StrategyRecommendationEngine()

        self.option_intelligence = OptionsIntelligenceEngine()

        self.option_selector = OptionsStrategySelector()

        self.optimizer = StrikeOptimizer()

        self.trade_planner = TradePlanner()

        self.decision = DecisionEngine()

        self.confidence = ConfidenceEngine()

        self.portfolio = PortfolioOptimizer(
            capital=100000
        )

        self.explainer = TradeExplainer()

    # =====================================================
    # Historical
    # =====================================================

    def _historical_analysis(
        self,
        symbol: str,
    ):

        dataframe = self.repository.get_history(symbol)

        statistics = self.statistics.calculate(
            dataframe
        )

        intelligence = self.intelligence.from_dataframe(
            dataframe
        )

        qualification = self.qualification.qualify(
            intelligence
        )

        return (
            dataframe,
            statistics,
            intelligence,
            qualification,
        )

    # =====================================================
    # Market
    # =====================================================

    def _market_analysis(
        self,
        dataframe,
        indicators,
    ):

        return self.market.evaluate(
            dataframe,
            indicators,
        )

    # =====================================================
    # Strategy
    # =====================================================

    def _strategy_analysis(
        self,
        intelligence,
    ):

        return self.strategy.recommend(
            intelligence
        )

    # =====================================================
    # Options
    # =====================================================

    def _options_analysis(
        self,
        intelligence,
        option_chain,
    ):

        option_analysis = (
            self.option_intelligence.analyze(
                intelligence,
                option_chain,
            )
        )

        option_strategy = (
            self.option_selector.select(
                option_analysis
            )
        )

        strike = self.optimizer.optimize(
            option_chain
        )

        trade_plan = (
            self.trade_planner.create_plan(
                option_strategy,
                strike,
            )
        )

        return (
            option_analysis,
            option_strategy,
            strike,
            trade_plan,
        )

    # =====================================================
    # AI Decision
    # =====================================================

    def _decision_analysis(
        self,
        intelligence,
        strategy,
        option_analysis,
        regime,
    ):

        decision = self.decision.evaluate(

            historical_score=intelligence.overall_score,

            strategy_score=strategy.confidence,

            options_score=option_analysis.combined_score,

            market_score=regime.score,

        )

        confidence = self.confidence.evaluate(

            historical_score=intelligence.overall_score,

            strategy_score=strategy.confidence,

            market_score=regime.score,

            options_score=option_analysis.combined_score,

            liquidity_score=intelligence.liquidity_score,

            volatility_score=100
            - intelligence.volatility,

            signal_agreement=80,

            prediction_accuracy=75,

        )

        return decision, confidence

    # =====================================================
    # Portfolio
    # =====================================================

    def _portfolio_analysis(
        self,
        symbol,
        dataframe,
        strike,
        intelligence,
        confidence,
    ):

        return self.portfolio.optimize(
            [
                {
                    "symbol": symbol,
                    "confidence": confidence.confidence,
                    "expected_return": strike.expected_return,
                    "volatility": intelligence.volatility,
                    "price": dataframe.Close.iloc[-1],
                }
            ]
        )

    # =====================================================
    # Explanation
    # =====================================================

    def _generate_explanation(
        self,
        symbol,
        intelligence,
        strategy,
        option_analysis,
        decision,
        confidence,
        portfolio,
    ):

        return self.explainer.explain(

            symbol=symbol,

            historical=intelligence.__dict__,

            strategy=strategy.__dict__,

            options=option_analysis.__dict__,

            decision=decision.__dict__,

            confidence=confidence.__dict__,

            portfolio=self.portfolio.summary(
                portfolio
            ),
        )

    # =====================================================
    # Pipeline
    # =====================================================

    def run(
        self,
        symbol: str,
        indicators: Dict[str, Any],
        option_chain: Dict[str, Any],
    ) -> Dict[str, Any]:

        (
            dataframe,
            statistics,
            intelligence,
            qualification,
        ) = self._historical_analysis(symbol)

        regime = self._market_analysis(
            dataframe,
            indicators,
        )

        strategy = self._strategy_analysis(
            intelligence
        )

        (
            option_analysis,
            option_strategy,
            strike,
            trade_plan,
        ) = self._options_analysis(
            intelligence,
            option_chain,
        )

        decision, confidence = (
            self._decision_analysis(
                intelligence,
                strategy,
                option_analysis,
                regime,
            )
        )

        portfolio = self._portfolio_analysis(
            symbol,
            dataframe,
            strike,
            intelligence,
            confidence,
        )

        explanation = self._generate_explanation(
            symbol,
            intelligence,
            strategy,
            option_analysis,
            decision,
            confidence,
            portfolio,
        )

        return {

            "qualification": qualification,

            "market_regime": regime,

            "historical": intelligence,

            "statistics": statistics,

            "strategy": strategy,

            "option_strategy": option_strategy,

            "options": option_analysis,

            "trade_plan": trade_plan,

            "decision": decision,

            "confidence": confidence,

            "portfolio": portfolio,

            "explanation": explanation,

        }
