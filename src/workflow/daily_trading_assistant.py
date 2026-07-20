"""The canonical end-to-end daily trading recommendation workflow."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
import logging
from time import perf_counter
from typing import Any

from src.sector.sector_mapper import SectorMapper
from src.news.analysis_service import NewsAnalysisService
from src.workflow.context_enrichment import ContextEnrichment
from src.learning.outcome_repository import OutcomeRepository
from src.position_sizing.position_size import PositionSizingEngine
from src.trade_plan.trade_plan import TradePlanEngine
from src.options.short_put import ShortPutStrategyEngine
from src.workflow.decision_policy import (
    classify_setup, market_alignment, normalize_market_regime,
    option_confidence_status, pcr_adjustment, market_risk_scale,
    combine_strategy_eligibility, risk_reward_tier, adaptive_market_policy,
    expected_value,
)

logger = logging.getLogger(__name__)


class DailyTradingAssistant:
    """Transform ranked analyses into the architecture's final daily output.

    The workflow consumes the public platform facade so data-source, paper
    trading, validation, and error behaviour remain consistent everywhere.
    """

    def __init__(self, platform):
        self.platform = platform
        self.sectors = SectorMapper()
        self.outcomes = OutcomeRepository()

    @staticmethod
    def _stars(score: float, maximum: float = 100) -> str:
        filled = max(0, min(5, round((score / maximum) * 5)))
        return "★" * filled + "☆" * (5 - filled)

    @staticmethod
    def _confidence_grade(score: float, status: str) -> dict[str, str]:
        if status == "REJECTED" or score < 50:
            return {"grade": "F", "label": "Avoid"}
        if status == "WATCHLIST":
            return ({"grade": "C", "label": "Average"} if score >= 65
                    else {"grade": "D", "label": "Weak"})
        bands = ((90, "A+", "Excellent Trade"), (80, "A", "Very Good"),
                 (70, "B", "Good"), (60, "C", "Average"),
                 (50, "D", "Weak"))
        for floor_score, grade, label in bands:
            if score >= floor_score:
                return {"grade": grade, "label": label}
        return {"grade": "F", "label": "Avoid"}

    @staticmethod
    def _option_rejection(option: dict[str, Any]) -> dict[str, Any]:
        """Normalize JSON null and absent rejection payloads to a dictionary."""
        return option.get("rejection") or {}

    @staticmethod
    def _short_put_candidate(plan: dict[str, Any]) -> dict[str, Any]:
        """Normalize explicit JSON null from rejected short-put plans."""
        candidate = plan.get("candidate")
        return candidate if isinstance(candidate, dict) else {}

    @staticmethod
    def _technical_probability(candidate: dict[str, Any], news_score: float = 0) -> float:
        # A transparent heuristic, capped below certainty.  It is deliberately
        # labelled as an estimate until calibrated against recorded outcomes.
        score = candidate["technical_score"]
        confidence = candidate["confidence"]
        breakout = candidate.get("breakout", {}).get("score", 0)
        return round(min(95, max(0, score * 0.60 + confidence * 0.25 + breakout * 3 + news_score * 0.10)), 2)

    @staticmethod
    def _available_weighted_score(components: list[tuple[float, float, bool]]) -> float:
        """Average only available evidence; unavailable context has zero impact."""
        available = [(score, weight) for score, weight, is_available in components if is_available]
        total_weight = sum(weight for _, weight in available)
        return sum(score * weight for score, weight in available) / total_weight if total_weight else 0.0

    def _trade_readiness(self, analysis: dict, candidate: dict, alignment: dict,
                         sector_data: dict, option: dict, setup: str,
                         seasonality: dict | None = None,
                         regime_history: dict | None = None) -> dict:
        """Expose the exact conditions that would promote a watchlist setup."""
        breakout = analysis["breakout"].get("confirmed", False)
        entry_confirmed = analysis.get("setup_evaluation", {}).get("stage_2", {}).get("eligible", False)
        reversal_needed = setup in {"BULLISH_PULLBACK", "BEARISH_BOUNCE"}
        reversal_seen = analysis["candlestick"].get("signal") in {"BUY", "SELL"}
        checks = [
            {"name": "technical_score", "passed": candidate["technical_score"] >= 55,
             "detail": f"{candidate['technical_score']}/100 (minimum 55)"},
            {"name": "entry_confirmation", "passed": entry_confirmed,
             "detail": "all confirmation checks passed" if entry_confirmed else "waiting for EMA20, bullish candle, volume above 1.2x and MACD confirmation"},
            {"name": "volume", "passed": analysis["analysis"]["relative_volume"] >= .75,
             "detail": f"relative volume {analysis['analysis']['relative_volume']:.2f}x (minimum 0.75x)"},
            {"name": "market_alignment", "passed": alignment["status"] != "CONFLICT",
             "detail": alignment["status"]},
            {"name": "sector_support", "passed": not sector_data.get("available") or sector_data.get("score", 50) >= 50,
             "detail": sector_data.get("rating", "UNAVAILABLE")},
            {"name": "risk_reward", "passed": candidate["trade_plan"]["risk_reward"] >= self.platform.settings.equity_min_risk_reward,
             "detail": f"1:{candidate['trade_plan']['risk_reward']} (minimum 1:{self.platform.settings.equity_min_risk_reward})"},
            {"name": "option_context", "passed": option_confidence_status(option.get("confidence")) not in {"CONFLICT", "UNRELIABLE"},
             "detail": ("supportive option evidence" if option.get("available")
                        else "option evidence unavailable — neutral")},
        ]
        seasonality = seasonality or {}
        checks.append({
            "name": "current_month_history",
            "passed": (seasonality.get("sample_quality") in {"ROBUST", "LIMITED"}
                       and seasonality.get("score", 50) >= 45),
            "detail": (f"{seasonality.get('month_name', 'month')} win rate "
                       f"{seasonality.get('win_rate_percent', 'N/A')}% across "
                       f"{seasonality.get('sample_years', 0)} years ({seasonality.get('sample_quality', 'INSUFFICIENT')})"),
        })
        regime_history = regime_history or {}
        checks.append({
            "name": "regime_history",
            "passed": (regime_history.get("sample_quality") in {"ROBUST", "LIMITED"}
                       and (regime_history.get("win_rate_percent") or 0) >= 50),
            "detail": (f"{regime_history.get('regime', 'UNAVAILABLE')} regime: "
                       f"{regime_history.get('win_rate_percent', 'N/A')}% historical win rate across "
                       f"{regime_history.get('sample_count', 0)} samples"),
        })
        passed = sum(item["passed"] for item in checks)
        percentage = round(passed * 100 / len(checks)) if checks else 0
        classification = ("EXECUTE" if percentage >= 90 else "PREPARE_ORDER" if percentage >= 75
                          else "WATCH_INTRADAY" if percentage >= 60 else "IGNORE")
        return {"ready": classification == "EXECUTE", "passed": passed, "total": len(checks),
                "percentage": percentage, "classification": classification,
                "checks": checks, "next_actions": [item["detail"] for item in checks if not item["passed"]]}

    @staticmethod
    def _rank_sectors(reviewed: list[dict], sector_strength: dict) -> list[dict]:
        grouped: dict[str, list[dict]] = {}
        for trade in reviewed:
            if trade["sector"] != "UNKNOWN":
                grouped.setdefault(trade["sector"], []).append(trade)
        rows = []
        for sector, items in grouped.items():
            context = sector_strength.get(sector, {})
            candidate_score = sum(item["ai_score"] for item in items) / len(items)
            index_available = context.get("available", False)
            index_score = context.get("score", 50) if index_available else 50
            # Candidate quality remains useful when a Yahoo sector index is unavailable.
            rank_score = candidate_score * .6 + index_score * .4
            rows.append({"sector": sector, "score": round(rank_score, 2),
                         "index_score": index_score, "index_available": index_available,
                         "rating": context.get("rating", "UNAVAILABLE"),
                         "candidate_count": len(items),
                         "average_candidate_score": round(candidate_score, 2)})
        rows.sort(key=lambda item: (item["score"], item["candidate_count"]), reverse=True)
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        return rows

    @staticmethod
    def _conflict_gate(candidate: dict[str, Any], option: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
        """Reject only critical news, execution, and data-quality failures."""
        conflicts = []
        critical_conflicts = []
        if news.get("sentiment") == "BEARISH" or news.get("trade_impact") == "BLOCK":
            critical_conflicts.append("AI news analysis identified material bearish trade risk")
        if news.get("events"):
            critical_conflicts.append("news risk event: " + ", ".join(news["events"]))
        if candidate["action"] in {"BUY", "BUY ON DIP"}:
            if option.get("pcr") is not None and option["pcr"] < .8:
                conflicts.append(f"bearish PCR ({option['pcr']})")
            if option.get("confidence") is not None and option["confidence"] < 50:
                conflicts.append(f"weak option-chain confidence ({option['confidence']}%) — score adjustment only")
            entry_validation = option.get("entry_validation", {})
            if entry_validation and not entry_validation.get("approved", True):
                # Option execution can block the derivative structure, but it
                # must never reject an otherwise valid stock trade.
                conflicts.extend(entry_validation.get("reasons", []))
        conflicts = critical_conflicts + conflicts
        return {"approved": not critical_conflicts, "conflicts": conflicts,
                "critical_conflicts": critical_conflicts,
                "decision": "APPROVED" if not critical_conflicts else "EXCLUDED"}

    @staticmethod
    def _news_not_requested() -> dict[str, Any]:
        return {
            "available": False, "requested": False, "score": 0,
            "sentiment": "NEUTRAL", "confidence": 0, "article_count": 0,
            "events": [], "headlines": [], "analysis_method": "NOT_REQUESTED",
            "score_impact": 0, "trade_impact": "NONE",
            "reasons": ["News analysis was deferred because this stock was not in the final news shortlist."],
            "collection_state": "NOT_FETCHED", "analysis_state": "NOT_RUN",
        }

    @staticmethod
    def _normalize_news_state(news: dict[str, Any]) -> dict[str, Any]:
        """Separate headline collection from AI sentiment availability."""
        count = int(news.get("article_count") or len(news.get("headlines", [])))
        requested = news.get("requested", True)
        collection = ("NOT_FETCHED" if not requested else
                      "FETCHED" if count > 0 else "FETCH_FAILED_OR_EMPTY")
        method = news.get("analysis_method", "UNAVAILABLE")
        analysis = ("ANALYZED" if news.get("available") else
                    "FAILED" if count > 0 and method in {"AI_UNAVAILABLE", "UNAVAILABLE"} else
                    "NOT_RUN")
        return {**news, "collection_state": news.get("collection_state", collection),
                "analysis_state": news.get("analysis_state", analysis)}

    def _trade(self, rank: int, candidate: dict[str, Any], market: dict[str, Any],
               sector_strength: dict[str, Any], news: dict[str, Any] | None = None,
               relative_strength: dict[str, Any] | None = None,
               record_recommendation: bool = True) -> dict[str, Any]:
        analysis = self.platform.analyze(candidate["symbol"])
        sector = self.sectors.get_sector(candidate["symbol"])
        sector_data = sector_strength.get(sector, {"available": False, "score": 50, "rating": "UNAVAILABLE"})
        relative_strength = relative_strength or ContextEnrichment(
            self.platform.settings.market_data_source == "kite"
        ).relative_strength(candidate["symbol"])
        option = candidate.get("options", {"available": False})
        seasonality = candidate.get("current_month_seasonality", {})
        regime_history = candidate.get("regime_history", {})
        direction = "BULLISH" if candidate["action"] in {"BUY", "BUY ON DIP", "WATCH"} else "BEARISH"
        normalized_regime = normalize_market_regime(market.get("regime", "UNAVAILABLE"), market.get("confidence", 0))
        alignment = market_alignment(normalized_regime, market.get("confidence", 0), direction)
        technical = analysis["analysis"]
        contextual_breakout_probability = round(
            (20 if technical["relative_volume"] >= 1.2 else 10 if technical["relative_volume"] >= .9 else 0)
            + (20 if technical["macd"] > technical["macd_signal_line"] else 0)
            + (20 if relative_strength.get("available") and relative_strength.get("score", 0) >= 60 else 10 if not relative_strength.get("available") else 0)
            + (20 if sector_data.get("available") and sector_data.get("score", 0) >= 60 else 10 if not sector_data.get("available") else 0)
            + (20 if alignment["status"] == "ALIGNED" else 10 if alignment["status"] in {"UNCERTAIN", "NEUTRAL"} else 0),
            2,
        )
        if technical["relative_volume"] < .9 or technical["macd"] <= technical["macd_signal_line"]:
            contextual_breakout_probability = min(contextual_breakout_probability, 60)
        plan = asdict(TradePlanEngine.generate(
            candidate.get("entry_report", analysis["entry"]),
            breakout_probability=contextual_breakout_probability,
        ))
        risk_scale = market_risk_scale(
            market.get("confidence", 0), market.get("available", False), alignment["status"]
        )
        base_risk = PositionSizingEngine.calculate(
            self.platform.settings.capital,
            self.platform.settings.risk_percent,
            plan["entry"],
            plan["stop_loss"],
        )
        scaled_quantity = int(base_risk.get("quantity", 0) * risk_scale)
        scaled_risk = {**base_risk, "quantity": scaled_quantity,
                       "capital_used": round(base_risk.get("capital_used", 0) * risk_scale, 2),
                       "risk_amount": round(base_risk.get("risk_amount", 0) * risk_scale, 2),
                       "actual_risk": round(base_risk.get("actual_risk", 0) * risk_scale, 2),
                       "market_confidence_scale": risk_scale,
                       "effective_risk_percent": round(self.platform.settings.risk_percent * risk_scale, 3)}
        option_trade = option.get("trade", {})
        scaled_option_risk = round(self.platform.settings.option_risk_per_trade * risk_scale, 2)
        if option.get("available") and option_trade.get("maximum_loss", 0) > scaled_option_risk:
            reason = "Maximum option loss exceeds the market-confidence-adjusted risk budget."
            option = {**option, "available": False, "reason": reason,
                      "rejection": {"code": "SCALED_RISK_BUDGET_EXCEEDED", "category": "RISK",
                                    "reason": reason, "maximum_loss": option_trade["maximum_loss"],
                                    "available_budget": scaled_option_risk}}
        if news is None:
            news = self._news_not_requested()
        if self.platform.settings.market_data_source != "kite":
            news = {
                "available": False, "score": 0, "sentiment": "NEUTRAL",
                "confidence": 0, "article_count": 0, "events": [], "headlines": [],
                "analysis_method": "UNAVAILABLE", "score_impact": 0,
                "reasons": ["News analysis requires live mode (MARKET_DATA_SOURCE=kite)."],
                "requested": False,
            }
        news = self._normalize_news_state(news)
        short_put = option.get("short_put", {"available": False, "rejection_code": "OPTION_DATA_UNAVAILABLE",
                                             "rejection_reasons": ["Short-Put analysis is unavailable."]})
        short_put = ShortPutStrategyEngine.apply_context(
            short_put, alignment, sector_data, news, self.platform.settings,
        )
        option = {**option, "short_put": short_put}
        short_put_approved = bool(short_put.get("available"))
        validation = self._conflict_gate(candidate, option, news)
        adjustments = news["score"] * 0.15 if news.get("available") else 0
        if sector_data.get("available"):
            adjustments += (sector_data["score"] - 50) * 0.10
        if relative_strength["available"]:
            adjustments += (relative_strength["score"] - 50) * 0.10
        if market["available"]:
            adjustments += market["score"] * 0.05
        option_status = option_confidence_status(option.get("confidence"))
        option_rejection_code = self._option_rejection(option).get("code")
        budget_only_option_failure = option_rejection_code in {
            "RISK_BUDGET_EXCEEDED", "CAPITAL_REQUIRED_EXCEEDED", "SCALED_RISK_BUDGET_EXCEEDED",
        }
        option_score = 50 + pcr_adjustment(option.get("pcr"), direction)
        if option_status == "CONFIRMED":
            option_score += 15
        elif option_status == "CONFLICT":
            option_score -= 10
        elif option_status == "UNRELIABLE":
            option_score -= 15
        unified_score = self._available_weighted_score([
            (candidate["technical_score"], .55, True),
            (alignment["score"], .15, True),
            (sector_data.get("score", 50), .10, bool(sector_data.get("available"))),
            (relative_strength.get("score", 50), .10, bool(relative_strength.get("available"))),
            (candidate["stock_liquidity"]["score"], .06, True),
            (option_score, .04, option.get("confidence") is not None),
        ])
        unified_score = round(min(100, max(0, unified_score - alignment["penalty"])), 2)
        # Seasonality is contextual: at most +/-1.5 points and only with five
        # or more observations. Regime history carries more weight (up to 3).
        seasonality_adjustment = 0.0
        if seasonality.get("sample_years", 0) >= 5:
            seasonality_adjustment = max(-1.5, min(1.5, (seasonality.get("score", 50) - 50) * .03))
            unified_score = round(min(100, max(0, unified_score + seasonality_adjustment)), 2)
        regime_adjustment = 0.0
        if regime_history.get("sample_count", 0) >= 15 and regime_history.get("win_rate_percent") is not None:
            regime_adjustment = max(-3, min(3, (regime_history["win_rate_percent"] - 50) * .12))
            unified_score = round(min(100, max(0, unified_score + regime_adjustment)), 2)
        setup_evaluation = analysis.get("setup_evaluation", {})
        setup = setup_evaluation.get("stage_1", {}).get("category") or classify_setup(
            analysis["analysis"]["trend"], analysis["analysis"]["rsi_signal"]
        )
        entry_eligible = setup_evaluation.get("stage_2", {}).get("eligible", False)
        probability = self._technical_probability(
            {**candidate, "breakout": analysis["breakout"]},
            news["score"] if news.get("available") else 0,
        )
        calibrated_probability = self.outcomes.contextual_probability(setup, normalized_regime)
        if calibrated_probability is None:
            calibrated_probability = self.outcomes.calibrated_probability(candidate["action"])
        if calibrated_probability is not None:
            probability = round((probability * 0.4) + (calibrated_probability * 0.6), 2)
        expectancy = expected_value(probability, plan["expected_reward"], plan["risk"])
        reasons = [
            candidate["reason"],
            f"Trend: {analysis['analysis']['trend']}.",
            f"RSI: {analysis['analysis']['rsi']:.1f}.",
            f"Relative volume: {analysis['analysis']['relative_volume']:.2f}x.",
            f"Breakout: {'confirmed' if analysis['breakout']['confirmed'] else 'not confirmed'}.",
            f"Candlestick: {analysis['candlestick']['pattern']}.",
        ]
        reasons.extend(option.get("reasons", []))
        reasons.extend(news["reasons"])
        rr_tier = risk_reward_tier(
            unified_score,
            plan["risk_reward"],
            self.platform.settings.equity_min_risk_reward,
            self.platform.settings.equity_b_grade_min_risk_reward,
            self.platform.settings.equity_watchlist_min_risk_reward,
        )
        minimum_equity_rr = rr_tier["minimum"]
        low_risk_reward = not rr_tier["approved"]
        readiness = self._trade_readiness(analysis, candidate, alignment, sector_data, option, setup,
                                          seasonality, regime_history)
        market_policy = adaptive_market_policy(normalized_regime)
        readiness_approved = readiness["percentage"] >= market_policy["readiness_minimum"]
        confirmation_approved = entry_eligible or (
            not market_policy["confirmation_required"] and readiness_approved
        )
        strategy_eligibility = combine_strategy_eligibility(
            confirmation_approved, plan["risk_reward"], minimum_equity_rr, short_put_approved,
        )
        # Reversal candidates remain observable while their entry develops;
        # inadequate R:R still makes them ineligible for an actual trade.
        risk_reward_rejects_setup = (not rr_tier["watchlist_eligible"]
                                     and setup != "REVERSAL CANDIDATE"
                                     and not strategy_eligibility["short_put_approved"])
        equity_execution_failure = (
            risk_reward_rejects_setup or plan["stop_loss"] <= 0 or scaled_quantity <= 0
        )
        critical_failure = (
            not validation["approved"]
            or (equity_execution_failure and not short_put_approved)
        )
        if critical_failure or unified_score < 55:
            status = "REJECTED"
        elif (not short_put_approved and (not confirmation_approved
              or not readiness_approved
              or unified_score < market_policy["trade_score_minimum"] or low_risk_reward
              or setup in {"BULLISH_PULLBACK", "BEARISH_BOUNCE", "REVERSAL CANDIDATE"}
              or (market.get("confidence", 0) >= 65 and alignment["status"] == "CONFLICT"))):
            status = "WATCHLIST"
        else:
            status = "TRADE"
        status_reasons = list(validation["critical_conflicts"])
        if low_risk_reward:
            status_reasons.append(
                f"{rr_tier['grade']}-grade risk/reward {plan['risk_reward']}:1 is below the {minimum_equity_rr} minimum."
            )
            status_reasons.extend(plan.get("diagnostics", []))
        if plan["stop_loss"] <= 0:
            status_reasons.append("Stop-loss is invalid.")
        if scaled_quantity <= 0:
            status_reasons.append("Market-confidence-adjusted position size is below one share.")
        if unified_score < 55:
            status_reasons.append(f"Final score {unified_score} is below 55.")
        if status == "WATCHLIST":
            if unified_score < market_policy["trade_score_minimum"]:
                status_reasons.append(f"Final score {unified_score} has not reached the {market_policy['trade_score_minimum']} {market_policy['profile']} threshold.")
            if not confirmation_approved:
                missing = setup_evaluation.get("stage_2", {}).get("missing", [])
                status_reasons.append("Entry confirmation missing: " + ", ".join(missing))
            if option_status in {"CONFLICT", "UNRELIABLE"} and not budget_only_option_failure:
                rejection = self._option_rejection(option)
                status_reasons.append(rejection.get("reason", f"Option confirmation is {option_status}."))
            if market.get("confidence", 0) >= 65 and alignment["status"] == "CONFLICT":
                status_reasons.append("Trade direction conflicts with a confirmed market regime.")
        equity_eligible = (
            strategy_eligibility["equity_approved"]
            and plan["stop_loss"] > 0
            and scaled_quantity > 0
            and validation["approved"]
            and status == "TRADE"
        )
        eligibility = {"eligible": status == "TRADE", "status": status,
                       "blocking_reasons": status_reasons,
                       "model_confidence_does_not_imply_eligibility": True}
        trade = {
            "rank": rank,
            "symbol": candidate["symbol"],
            "sector": sector,
            "current_price": candidate["current_price"],
            "ai_score": unified_score,
            "technical_score": candidate["technical_score"],
            "confidence": candidate["confidence"],
            "model_confidence": {"decision_confidence": candidate["confidence"],
                                 "estimated_probability": probability,
                                 "calibrated_probability": calibrated_probability},
            "trade_eligibility": eligibility,
            "equity_eligibility": {
                "eligible": equity_eligible,
                "status": "APPROVED" if equity_eligible else "REJECTED",
                "reason": None if equity_eligible else f"Equity reward/risk or entry confirmation failed ({plan['risk_reward']}:1).",
            },
            "short_put_eligibility": {
                "eligible": short_put_approved,
                "status": "APPROVED" if short_put_approved else "REJECTED",
                "rejection_code": short_put.get("rejection_code"),
                "reasons": short_put.get("rejection_reasons", []),
            },
            "trade_readiness": readiness,
            "market_policy": market_policy,
            "expected_value": expectancy,
            "current_month_seasonality": {**seasonality, "score_adjustment": round(seasonality_adjustment, 2)},
            "regime_history": {**regime_history, "score_adjustment": round(regime_adjustment, 2)},
            "risk_policy": {"configured_risk_percent": self.platform.settings.risk_percent,
                            "effective_risk_percent": scaled_risk["effective_risk_percent"],
                            "market_confidence": market.get("confidence", 0), "position_scale": risk_scale},
            "risk_reward_policy": rr_tier,
            "option_budget_policy": {"capital_available": self.platform.settings.option_capital,
                                     "risk_per_trade": self.platform.settings.option_risk_per_trade,
                                     "confidence_adjusted_risk": scaled_option_risk,
                                     "stock_eligibility_independent": True},
            "probability": probability,
            "strategy": (short_put.get("strategy") if short_put_approved
                         else option.get("strategy") if option.get("available")
                         else "Swing Breakout" if analysis["breakout"]["confirmed"]
                         else candidate["action"]),
            "strategy_priority": ["SHORT_PUT", "BULL_PUT_SPREAD", "CASH_SECURED_PUT", "BULL_CALL_SPREAD"],
            "confidence_grade": self._confidence_grade(unified_score, status),
            "status": status,
            "setup": setup,
            "action": "WAIT FOR CONFIRMATION" if not entry_eligible else candidate["action"],
            "market_alignment": alignment,
            "option_status": option_status,
            "status_reasons": status_reasons,
            "time_frame": "3-5 trading days",
            "technical": {
                "trend": analysis["analysis"]["trend"],
                "trend_stars": self._stars(candidate["technical_score"]),
                "momentum": setup_evaluation.get("momentum_label", analysis["analysis"]["rsi_signal"]),
                "setup_evaluation": setup_evaluation,
                "volume": analysis["analysis"]["volume_signal"],
                "relative_strength": "NOT AVAILABLE",
                "breakout": analysis["breakout"],
                "candlestick": analysis["candlestick"],
            },
            "levels": {
                "support": analysis["entry"]["support"],
                "resistance": analysis["entry"]["resistance"],
                "entry": plan["entry"],
                "stop_loss": plan["stop_loss"],
                "target_1": plan["target1"],
                "target_2": plan["target2"],
                "target_3": plan["target3"],
                "risk_reward": plan["risk_reward"],
                "expected_reward": plan.get("expected_reward", plan["reward"]),
                "nearest_target_reward": plan.get("nearest_target_reward", plan["reward"]),
                "target_basis": plan.get("target_basis", "NEAREST_RESISTANCE"),
                "breakout_probability": plan.get("breakout_probability", 0),
                "target_diagnostics": plan.get("diagnostics", []),
            },
            "risk": scaled_risk,
            "stock_liquidity": candidate["stock_liquidity"],
            "trust": candidate["trust"],
            "option_strategy": option,
            "short_put_strategy": short_put,
            "news": news,
            "market_context": {**market, "regime": normalized_regime},
            "sector_context": {"sector": sector, **sector_data},
            "relative_strength": relative_strength,
            "ai_reasoning": reasons,
            "recommendation": candidate["action"],
            "calibrated_probability": calibrated_probability,
            "validation": validation,
        }
        if status == "TRADE" and record_recommendation:
            trade["recommendation_id"] = self.outcomes.record_recommendation(trade)
        else:
            trade["recommendation_id"] = None
        return trade

    def generate(self, limit: int = 5, minimum_score: int = 40) -> dict[str, Any]:
        started = perf_counter()
        news_preload_executor = None
        news_preload_future = None
        if self.platform.settings.market_data_source == "kite":
            news_preload_executor = ThreadPoolExecutor(max_workers=1)
            news_preload_future = news_preload_executor.submit(NewsAnalysisService.preload_model)
        # Ranking and risk optimize different properties, so always send the
        # top 20 (configurable up to 30) through risk/context review.
        enrichment_limit = min(30, max(self.platform.settings.ranking_shortlist_size, limit + 5))
        # Technical screening is cheap after the daily candle cache is warm.
        # Expensive 10-year history and option-chain enrichment is deferred
        # until shared market context has been collected.
        try:
            ranked = self.platform.suggest_stocks(
                limit=enrichment_limit, minimum_score=minimum_score, enrich=False
            )
            deferred_enrichment = True
        except TypeError as exc:
            if "enrich" not in str(exc):
                raise
            ranked = self.platform.suggest_stocks(
                limit=enrichment_limit, minimum_score=minimum_score
            )
            deferred_enrichment = False
        screening_seconds = perf_counter() - started
        logger.info("Daily stage screening: %.3fs", screening_seconds)

        context_started = perf_counter()
        enrichment = ContextEnrichment(self.platform.settings.market_data_source == "kite")
        market_context, sector_strength = enrichment.market_and_sectors()
        candidates = ranked["suggestions"]
        if deferred_enrichment:
            enrich_started = perf_counter()
            if len(candidates) > 1:
                with ThreadPoolExecutor(max_workers=min(2, len(candidates))) as executor:
                    candidates = list(executor.map(self.platform.enrich_candidate, candidates))
            else:
                candidates = [self.platform.enrich_candidate(candidate) for candidate in candidates]
            ranked["suggestions"] = candidates
            logger.info("Daily stage finalist enrichment: %.3fs", perf_counter() - enrich_started)
        # Relative-strength downloads are independent and safely bounded.
        if len(candidates) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as executor:
                strengths = list(executor.map(
                    lambda item: enrichment.relative_strength(item["symbol"]), candidates
                ))
        else:
            strengths = [enrichment.relative_strength(item["symbol"]) for item in candidates]
        strength_by_symbol = {
            candidate["symbol"]: strength for candidate, strength in zip(candidates, strengths)
        }
        context_seconds = perf_counter() - context_started
        logger.info("Daily stage market/relative strength: %.3fs", context_seconds)

        preliminary_started = perf_counter()
        preliminary = [
            self._trade(index, candidate, market_context, sector_strength,
                        news=self._news_not_requested(),
                        relative_strength=strength_by_symbol[candidate["symbol"]],
                        record_recommendation=False)
            for index, candidate in enumerate(candidates, start=1)
        ]
        preliminary.sort(key=lambda item: (item["expected_value"]["risk_multiple"], item["ai_score"]), reverse=True)
        preliminary_seconds = perf_counter() - preliminary_started
        logger.info("Daily stage preliminary review: %.3fs", preliminary_seconds)

        preload_wait_started = perf_counter()
        preload_result = (
            news_preload_future.result() if news_preload_future is not None
            else {"available": False, "model_load_seconds": 0, "wall_seconds": 0}
        )
        news_preload_wait_seconds = perf_counter() - preload_wait_started
        if news_preload_executor is not None:
            news_preload_executor.shutdown(wait=False)
        logger.info(
            "News model preload: load=%.3fs wall=%.3fs wait_after_screening=%.3fs",
            preload_result.get("model_load_seconds", 0),
            preload_result.get("wall_seconds", 0), news_preload_wait_seconds,
        )

        # News is expensive, so request it only for stocks that survived all
        # technical, market, liquidity, risk, and execution gates. A three-name
        # buffer allows bearish material news to remove a finalist.
        news_started = perf_counter()
        news_target_symbols = [
            trade["symbol"] for trade in preliminary if trade["status"] != "REJECTED"
        ][:min(len(preliminary), limit + 3)]
        news_by_symbol: dict[str, dict[str, Any]] = {}
        results: list[dict[str, Any]] = []
        if self.platform.settings.market_data_source == "kite" and news_target_symbols:
            with ThreadPoolExecutor(max_workers=min(4, len(news_target_symbols))) as executor:
                results = list(executor.map(NewsAnalysisService.analyze, news_target_symbols))
            news_by_symbol = dict(zip(news_target_symbols, results))
        news_seconds = perf_counter() - news_started
        news_component_timings = [result.get("timings", {}) for result in results]
        news_network_seconds = sum(item.get("network_seconds", 0) for item in news_component_timings)
        news_model_load_seconds = (
            preload_result.get("model_load_seconds", 0)
            + sum(item.get("model_load_seconds", 0) for item in news_component_timings)
        )
        news_inference_seconds = sum(item.get("inference_seconds", 0) for item in news_component_timings)
        logger.info("Daily stage targeted news: %.3fs for %d stocks",
                    news_seconds, len(news_target_symbols))
        logger.info(
            "Daily news timings: model_load=%.3fs inference=%.3fs network=%.3fs wall=%.3fs",
            news_model_load_seconds, news_inference_seconds, news_network_seconds, news_seconds,
        )

        final_review_started = perf_counter()
        reviewed = [
            self._trade(index, candidate, market_context, sector_strength,
                        news=news_by_symbol.get(candidate["symbol"], self._news_not_requested()),
                        relative_strength=strength_by_symbol[candidate["symbol"]])
            for index, candidate in enumerate(candidates, start=1)
        ]
        reviewed.sort(key=lambda item: (item["expected_value"]["risk_multiple"], item["ai_score"]), reverse=True)
        final_review_seconds = perf_counter() - final_review_started
        logger.info("Daily stage final review: %.3fs", final_review_seconds)
        rejected = [trade for trade in reviewed if trade["status"] == "REJECTED"]
        watchlist = [trade for trade in reviewed if trade["status"] == "WATCHLIST"]
        trades = [trade for trade in reviewed if trade["status"] == "TRADE"][:limit]
        for index, trade in enumerate(trades, start=1):
            trade["rank"] = index
        for index, trade in enumerate(watchlist, start=1):
            trade["rank"] = index
        sectors = Counter(trade["sector"] for trade in trades if trade["sector"] != "UNKNOWN")
        bullish = sum(1 for trade in trades if trade["recommendation"] in {"BUY", "BUY ON DIP"})
        market = normalize_market_regime(market_context["regime"], market_context["confidence"]) if market_context["available"] else ("BULLISH" if bullish > len(trades) / 2 else "NEUTRAL")
        average_probability = round(sum(item["probability"] for item in trades) / len(trades), 2) if trades else 0
        average_watchlist_probability = round(sum(item["probability"] for item in watchlist) / len(watchlist), 2) if watchlist else None
        option_strategies = Counter(
            item["option_strategy"].get("strategy") for item in trades
            if item["option_strategy"].get("available") and item["option_strategy"].get("strategy")
        )
        short_put_plans = [item.get("short_put_strategy", {}) for item in reviewed]
        approved_short_puts = [plan for plan in short_put_plans if plan.get("available")]
        approved_short_puts_with_candidate = [
            plan for plan in approved_short_puts
            if self._short_put_candidate(plan)
        ]
        short_put_rejections = Counter(
            plan.get("rejection_code") for plan in short_put_plans
            if not plan.get("available") and plan.get("rejection_code")
        )
        stage_counts = ranked.get("statistics", {})
        historical_learning = self.outcomes.learning_summary()
        sector_ranking = self._rank_sectors(reviewed, sector_strength)
        context_statistics = {
            "news": {
                "passed": sum(1 for item in reviewed if item["news"].get("available") and item["news"].get("sentiment") != "BEARISH" and item["news"].get("trade_impact") != "BLOCK" and not item["news"].get("events")),
                "failed": sum(1 for item in reviewed if item["news"].get("available") and (item["news"].get("sentiment") == "BEARISH" or item["news"].get("trade_impact") == "BLOCK" or item["news"].get("events"))),
                "unavailable": sum(1 for item in reviewed if not item["news"].get("available")),
            },
            "sector": {
                "passed": sum(1 for item in reviewed if item["sector_context"].get("available") and item["sector_context"].get("score", 50) >= 50),
                "failed": sum(1 for item in reviewed if item["sector_context"].get("available") and item["sector_context"].get("score", 50) < 50),
                "unavailable": sum(1 for item in reviewed if not item["sector_context"].get("available")),
            },
            "relative_strength": {
                "passed": sum(1 for item in reviewed if item["relative_strength"].get("available") and item["relative_strength"].get("score", 50) >= 50),
                "failed": sum(1 for item in reviewed if item["relative_strength"].get("available") and item["relative_strength"].get("score", 50) < 50),
                "unavailable": sum(1 for item in reviewed if not item["relative_strength"].get("available")),
            },
        }
        trust_passed = stage_counts.get("trust_passed", len(ranked["suggestions"]))
        risk_valid = sum(1 for item in reviewed if item["risk_reward_policy"]["watchlist_eligible"] and item["levels"]["stop_loss"] > 0)
        filter_stages = [
            {"stage": "universe", "input": ranked["universe_size"], "passed": ranked["universe_size"], "rejected": 0},
            {"stage": "analysis", "input": ranked["universe_size"], "passed": stage_counts.get("analysis_succeeded", 0), "rejected": stage_counts.get("analysis_failed", 0)},
            {"stage": "technical", "input": stage_counts.get("analysis_succeeded", 0), "passed": stage_counts.get("technical_passed", 0), "rejected": max(0, stage_counts.get("analysis_succeeded", 0) - stage_counts.get("technical_passed", 0))},
            {"stage": "liquidity", "input": stage_counts.get("technical_passed", 0), "passed": stage_counts.get("liquidity_passed", 0), "rejected": max(0, stage_counts.get("technical_passed", 0) - stage_counts.get("liquidity_passed", 0))},
            {"stage": "trust", "input": stage_counts.get("liquidity_passed", 0), "passed": trust_passed, "rejected": max(0, stage_counts.get("liquidity_passed", 0) - trust_passed)},
            {"stage": "ranking_shortlist", "input": trust_passed, "passed": len(reviewed), "rejected": 0,
             "deferred": max(0, trust_passed - len(reviewed))},
            {"stage": "context_review", "input": len(reviewed), "passed": len(reviewed), "rejected": 0},
            {"stage": "risk", "input": len(reviewed), "passed": risk_valid, "rejected": len(reviewed) - risk_valid},
            {"stage": "final_trade", "input": len(reviewed), "passed": len(trades), "rejected": len(reviewed) - len(trades)},
        ]
        timings = {
            "screening_seconds": round(screening_seconds, 3),
            "market_and_relative_strength_seconds": round(context_seconds, 3),
            "preliminary_review_seconds": round(preliminary_seconds, 3),
            "targeted_news_seconds": round(news_seconds, 3),
            "news_model_load_seconds": round(news_model_load_seconds, 3),
            "news_model_preload_wait_seconds": round(news_preload_wait_seconds, 3),
            "news_inference_seconds": round(news_inference_seconds, 3),
            "news_network_seconds": round(news_network_seconds, 3),
            "final_review_seconds": round(final_review_seconds, 3),
            "total_seconds": round(perf_counter() - started, 3),
            "news_stocks_requested": len(news_target_symbols),
            "candidates_enriched": len(candidates),
        }
        logger.info("Daily report stages completed: %s", timings)
        return {
            "report_type": "daily_trading_assistant",
            "date": date.today().isoformat(),
            "market": {**market_context, "regime": market, "confidence": market_context["confidence"] if market_context["available"] else round(sum(item["confidence"] for item in trades) / len(trades), 2) if trades else 0},
            "trades": trades,
            "watchlist": watchlist,
            "sector_ranking": sector_ranking,
            "filter_stages": filter_stages,
            "context_statistics": context_statistics,
            "historical_learning": historical_learning,
            "timings": timings,
            "rejected": [
                {"symbol": trade["symbol"], "technical_score": trade["technical_score"],
                 "reasons": trade["status_reasons"] or trade["validation"]["conflicts"]}
                for trade in rejected
            ],
            "summary": {
                "market": market,
                "best_sector": sector_ranking[0]["sector"] if sector_ranking else "NOT AVAILABLE",
                "best_option_strategy": option_strategies.most_common(1)[0][0] if option_strategies else "NOT AVAILABLE",
                "stocks_scanned": ranked["universe_size"],
                "stocks_qualified": len(ranked["suggestions"]),
                "analysis_succeeded": stage_counts.get("analysis_succeeded", len(ranked["suggestions"])),
                "analysis_failed": stage_counts.get("analysis_failed", max(0, ranked["universe_size"] - len(ranked["suggestions"]))),
                "technical_passed": stage_counts.get("technical_passed", len(ranked["suggestions"])),
                "liquidity_passed": stage_counts.get("liquidity_passed", len(ranked["suggestions"])),
                "trust_passed": trust_passed,
                "context_reviewed": len(reviewed),
                "risk_valid": risk_valid,
                "market_aligned": sum(1 for item in reviewed if item["market_alignment"]["status"] == "ALIGNED"),
                "watchlisted": len(watchlist),
                "option_confirmed": sum(1 for item in reviewed if item["option_status"] == "CONFIRMED"),
                "rejected": len(rejected),
                "conflicts_rejected": sum(1 for item in rejected if item["validation"]["critical_conflicts"]),
                "stocks_shortlisted": len(trades) + len(watchlist),
                "trades_generated": len(trades),
                "short_put_reviewed": len(short_put_plans),
                "short_put_approved": len(approved_short_puts),
                "cash_secured_put_approved": sum(1 for plan in approved_short_puts if plan.get("strategy") == "CASH_SECURED_PUT"),
                "bull_put_spread_approved": sum(1 for plan in approved_short_puts if plan.get("strategy") == "BULL_PUT_SPREAD"),
                "short_put_rejected": len(short_put_plans) - len(approved_short_puts),
                "average_probability_otm": (lambda values: round(sum(values) / len(values), 2) if values else None)([
                    self._short_put_candidate(plan).get("probability_otm")
                    for plan in short_put_plans
                    if self._short_put_candidate(plan).get("probability_otm") is not None
                ]),
                "average_otm_distance": round(
                    sum(plan["candidate"]["strike_distance_percent"] for plan in approved_short_puts_with_candidate)
                    / len(approved_short_puts_with_candidate), 2
                ) if approved_short_puts_with_candidate else None,
                "average_short_put_return_on_risk": round(sum(plan["return_on_risk_percent"] for plan in approved_short_puts) / len(approved_short_puts), 2) if approved_short_puts else None,
                "most_common_short_put_rejection": short_put_rejections.most_common(1)[0][0] if short_put_rejections else None,
                "average_probability": average_probability if trades else None,
                "average_watchlist_probability": average_watchlist_probability,
                "highest_ai_score": trades[0]["symbol"] if trades else None,
                "market_risk": "LOW" if market == "BULLISH" else "MODERATE",
                "recommendation": "Take only bullish, risk-defined trades." if market == "BULLISH" else "Wait for clearer market alignment.",
            },
            "limitations": [
                "Probability is a transparent heuristic, not a guaranteed or calibrated win rate.",
                "Relative strength, sector, and news are shown only when a live data integration is available.",
                "Recommendations are for research and paper trading; no live order is submitted.",
            ],
        }
