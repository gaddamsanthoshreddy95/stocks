"""The supported, safe entry point for analysis, backtesting, and paper trading."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
import re
import logging
from time import monotonic, perf_counter
from threading import Lock, RLock
from typing import Any

import numpy as np
import pandas as pd
from kiteconnect.exceptions import TokenException
from requests.exceptions import RequestException

from src.application.errors import (
    AuthenticationError,
    DataUnavailableError,
    OrderError,
    ValidationError,
)
from src.application.settings import PlatformSettings
from src.backtesting.backtester import Backtester
from src.backtesting.metrics import Metrics
from src.broker.paper_broker import PaperBroker
from src.data_provider.provider import DataProvider
from src.data_provider.kite_data_provider import KiteDataProvider
from src.indicators.pipeline import IndicatorPipeline
from src.position_sizing.position_size import PositionSizingEngine
from src.options.engine.option_engine import OptionEngine
from src.options.kite_option_chain import (
    KiteOptionChainProvider, OptionInstrumentLookupError, OptionQuoteUnavailableError,
)
from src.options.trade_builder import OptionTradeBuilder
from src.options.entry_validator import OptionEntryValidator
from src.options.structure_validator import OptionStructureValidator
from src.options.short_put import ShortPutStrategyEngine
from src.sector.sector_mapper import SectorMapper
from src.trade_plan.trade_plan import TradePlanEngine
from src.trading_engine.engine import TradingEngine
from src.workflow.daily_trading_assistant import DailyTradingAssistant
from src.learning.outcome_repository import OutcomeRepository
from src.historical.current_month_seasonality import CurrentMonthSeasonality
from src.historical.regime_performance import RegimePerformance

logger = logging.getLogger(__name__)


class TradingPlatform:
    """Facade that keeps callers independent from individual analysis engines.

    Live trading is intentionally unavailable here.  Use ``paper_trade`` to
    simulate orders against the latest analysis price.
    """

    def __init__(self, settings: PlatformSettings | None = None, paper_broker: PaperBroker | None = None):
        self.settings = settings or PlatformSettings.from_environment()
        self.provider = (
            KiteDataProvider()
            if self.settings.market_data_source == "kite"
            else DataProvider()
        )
        self.engine = TradingEngine(provider=self.provider, settings=self.settings)
        self.paper_broker = paper_broker or PaperBroker(starting_cash=self.settings.capital)
        self._option_chain_provider = None
        self._option_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
        self._option_cache_lock = RLock()
        self._option_cache_ttl_seconds = 120
        self._analysis_cache: dict[str, dict[str, Any]] = {}
        self._analysis_cache_lock = RLock()

    @staticmethod
    def _symbol(symbol: str) -> str:
        clean = symbol.strip().upper() if isinstance(symbol, str) else ""
        if not clean or len(clean) > 30 or not re.fullmatch(r"[A-Z0-9.&-]+", clean):
            raise ValidationError(
                "symbol must contain letters, numbers, dots, ampersands, or hyphens"
            )
        return clean.removesuffix(".NS")

    @staticmethod
    def _serialize(value: Any) -> Any:
        if is_dataclass(value):
            return {key: TradingPlatform._serialize(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {str(key): TradingPlatform._serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [TradingPlatform._serialize(item) for item in value]
        if isinstance(value, (np.integer, np.floating)):
            return value.item()
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return value

    @staticmethod
    def _option_direction(candidate: dict[str, Any]) -> str:
        """Derive derivative direction from the underlying, not WATCH status."""
        trend = str(candidate.get("analysis_report", {}).get("analysis", {}).get("trend", "")).upper()
        if "BULLISH" in trend:
            return "BULLISH"
        if "BEARISH" in trend:
            return "BEARISH"
        action = str(candidate.get("action", "")).upper()
        if action in {"BUY", "BUY ON DIP"}:
            return "BULLISH"
        if action in {"SELL", "SHORT"}:
            return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def _bearish_option_score(analysis: dict[str, Any], candlestick: dict[str, Any]) -> dict[str, Any]:
        """Score explicit bearish evidence independently from the bullish stock score."""
        trend = str(analysis.get("trend", "")).upper()
        checks = {
            "bearish_trend": "BEARISH" in trend,
            "price_below_ema20": float(analysis.get("current_price", 0)) < float(analysis.get("ema20", 0)),
            "macd_below_signal": float(analysis.get("macd", 0)) < float(analysis.get("macd_signal_line", 0)),
            "rsi_below_50": float(analysis.get("rsi", 100)) < 50,
            "bearish_candle": candlestick.get("signal") == "SELL",
            "adequate_volume": float(analysis.get("relative_volume", 0)) >= .75,
        }
        weights = {"bearish_trend": 35, "price_below_ema20": 20, "macd_below_signal": 20,
                   "rsi_below_50": 10, "bearish_candle": 10, "adequate_volume": 5}
        score = sum(weights[name] for name, passed in checks.items() if passed)
        return {"score": score, "checks": checks,
                "confirmed": all(checks[name] for name in
                                 ("bearish_trend", "price_below_ema20", "macd_below_signal"))}

    def bearish_option_candidates(self, limit: int = 2, minimum_score: int = 60,
                                  option_month: str | None = None) -> dict[str, Any]:
        """Return up to two evidence-backed, structurally valid bearish debit trades."""
        if not isinstance(limit, int) or not 1 <= limit <= 5:
            raise ValidationError("bearish option limit must be between 1 and 5")
        if not isinstance(minimum_score, int) or not 0 <= minimum_score <= 100:
            raise ValidationError("bearish option minimum score must be between 0 and 100")
        symbols = self._universe_symbols()
        reviewed, qualified = 0, []
        rejection_counts: dict[str, int] = {}
        for symbol in symbols:
            try:
                report = self.analyze(symbol)
            except (DataUnavailableError, ValueError, KeyError, TypeError):
                rejection_counts["ANALYSIS_UNAVAILABLE"] = rejection_counts.get("ANALYSIS_UNAVAILABLE", 0) + 1
                continue
            reviewed += 1
            analysis = report["analysis"]
            evidence = self._bearish_option_score(analysis, report["candlestick"])
            if evidence["score"] < minimum_score or not evidence["confirmed"]:
                rejection_counts["BEARISH_EVIDENCE_INSUFFICIENT"] = rejection_counts.get("BEARISH_EVIDENCE_INSUFFICIENT", 0) + 1
                continue
            liquidity = self._stock_liquidity(analysis)
            if liquidity["score"] < self.settings.candidate_min_liquidity_score:
                rejection_counts["STOCK_LIQUIDITY_LOW"] = rejection_counts.get("STOCK_LIQUIDITY_LOW", 0) + 1
                continue
            candidate = {
                "symbol": symbol, "action": "SELL", "current_price": analysis["current_price"],
                "trade_plan": report["trade_plan"],
                "analysis_report": {"analysis": analysis, "entry": report["entry"],
                                    "breakout": report["breakout"], "candlestick": report["candlestick"],
                                    "setup_evaluation": report["setup_evaluation"]},
            }
            option = self._option_trade_plan(candidate, option_month)
            structure = OptionStructureValidator.validate(option, self.settings)
            trade = option.get("trade") or {}
            bearish_structure = trade.get("strategy") in {"Long Put", "Bear Put Spread"}
            approval = {
                "result_type": "OptionTradeApprovalResult",
                "status": "APPROVED" if structure["valid"] and bearish_structure else "REJECTED",
                "approved": bool(structure["valid"] and bearish_structure),
                "rejection_codes": ([] if structure["valid"] and bearish_structure else
                                    structure.get("rejection_codes") or ["NOT_BEARISH_DEBIT_STRUCTURE"]),
            }
            if not approval["approved"]:
                code = approval["rejection_codes"][0]
                rejection_counts[code] = rejection_counts.get(code, 0) + 1
                continue
            qualified.append({
                "symbol": symbol, "bearish_score": evidence["score"], "evidence": evidence,
                "current_price": analysis["current_price"], "trend": analysis["trend"],
                "rsi": analysis["rsi"], "relative_volume": analysis["relative_volume"],
                "levels": {"support": report["entry"].get("support"),
                           "resistance": report["entry"].get("resistance"),
                           "entry": report["trade_plan"].get("entry"),
                           "stop_loss": report["trade_plan"].get("stop_loss"),
                           "target_1": report["trade_plan"].get("target1"),
                           "target_2": report["trade_plan"].get("target2"),
                           "target_3": report["trade_plan"].get("target3")},
                "stock_liquidity": liquidity, "option": option,
                "option_structure": structure, "option_trade_approval": approval,
            })
        qualified.sort(key=lambda row: (row["bearish_score"], row["stock_liquidity"]["score"]), reverse=True)
        return {"requested_limit": limit, "reviewed": reviewed, "minimum_score": minimum_score,
                "candidates": qualified[:limit], "qualified_before_limit": len(qualified),
                "rejection_counts": rejection_counts,
                "message": (f"{min(limit, len(qualified))} validated bearish option candidate(s)."
                            if qualified else "No bearish option candidate passed every validation gate.")}

    @staticmethod
    def _stock_liquidity(analysis: dict[str, Any]) -> dict[str, Any]:
        """Score cash-market liquidity using average daily traded value.

        Relative volume describes today's participation; average traded value
        determines whether a stock is normally practical to enter and exit.
        """
        average_turnover = analysis["current_price"] * analysis["average_volume"]
        turnover_crore = average_turnover / 10_000_000
        if turnover_crore >= 100:
            base, status = 100, "EXCELLENT"
        elif turnover_crore >= 50:
            base, status = 85, "HIGH"
        elif turnover_crore >= 20:
            base, status = 70, "GOOD"
        elif turnover_crore >= 5:
            base, status = 50, "MODERATE"
        else:
            base, status = 25, "LOW"
        rvol = analysis["relative_volume"]
        participation = 10 if rvol >= 1 else 0 if rvol >= .75 else -10
        return {
            "score": max(0, min(100, base + participation)),
            "status": status,
            "average_daily_turnover_crore": round(turnover_crore, 2),
            "relative_volume": round(rvol, 2),
        }

    @staticmethod
    def _trust_score(analysis: dict[str, Any], liquidity: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
        """Conservative F&O suitability score; it is not a manipulation claim."""
        atr_percent = (analysis["atr"] / analysis["current_price"]) * 100
        score = liquidity["score"] * .50
        score += 20 if quality["history_days"] >= 200 else 5
        score += 15 if atr_percent <= 5 else 5 if atr_percent <= 8 else 0
        score += 10 if quality["large_return_days"] <= 2 and quality["large_gap_days"] <= 2 else 0
        score = round(min(100, score), 2)
        flags = []
        if quality["large_return_days"] > 2:
            flags.append("frequent extreme daily moves")
        if quality["large_gap_days"] > 2:
            flags.append("frequent large gaps")
        if atr_percent > 8:
            flags.append("very high ATR volatility")
        return {"score": score, "status": "TRUSTED" if score >= 70 else "CAUTION" if score >= 55 else "EXCLUDE",
                "atr_percent": round(atr_percent, 2), "flags": flags}

    def analyze(self, symbol: str) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        with self._analysis_cache_lock:
            cached = self._analysis_cache.get(symbol)
            if cached is not None:
                return deepcopy(cached)
        try:
            report = self.engine.analyze(symbol)
        except TokenException as exc:
            logger.error("Market-data authentication failed for %s: %s", symbol, exc)
            raise AuthenticationError(
                "Zerodha Kite authentication failed. Update KITE_API_KEY and "
                "generate a fresh KITE_ACCESS_TOKEN before running the platform."
            ) from exc
        except RequestException as exc:
            logger.error("Market-data network request failed for %s: %s", symbol, exc)
            raise DataUnavailableError(
                "Unable to reach Zerodha Kite. Check your network connection and try again."
            ) from exc
        if report is None:
            raise DataUnavailableError(f"No historical data is available for {symbol}")

        trade_plan = TradePlanEngine.generate(report["entry"])
        position_size = PositionSizingEngine.calculate(
            self.settings.capital,
            self.settings.risk_percent,
            trade_plan.entry,
            trade_plan.stop_loss,
        )
        result = self._serialize({
            "symbol": symbol,
            "analysis": report["analysis"],
            "entry": report["entry"],
            "breakout": report["breakout"],
            "candlestick": report["candlestick"],
            "setup_evaluation": report["setup_evaluation"],
            "market_quality": report["market_quality"],
            "decision": report["decision"],
            "trade_plan": trade_plan,
            "position_size": position_size,
        })
        with self._analysis_cache_lock:
            self._analysis_cache[symbol] = deepcopy(result)
        return result

    def backtest(self, symbol: str) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        try:
            dataframe = self.provider.get_data(symbol)
        except TokenException as exc:
            raise AuthenticationError(
                "Zerodha Kite authentication failed. Generate a fresh daily "
                "KITE_ACCESS_TOKEN and try again."
            ) from exc
        except RequestException as exc:
            raise DataUnavailableError(
                "Unable to reach Zerodha Kite. Check your network connection and try again."
            ) from exc
        if dataframe is None or dataframe.empty:
            raise DataUnavailableError(f"No historical data is available for {symbol}")
        trades = Backtester.run(IndicatorPipeline.run(dataframe.copy()))
        return {"symbol": symbol, "metrics": Metrics.summarize(trades), "trades": self._serialize(trades)}

    def _universe_symbols(self) -> list[str]:
        """Use Kite's current F&O universe, or cached files in explicit offline mode."""
        if self.settings.market_data_source == "kite":
            try:
                return self.provider.get_symbols()
            except TokenException as exc:
                raise AuthenticationError(
                    "Zerodha Kite authentication failed. Update KITE_API_KEY and "
                    "generate a fresh KITE_ACCESS_TOKEN before running the platform."
                ) from exc
            except RequestException as exc:
                raise DataUnavailableError(
                    "Unable to reach Zerodha Kite. Check your network connection and try again."
                ) from exc
        from src.config.settings import DATA_FOLDER

        if not Path(DATA_FOLDER).exists():
            return []
        return sorted(path.name.removesuffix(".NS.csv") for path in Path(DATA_FOLDER).glob("*.NS.csv"))

    def enrich_candidate(self, candidate: dict[str, Any], option_month: str | None = None) -> dict[str, Any]:
        """Add expensive long-history and option context to one finalist."""
        candidate = deepcopy(candidate)
        historical = self._historical_context(candidate["symbol"])
        candidate["current_month_seasonality"] = historical["current_month"]
        candidate["regime_history"] = historical["regime"]
        candidate["options"] = self._option_trade_plan(candidate, option_month)
        option_confidence = candidate["options"].get("confidence", 0)
        candidate["probability"] = round(min(
            95,
            candidate["technical_score"] * 0.6
            + candidate["confidence"] * 0.25
            + option_confidence * 0.15,
        ), 2)
        candidate["ai_reasoning"] = [
            candidate["reason"],
            f"Technical score: {candidate['technical_score']}/100.",
            f"Risk/reward: {candidate['risk_reward']}:1.",
            f"Candlestick: {candidate['candlestick']['pattern']} ({candidate['candlestick']['signal']}).",
            *candidate["options"].get("reasons", []),
        ]
        return candidate

    def suggest_stocks(self, limit: int = 5, minimum_score: int = 40,
                       enrich: bool = True) -> dict[str, Any]:
        """Rank the configured stock universe and return actionable setups.

        A candidate must meet the score threshold and receive BUY, BUY ON DIP,
        or WATCH from the decision engine.  Failed analyses are omitted rather
        than allowing a missing data file to stop the entire scan.
        """
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValidationError("limit must be an integer between 1 and 50")
        if not isinstance(minimum_score, int) or not 0 <= minimum_score <= 100:
            raise ValidationError("minimum_score must be an integer between 0 and 100")

        started = perf_counter()
        symbols = self._universe_symbols()
        universe_seconds = perf_counter() - started
        if not symbols:
            raise DataUnavailableError("No stocks are available to screen")

        stage_counts = {"analysis_succeeded": 0, "analysis_failed": 0,
                        "technical_passed": 0, "liquidity_passed": 0,
                        "trust_passed": 0}
        count_lock = Lock()

        def increment(key: str) -> None:
            with count_lock:
                stage_counts[key] += 1

        def evaluate(symbol: str) -> dict[str, Any] | None:
            try:
                report = self.analyze(symbol)
            except (DataUnavailableError, ValueError, KeyError, TypeError):
                increment("analysis_failed")
                return None
            increment("analysis_succeeded")
            analysis = report["analysis"]
            decision = report["decision"]
            if analysis["score"] < minimum_score or decision["action"] not in {"BUY", "BUY ON DIP", "WATCH"}:
                return None
            increment("technical_passed")
            liquidity = self._stock_liquidity(analysis)
            # Avoid names that are hard to enter/exit even if their chart score
            # is attractive. The threshold is deliberately modest for F&O names.
            if liquidity["score"] < self.settings.candidate_min_liquidity_score:
                return None
            increment("liquidity_passed")
            trust = self._trust_score(analysis, liquidity, report["market_quality"])
            if trust["score"] < self.settings.candidate_min_trust_score:
                return None
            increment("trust_passed")
            return {
                "symbol": symbol,
                "action": decision["action"],
                "confidence": decision["confidence"],
                "technical_score": analysis["score"],
                "recommendation": analysis["recommendation"],
                "current_price": analysis["current_price"],
                "stock_liquidity": liquidity,
                "trust": trust,
                "historical_gap_factor": round(1 + min(1, report["market_quality"].get("large_gap_days", 0) / 10), 3),
                "risk_reward": report["trade_plan"]["risk_reward"],
                "candlestick": report["candlestick"],
                "setup_evaluation": report["setup_evaluation"],
                "reason": decision["reason"],
                "trade_plan": report["trade_plan"],
                "entry_report": report["entry"],
                "analysis_report": {
                    "analysis": report["analysis"], "entry": report["entry"],
                    "breakout": report["breakout"], "candlestick": report["candlestick"],
                    "setup_evaluation": report["setup_evaluation"],
                },
                "position_size": report["position_size"],
            }

        candidates = []
        workers = 1 if self.settings.market_data_source == "kite" else min(8, len(symbols))
        scan_started = perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(evaluate, symbol) for symbol in symbols]
            for future in as_completed(futures):
                candidate = future.result()
                if candidate:
                    candidates.append(candidate)
        scan_seconds = perf_counter() - scan_started
        logger.info("Technical scan: %.3fs for %d symbols", scan_seconds, len(symbols))

        action_rank = {"BUY": 2, "BUY ON DIP": 1, "WATCH": 0}
        candidates.sort(
            key=lambda item: (
                action_rank[item["action"]],
                item["technical_score"],
                item["stock_liquidity"]["score"],
                item["risk_reward"],
            ),
            reverse=True,
        )
        top_candidates = candidates[:limit]

        enrichment_started = perf_counter()
        # Only finalists are enriched. Two workers overlap independent I/O
        # without producing an aggressive burst against Kite's API limits.
        if enrich:
            if len(top_candidates) > 1:
                with ThreadPoolExecutor(max_workers=min(2, len(top_candidates))) as executor:
                    top_candidates = list(executor.map(self.enrich_candidate, top_candidates))
            else:
                top_candidates = [self.enrich_candidate(candidate) for candidate in top_candidates]
        enrichment_seconds = perf_counter() - enrichment_started
        logger.info("Historical/option enrichment: %.3fs for %d candidates",
                    enrichment_seconds, len(top_candidates))
        timings = {
            "universe_seconds": round(universe_seconds, 3),
            "technical_scan_seconds": round(scan_seconds, 3),
            "historical_and_options_seconds": round(enrichment_seconds, 3),
            "total_seconds": round(perf_counter() - started, 3),
        }
        logger.info("Suggestion stages completed: %s", timings)
        return {
            "universe_size": len(symbols),
            "market_data_source": self.settings.market_data_source,
            "minimum_score": minimum_score,
            "suggestions": top_candidates,
            "statistics": stage_counts,
            "timings": timings,
            "message": (
                "No stocks currently meet the selected criteria."
                if not candidates
                else f"Ranked candidates using {self.settings.market_data_source} daily data."
            ),
        }

    def _option_trade_plan(self, candidate: dict[str, Any], option_month: str | None = None) -> dict[str, Any]:
        key = (
            candidate["symbol"], round(float(candidate["current_price"]), 1),
            self.settings.trading_strategy_mode, self._option_direction(candidate), option_month,
        )
        with self._option_cache_lock:
            cached = self._option_cache.get(key)
            if cached and monotonic() - cached[0] < self._option_cache_ttl_seconds:
                return deepcopy(cached[1])
            result = self._option_trade_plan_uncached(candidate, option_month)
            self._option_cache[key] = (monotonic(), deepcopy(result))
            return result

    def _option_trade_plan_uncached(self, candidate: dict[str, Any], option_month: str | None = None) -> dict[str, Any]:
        """Enrich only final candidates with live option-chain intelligence."""
        if self.settings.market_data_source != "kite":
            return {"available": False, "error_type": "DATA_SOURCE",
                    "rejection": {"code": "LIVE_DATA_REQUIRED", "category": "DATA",
                                  "reason": "Option analysis requires MARKET_DATA_SOURCE=kite."},
                    "reason": "Option analysis requires MARKET_DATA_SOURCE=kite."}
        try:
            if self._option_chain_provider is None:
                instruments = (
                    self.provider.get_nfo_instruments()
                    if hasattr(self.provider, "get_nfo_instruments") else None
                )
                self._option_chain_provider = KiteOptionChainProvider(
                    self.provider.provider.kite, instruments=instruments
                )
            chain = self._option_chain_provider.get_chain(
                candidate["symbol"], candidate["current_price"], expiry_month=option_month
            )
            short_put = {"available": False, "rejection_code": "STRATEGY_MODE_DISABLED",
                         "rejection_reasons": ["Short-Put mode is disabled."]}
            if self.settings.trading_strategy_mode in {"short_put", "both"}:
                chains = self._option_chain_provider.get_chains(
                    candidate["symbol"], candidate["current_price"],
                    self.settings.short_put_min_dte, self.settings.short_put_max_dte,
                    expiry_month=option_month,
                )
                portfolio = self.paper_broker.portfolio()
                invested = float(portfolio.get("invested_cost", 0))
                candidate_sector = SectorMapper().get_sector(candidate["symbol"])
                sector_invested = sum(
                    float(position.get("quantity", 0)) * float(position.get("average_price", 0))
                    for position in portfolio.get("positions", [])
                    if SectorMapper().get_sector(position.get("symbol", "")) == candidate_sector
                )
                exposure = {
                    "portfolio_exposure_percent": invested / self.settings.capital * 100,
                    "sector_exposure_percent": sector_invested / self.settings.capital * 100,
                    "correlated_exposure_percent": None,
                    "available_capital": min(self.settings.option_capital, float(portfolio.get("cash", self.settings.option_capital))),
                    "broker_margin_available": False,
                }
                short_put = ShortPutStrategyEngine.evaluate(
                    candidate["symbol"], candidate["analysis_report"], chains, self.settings,
                    exposure_context=exposure,
                )
                if short_put.get("available") and short_put.get("strategy") == "BULL_PUT_SPREAD":
                    legs = [short_put.get("sold_leg"), short_put.get("hedge_leg")]
                    try:
                        orders = [{
                            "exchange": "NFO", "tradingsymbol": leg["symbol"],
                            "transaction_type": leg["side"], "variety": "regular",
                            "product": "NRML", "order_type": "LIMIT", "quantity": leg["quantity"],
                            "price": leg["premium"],
                        } for leg in legs if leg]
                        margin = self.provider.provider.kite.basket_order_margins(orders, consider_positions=True)
                        total_margin = float((margin.get("final") or {}).get("total") or 0)
                        if total_margin > 0 and short_put.get("lots", 0) > 0:
                            exposure["broker_margin_per_lot"] = total_margin / short_put["lots"]
                            exposure["broker_margin_available"] = True
                            short_put = ShortPutStrategyEngine.evaluate(
                                candidate["symbol"], candidate["analysis_report"], chains, self.settings,
                                exposure_context=exposure,
                            )
                    except (TokenException, RequestException, KeyError, TypeError, ValueError, AttributeError) as exc:
                        logger.warning("Broker margin unavailable for %s: %s: %s",
                                       candidate["symbol"], exc.__class__.__name__, exc)
            direction = self._option_direction(candidate)
            analysis = OptionEngine().analyze(chain, direction=direction)
            if direction == "NEUTRAL":
                return self._serialize({
                    "available": False,
                    "reason": "Underlying direction is not confirmed.",
                    "strategy": "Wait",
                    "analysis_strategy": analysis.suggested_strategy,
                    "confidence": analysis.confidence,
                    "pcr": analysis.pcr,
                    "rejection": {"code": "DIRECTION_UNCONFIRMED", "category": "SETUP",
                                  "reason": "Underlying direction is not confirmed."},
                    "short_put": short_put,
                    "option_decision": {
                        "direction": direction, "selected": "Wait", "candidates": [],
                        "equity_independent": True,
                    },
                })
            trade = OptionTradeBuilder.build(
                chain,
                analysis.suggested_strategy or "Wait",
                direction,
                support=candidate["trade_plan"]["stop_loss"],
                resistance=candidate["trade_plan"]["target1"],
                risk_budget=self.settings.option_risk_per_trade,
                capital_available=self.settings.option_capital,
            )
            # Builders must return a canonical payload, but normalize older or
            # partial rejection dictionaries defensively at this boundary.
            if not isinstance(trade, dict):
                trade = {}
            trade.setdefault("available", False)
            trade.setdefault("strategy", "Wait")
            trade.setdefault("analysis_strategy", analysis.suggested_strategy or "Wait")
            trade.setdefault("expiry", chain.expiry)
            trade.setdefault("legs", [])
            trade.setdefault("reason", "No executable option structure was produced.")
            trade.setdefault("rejection", {
                "code": "OPTION_STRUCTURE_UNAVAILABLE", "category": "STRUCTURE",
                "reason": trade["reason"],
            })
            validation = OptionEntryValidator.validate(chain, trade, direction, self.settings)
            rejection = trade.get("rejection")
            if trade.get("available") and not validation["approved"]:
                rejection = {"code": "ENTRY_VALIDATION_FAILED", "category": "EXECUTION",
                             "reason": "; ".join(validation["reasons"])}
            # Bullish premium-selling plans have first refusal. Directional
            # debit spreads are a fallback only when no seller plan is valid.
            candidates = []
            if short_put.get("strategy"):
                candidates.append({
                    "strategy": short_put["strategy"],
                    "available": bool(short_put.get("available")),
                    "source": "SHORT_PUT_ENGINE",
                    "rejection_code": short_put.get("rejection_code"),
                })
            candidates.append({
                "strategy": trade.get("strategy", "Wait"),
                "available": bool(trade.get("available") and validation["approved"]),
                "source": "DIRECTIONAL_OPTION_ENGINE",
            })
            selected_strategy = next(
                (item["strategy"] for item in candidates if item["available"]), "Wait"
            )
            return self._serialize({
                "available": trade.get("available", False),
                "reason": trade.get("reason"),
                "expiry": chain.expiry,
                "strategy": trade.get("strategy", "Wait"),
                "analysis_strategy": analysis.suggested_strategy,
                "direction": direction,
                "confidence": analysis.confidence,
                "pcr": analysis.pcr,
                "max_pain": analysis.max_pain,
                "strongest_support": analysis.strongest_support,
                "strongest_resistance": analysis.strongest_resistance,
                "reasons": analysis.reasons,
                "trade": trade,
                "entry_validation": validation,
                "rejection": rejection,
                "short_put": short_put,
                "option_decision": {
                    "direction": direction,
                    "selected": selected_strategy,
                    "candidates": candidates,
                    "equity_independent": True,
                },
            })
        except TokenException as exc:
            return {"available": False, "error_type": "AUTHENTICATION",
                    "rejection": {"code": "AUTHENTICATION_FAILED", "category": "DATA", "reason": f"Kite token error: {exc}"},
                    "reason": f"Kite token error: {exc}", "reasons": []}
        except RequestException as exc:
            return {"available": False, "error_type": "NETWORK",
                    "rejection": {"code": "NETWORK_FAILED", "category": "DATA", "reason": f"Kite network error: {exc}"},
                    "reason": f"Kite network error: {exc}", "reasons": []}
        except OptionInstrumentLookupError as exc:
            logger.warning("Option instrument lookup failed for %s: %s", candidate["symbol"], exc)
            return {"available": False, "error_type": "INSTRUMENT_LOOKUP",
                    "rejection": {"code": "OPTION_DATA_UNAVAILABLE", "category": "DATA", "reason": str(exc)},
                    "reason": str(exc), "reasons": [], "short_put": {"available": False,
                    "rejection_code": "OPTION_DATA_UNAVAILABLE", "rejection_reasons": [str(exc)]}}
        except OptionQuoteUnavailableError as exc:
            logger.warning("Option quote unavailable for %s: %s", candidate["symbol"], exc)
            return {"available": False, "error_type": "QUOTE_UNAVAILABLE",
                    "rejection": {"code": "OPTION_DATA_UNAVAILABLE", "category": "DATA", "reason": str(exc)},
                    "reason": str(exc), "reasons": [], "short_put": {"available": False,
                    "rejection_code": "OPTION_DATA_UNAVAILABLE", "rejection_reasons": [str(exc)]}}
        except ValueError as exc:
            logger.warning("Invalid option data for %s: %s", candidate["symbol"], exc)
            return {"available": False, "error_type": "OPTION_DATA",
                    "rejection": {"code": "OPTION_DATA_INVALID", "category": "DATA", "reason": str(exc)},
                    "reason": str(exc), "reasons": []}
        except Exception as exc:
            logger.exception("Unexpected option provider error for %s", candidate["symbol"])
            return {"available": False, "error_type": exc.__class__.__name__,
                    "rejection": {"code": "OPTION_PROVIDER_ERROR", "category": "DATA", "reason": repr(exc)},
                    "reason": repr(exc), "reasons": []}

    def _historical_context(self, symbol: str) -> dict[str, Any]:
        try:
            history = self.provider.get_long_history(symbol, period="10y")
            return {"current_month": CurrentMonthSeasonality.analyze(history),
                    "regime": RegimePerformance.analyze(history, "BULLISH")}
        except Exception as exc:
            reason = f"{exc.__class__.__name__}: {exc}"
            return {"current_month": CurrentMonthSeasonality.unavailable(
                        date.today().strftime("%B").upper(), reason),
                    "regime": {"available": False, "regime": "UNAVAILABLE",
                               "sample_count": 0, "reason": reason}}

    def paper_trade(self, symbol: str, side: str, quantity: int | None = None) -> dict[str, Any]:
        side = side.strip().upper() if isinstance(side, str) else ""
        if side not in {"BUY", "SELL"}:
            raise ValidationError("side must be BUY or SELL")
        analysis = self.analyze(symbol)
        suggested_quantity = analysis["position_size"]["quantity"]
        quantity = suggested_quantity if quantity is None else quantity
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValidationError("quantity must be a positive integer")
        try:
            position_before = next((item for item in self.paper_broker.positions()
                                    if item["symbol"] == analysis["symbol"]), None)
            order = self.paper_broker.place_order(
                analysis["symbol"], side, quantity, price=analysis["analysis"]["current_price"]
            )
            outcomes = OutcomeRepository()
            if side == "BUY" and position_before is None:
                order["recommendation_id"] = outcomes.record_paper_entry(order, analysis)
            elif side == "SELL":
                position_after = next((item for item in self.paper_broker.positions()
                                       if item["symbol"] == analysis["symbol"]), None)
                if position_after is None:
                    order["recommendation_id"] = outcomes.close_paper_trade(
                        analysis["symbol"], order["filled_price"], order["order_id"]
                    )
            return order
        except ValueError as exc:
            raise OrderError(str(exc)) from exc

    def portfolio(self) -> dict[str, Any]:
        return self._serialize(self.paper_broker.portfolio())

    def daily_report(self, limit: int = 5, minimum_score: int = 40,
                     option_month: str | None = None,
                     excluded_symbols: set[str] | None = None) -> dict[str, Any]:
        """Generate the final ranked daily trade report.

        This is a research/paper-trading recommendation only.  It never sends
        live orders, even when Kite is the configured data source.
        """
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValidationError("limit must be an integer between 1 and 20")
        if not isinstance(minimum_score, int) or not 0 <= minimum_score <= 100:
            raise ValidationError("minimum_score must be an integer between 0 and 100")
        if option_month is not None:
            if not isinstance(option_month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", option_month):
                raise ValidationError("option_month must use YYYY-MM format")
        # Streamlit caches this platform instance across button clicks.  A new
        # report must therefore invalidate the prior analysis snapshot and,
        # for Kite, bypass the on-disk TTL while today's candle is changing.
        with self._analysis_cache_lock:
            self._analysis_cache.clear()
        begin_live_refresh = getattr(self.provider, "begin_live_refresh", None)
        end_live_refresh = getattr(self.provider, "end_live_refresh", None)
        live_refresh_started = False
        try:
            if begin_live_refresh is not None:
                begin_live_refresh(self._universe_symbols())
                live_refresh_started = True
            assistant = (DailyTradingAssistant(
                self, option_month=option_month, excluded_symbols=excluded_symbols
            ) if excluded_symbols else DailyTradingAssistant(self, option_month=option_month))
            report = assistant.generate(
                limit, minimum_score
            )
            return self._serialize(report)
        finally:
            if live_refresh_started and end_live_refresh is not None:
                end_live_refresh()

    def record_trade_outcome(self, recommendation_id: str, won: bool,
                             return_percent: float | None = None,
                             exit_price: float | None = None,
                             mfe_percent: float | None = None,
                             mae_percent: float | None = None) -> dict[str, Any]:
        if not isinstance(recommendation_id, str) or not recommendation_id:
            raise ValidationError("recommendation_id is required")
        if not isinstance(won, bool):
            raise ValidationError("won must be true or false")
        if return_percent is not None and not isinstance(return_percent, (int, float)):
            raise ValidationError("return_percent must be numeric")
        for name, value in (("exit_price", exit_price), ("mfe_percent", mfe_percent),
                            ("mae_percent", mae_percent)):
            if value is not None and not isinstance(value, (int, float)):
                raise ValidationError(f"{name} must be numeric")
        saved = OutcomeRepository().record_outcome(
            recommendation_id, won, return_percent, exit_price, mfe_percent, mae_percent
        )
        if not saved:
            raise ValidationError("recommendation_id was not found")
        return {"recommendation_id": recommendation_id, "recorded": True}
