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
from src.news.ai_sentiment import AISentimentAnalyzer
from src.workflow.context_enrichment import ContextEnrichment
from src.learning.outcome_repository import OutcomeRepository
from src.position_sizing.position_size import PositionSizingEngine
from src.risk.adverse_move import AdverseMoveRisk
from src.trade_plan.trade_plan import TradePlanEngine
from src.options.short_put import ShortPutStrategyEngine
from src.workflow.decision_policy import (
    classify_setup, market_alignment, normalize_market_regime,
    option_confidence_status, pcr_adjustment, market_risk_scale,
    combine_strategy_eligibility, risk_reward_tier, adaptive_market_policy,
    expected_value,
)
from src.event_risk import EventRiskService
from src.event_risk.service import DailyEventContext
from src.market_data.commodity_provider import CommodityProvider
from src.learning.recommendation_journal import RecommendationJournal
from src.workflow.final_decision import (
    EntryConfirmationResult, FinalConsistencyValidator, FinalDecisionEngine,
)
from src.workflow.stock_selection import classify_entry_timing
from src.options.structure_validator import OptionStructureValidator
from uuid import uuid4

logger = logging.getLogger(__name__)


class DailyTradingAssistant:
    """Transform ranked analyses into the architecture's final daily output.

    The workflow consumes the public platform facade so data-source, paper
    trading, validation, and error behaviour remain consistent everywhere.
    """

    def __init__(self, platform, option_month: str | None = None,
                 excluded_symbols: set[str] | None = None):
        self.platform = platform
        self.option_month = option_month
        self.sectors = SectorMapper()
        self.outcomes = OutcomeRepository()
        self.completed_outcomes = self.outcomes.learning_summary().get("completed_outcomes", 0)
        commodity_fetcher = (CommodityProvider().get_snapshot
                             if platform.settings.market_data_source == "kite" else None)
        self.event_service = EventRiskService(platform.settings, commodity_fetcher=commodity_fetcher)
        self.event_context = DailyEventContext([], "UNAVAILABLE", [], {})
        self.journal = RecommendationJournal()
        self.run_id = ""
        self.excluded_symbols = {
            str(symbol).upper().removesuffix(".NS") for symbol in (excluded_symbols or set())
        }

    @staticmethod
    def _stars(score: float, maximum: float = 100) -> str:
        filled = max(0, min(5, round((score / maximum) * 5)))
        return "★" * filled + "☆" * (5 - filled)

    def _quality_grade(self, score: float) -> dict[str, str]:
        settings = self.platform.settings
        bands = (
            (settings.quality_grade_a_plus, "A+", "Exceptional"),
            (settings.quality_grade_a, "A", "Excellent"),
            (settings.quality_grade_b_plus, "B+", "Very Good"),
            (settings.quality_grade_b, "B", "Good"),
            (settings.quality_grade_c_plus, "C+", "Above Average"),
            (settings.quality_grade_c, "C", "Average"),
        )
        for floor_score, grade, label in bands:
            if score >= floor_score:
                return {"grade": grade, "label": label}
        return {"grade": "D", "label": "Weak"}

    @staticmethod
    def _apply_sector_limit(reviewed: list[dict[str, Any]], maximum: int) -> int:
        """Keep the highest-ranked trades and transparently defer sector duplicates."""
        counts: Counter = Counter()
        deferred = 0
        for trade in reviewed:
            if trade.get("status") != "TRADE":
                continue
            sector = str(trade.get("sector") or "UNKNOWN")
            if sector == "UNKNOWN" or counts[sector] < maximum:
                counts[sector] += 1
                continue
            deferred += 1
            reason = f"Sector limit reached: only {maximum} final trade(s) are allowed from {sector}."
            trade["status"] = "WATCHLIST"
            trade["final_action"] = trade["action"] = trade["recommendation"] = "WATCHLIST"
            trade["trade_eligibility"] = {
                **trade["trade_eligibility"], "eligible": False, "status": "WATCHLIST",
                "blocking_reasons": [*trade["trade_eligibility"].get("blocking_reasons", []), reason],
            }
            trade["final_decision"] = {
                **trade["final_decision"], "action": "WATCHLIST", "executable": False,
                "reasons": [*trade["final_decision"].get("reasons", []), reason],
            }
            trade["risk"] = {**trade["risk"], "quantity": 0, "capital_used": 0,
                             "risk_amount": 0, "actual_risk": 0}
            trade["entry_selection"] = {**trade["entry_selection"], "status": "AVOID",
                                         "reason": reason}
            trade["selection_status"], trade["selection_reason"] = "AVOID", reason
            trade["option_trade_approval"] = {
                **trade["option_trade_approval"], "status": "REJECTED", "approved": False,
                "rejection_codes": [*trade["option_trade_approval"].get("rejection_codes", []),
                                    "SECTOR_CONCENTRATION_LIMIT"],
            }
            trade["option_execution_valid"] = False
            trade["option_context"] = {**trade["option_context"], "execution": "REJECTED"}
            FinalConsistencyValidator.validate(trade)
        return deferred

    @staticmethod
    def _band(value: float, bands: tuple[tuple[float, float], ...], default: float) -> float:
        return next((score for floor, score in bands if value >= floor), default)

    def _quality_score(self, analysis: dict, candidate: dict, relative_strength: dict,
                       plan: dict, expectancy: dict, probability: float,
                       momentum: str) -> dict[str, Any]:
        """Measure intrinsic setup quality without execution/context penalties."""
        trend = (analysis["analysis"].get("trend") or "").upper().replace(" ", "_")
        momentum = (momentum or "").upper().replace("_", " ")
        if "STRONG_BULLISH" in trend and "STRONG BULLISH" in momentum:
            trend_momentum = 100
        elif "STRONG_BULLISH" in trend and "BULLISH" in momentum:
            trend_momentum = 90
        elif "BULLISH" in trend and "BULLISH" in momentum:
            trend_momentum = 80
        elif "NEUTRAL" in trend and "BULLISH" in momentum:
            trend_momentum = 65
        elif ("BULLISH" in trend) != ("BULLISH" in momentum):
            trend_momentum = 45
        else:
            trend_momentum = 20
        relative_score = (float(relative_strength["score"])
                          if relative_strength.get("available")
                          and relative_strength.get("score") is not None else None)
        rr_score = self._band(float(plan["risk_reward"]),
                              ((2.5, 100), (2.0, 90), (1.7, 80), (1.5, 70),
                               (1.3, 60), (1.2, 50), (1.0, 35)), 10)
        ev_score = self._band(float(expectancy.get("risk_multiple", 0)),
                              ((2.0, 100), (1.5, 90), (1.0, 80), (.75, 70),
                               (.5, 55), (.25, 40)), 20)
        probability_score = self._band(float(probability),
                                       ((92, 100), (88, 90), (84, 80), (80, 70),
                                        (75, 60), (70, 50)), 30)
        liquidity = candidate["stock_liquidity"]
        trust = candidate["trust"]
        liquidity_status = (liquidity.get("status") or "").upper()
        trust_score = float(trust.get("score", 0))
        liquidity_trust = (100 if liquidity_status == "EXCELLENT" and trust_score >= 95
                           else 90 if liquidity_status in {"HIGH", "EXCELLENT"} and trust_score >= 90
                           else 70 if liquidity.get("score", 0) >= 40 and trust_score >= 55 else 40)
        factors = {
            "technical_score": float(candidate["technical_score"]),
            "trend_and_momentum_quality": trend_momentum,
            "relative_strength_quality": relative_score,
            "risk_reward_quality": rr_score,
            "expected_value_quality": ev_score,
            "setup_probability_quality": probability_score,
            "liquidity_and_trust_quality": liquidity_trust,
        }
        weights = {"technical_score": .25, "trend_and_momentum_quality": .20,
                   "relative_strength_quality": .15, "risk_reward_quality": .15,
                   "expected_value_quality": .10, "setup_probability_quality": .10,
                   "liquidity_and_trust_quality": .05}
        available_weight = sum(weights[name] for name, value in factors.items() if value is not None)
        weighted = sum(float(value) * weights[name] for name, value in factors.items() if value is not None)
        score = round(min(100, max(0, weighted / available_weight)), 2) if available_weight else 0.0
        grade = self._quality_grade(score)
        return {"score": score, **grade, "factors": factors}

    @staticmethod
    def _bullish_stock_selection_filters(*, plan: dict, setup: str, technical: dict,
                                         entry_quality: dict, adverse: dict,
                                         relative_strength: dict, sector: dict,
                                         liquidity: dict, settings) -> dict[str, Any]:
        """Hard stock-selection gates, separate from portfolio and position policy."""
        entry = float(plan.get("entry") or 0)
        stop = float(plan.get("stop_loss") or 0)
        stop_percent = ((entry - stop) * 100 / entry if entry > 0 and 0 < stop < entry
                        else float("inf"))
        target_first = adverse.get("probability_target_before_adverse_barrier")
        no_gap = adverse.get("probability_no_overnight_gap_beyond_barrier")
        rs_score = relative_strength.get("score")
        sector_score = sector.get("score")
        breakout = setup == "BREAKOUT"
        minimum_volume = (settings.entry_confirmation_relative_volume if breakout
                          else settings.entry_min_relative_volume)
        checks = [
            {"name": "logical_stop_within_limit", "passed": stop_percent <= settings.bullish_max_technical_stop_percent,
             "value": round(stop_percent, 2), "minimum_or_maximum": settings.bullish_max_technical_stop_percent,
             "reason": f"Technical stop is {stop_percent:.2f}% from entry; maximum is {settings.bullish_max_technical_stop_percent}%."},
            {"name": "intraday_path_sample_quality", "passed": bool(adverse.get("available")),
             "value": adverse.get("sample_count", 0), "minimum_or_maximum": settings.bullish_intraday_barrier_minimum_samples,
             "reason": adverse.get("reason", "Intraday path sample is sufficient.")},
            {"name": "target_before_adverse_probability",
             "passed": target_first is not None and float(target_first) >= settings.bullish_min_target_before_adverse_probability,
             "value": target_first, "minimum_or_maximum": settings.bullish_min_target_before_adverse_probability,
             "reason": f"Target-before-adverse probability is {target_first}%; minimum is {settings.bullish_min_target_before_adverse_probability}%."},
            {"name": "overnight_gap_safety", "passed": no_gap is not None and float(no_gap) >= settings.bullish_min_no_overnight_gap_probability,
             "value": no_gap, "minimum_or_maximum": settings.bullish_min_no_overnight_gap_probability,
             "reason": f"Probability of avoiding an overnight gap beyond the adverse barrier is {no_gap}%; minimum is {settings.bullish_min_no_overnight_gap_probability}%."},
            {"name": "positive_stock_relative_strength",
             "passed": bool(relative_strength.get("available")) and rs_score is not None
                       and float(rs_score) >= settings.bullish_min_relative_strength_score,
             "value": rs_score, "minimum_or_maximum": settings.bullish_min_relative_strength_score,
             "reason": f"Relative-strength score is {rs_score}; minimum is {settings.bullish_min_relative_strength_score}."},
            {"name": "supportive_sector", "passed": bool(sector.get("available")) and sector_score is not None
                                                    and float(sector_score) >= settings.entry_min_sector_score,
             "value": sector_score, "minimum_or_maximum": settings.entry_min_sector_score,
             "reason": f"Sector score is {sector_score}; minimum is {settings.entry_min_sector_score}."},
            {"name": "entry_not_overextended",
             "passed": entry_quality.get("position_size_guidance") != "ZERO_UNTIL_RETEST",
             "value": entry_quality.get("extension_band"), "minimum_or_maximum": "NORMAL_OR_CAUTION",
             "reason": f"Entry extension is {entry_quality.get('extension_band', 'UNKNOWN')}."},
            {"name": "volume_confirmation",
             "passed": float(technical.get("relative_volume") or 0) >= minimum_volume,
             "value": technical.get("relative_volume"), "minimum_or_maximum": minimum_volume,
             "reason": f"Relative volume is {float(technical.get('relative_volume') or 0):.2f}x; minimum is {minimum_volume}x."},
            {"name": "reward_to_next_valid_target",
             "passed": float(plan.get("risk_reward") or 0) >= settings.equity_min_risk_reward,
             "value": plan.get("risk_reward"), "minimum_or_maximum": settings.equity_min_risk_reward,
             "reason": f"Reward/risk is {plan.get('risk_reward')}:1; minimum is {settings.equity_min_risk_reward}:1."},
            {"name": "exit_liquidity", "passed": float(liquidity.get("score") or 0) >= settings.candidate_min_liquidity_score,
             "value": liquidity.get("score"), "minimum_or_maximum": settings.candidate_min_liquidity_score,
             "reason": f"Liquidity score is {liquidity.get('score')}; minimum is {settings.candidate_min_liquidity_score}."},
        ]
        failed = [item for item in checks if not item["passed"]]
        return {"passed": not failed, "checks": checks,
                "failed_checks": [item["name"] for item in failed],
                "blocking_reasons": [item["reason"] for item in failed]}

    def _ranking_key(self, item: dict[str, Any]) -> tuple:
        mode = self.platform.settings.candidate_ranking_mode
        values = {
            "EXPECTED_VALUE": item["expected_value"]["risk_multiple"],
            "QUALITY_SCORE": item["quality_score"],
            "AI_SCORE": item["ai_score"],
            "READINESS": item["execution_readiness_score"],
        }
        return (values[mode], item["expected_value"]["risk_multiple"],
                item["quality_score"], item["execution_readiness_score"], item["probability"])

    def _execution_state(self, score: float, regime: str) -> dict[str, Any]:
        settings = self.platform.settings
        regime = (regime or "UNAVAILABLE").upper()
        if regime in {"BULLISH", "STRONG_BULLISH"}:
            execute_threshold, policy = settings.readiness_execute_bullish, "BULLISH"
        elif regime == "STRONG_BEARISH":
            execute_threshold, policy = settings.readiness_execute_strong_bearish, "STRONG_BEARISH"
        elif "UNCERTAIN" in regime or regime == "BEARISH":
            execute_threshold, policy = settings.readiness_execute_cautious, "CAUTIOUS"
        else:
            execute_threshold, policy = settings.readiness_execute_neutral, "NEUTRAL"
        if score >= execute_threshold:
            status, label = "EXECUTE", "Ready to execute"
        elif score >= settings.readiness_prepare:
            status, label = "PREPARE", "Prepare order"
        elif score >= settings.readiness_watch_intraday:
            status, label = "WATCH_INTRADAY", "Watch intraday"
        elif score >= settings.readiness_wait:
            status, label = "WAIT", "Wait for confirmation"
        else:
            status, label = "IGNORE", "Ignore for now"
        return {"score": round(float(score), 2), "status": status, "label": label,
                "policy": policy, "execute_threshold": execute_threshold}

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

    def _execution_score(self, analysis: dict, candidate: dict, alignment: dict,
                         option: dict, option_structure: dict, news: dict, seasonality: dict,
                         regime_history: dict, direction: str, plan: dict) -> dict:
        """Score soft execution evidence; unavailable inputs cannot become vetoes."""
        trend = (analysis["analysis"].get("trend") or "").upper()
        aligned_trend = ((direction == "BULLISH" and "BULLISH" in trend)
                         or (direction == "BEARISH" and "BEARISH" in trend))
        trend_score = 100 if aligned_trend and "STRONG" in trend else 85 if aligned_trend else 20
        relative_volume = float(analysis["analysis"].get("relative_volume") or 0)
        volume_score = (100 if relative_volume >= 1.2 else 75 if relative_volume >= .9
                        else 55 if relative_volume >= .75 else 25)
        rr_score = min(100, max(0, float(plan["risk_reward"]) * 50))
        option_status = option_confidence_status(option.get("confidence"))
        option_score = (100 if option_status == "CONFIRMED" else
                        80 if option_structure.get("valid") else
                        30 if option_status == "CONFLICT" else
                        15 if option_status == "UNRELIABLE" else 50)
        news_score = (min(100, max(0, 50 + float(news.get("score", 0)) / 2))
                      if news.get("analysis_state") == "ANALYZED" else 50)
        history_available = self.completed_outcomes >= self.platform.settings.calibration_min_outcomes
        historical_scores = []
        if seasonality.get("sample_quality") in {"ROBUST", "LIMITED"}:
            historical_scores.append(float(seasonality.get("score", 50)))
        if regime_history.get("sample_quality") in {"ROBUST", "LIMITED"}:
            historical_scores.append(float(regime_history.get("win_rate_percent") or 50))
        history_available = history_available and bool(historical_scores)
        history_score = sum(historical_scores) / len(historical_scores) if historical_scores else 50
        factors = [
            {"name": "technical", "weight": 25, "score": candidate["technical_score"], "available": True},
            {"name": "trend", "weight": 20, "score": trend_score, "available": True},
            {"name": "risk_reward", "weight": 20, "score": rr_score, "available": True},
            {"name": "volume", "weight": 10, "score": volume_score, "available": True},
            {"name": "market_alignment", "weight": 10, "score": alignment["score"], "available": True},
            {"name": "option_context", "weight": 5, "score": option_score, "available": True},
            {"name": "news", "weight": 5, "score": news_score, "available": True},
            {"name": "historical_calibration", "weight": 5, "score": history_score,
             "available": history_available},
        ]
        available_weight = sum(item["weight"] for item in factors if item["available"])
        score = sum(item["score"] * item["weight"] for item in factors if item["available"])
        score = round(score / available_weight, 2) if available_weight else 0
        return {"score": score, "factors": factors,
                "historical_enforced": history_available,
                "historical_minimum_samples": self.platform.settings.calibration_min_outcomes}

    def _trade_readiness(self, analysis: dict, candidate: dict, alignment: dict,
                         sector_data: dict, option_structure: dict, setup: str,
                         seasonality: dict | None = None,
                         regime_history: dict | None = None,
                         entry_confirmation: EntryConfirmationResult | None = None) -> dict:
        """Expose the exact conditions that would promote a watchlist setup."""
        breakout = analysis["breakout"].get("confirmed", False)
        entry_confirmed = (entry_confirmation.passed if entry_confirmation is not None else
                           EntryConfirmationResult.from_setup(
                               analysis.get("setup_evaluation", {}), required=True).passed)
        reversal_needed = setup in {"BULLISH_PULLBACK", "BEARISH_BOUNCE"}
        reversal_seen = analysis["candlestick"].get("signal") in {"BUY", "SELL"}
        checks = [
            {"name": "technical_score", "passed": candidate["technical_score"] >= self.platform.settings.entry_min_technical_score,
             "detail": f"{candidate['technical_score']}/100 (minimum {self.platform.settings.entry_min_technical_score})"},
            {"name": "entry_confirmation", "passed": entry_confirmed,
             "detail": "all confirmation checks passed" if entry_confirmed else "waiting for EMA20, bullish candle, volume above 1.2x and MACD confirmation"},
            {"name": "volume", "passed": analysis["analysis"]["relative_volume"] >= self.platform.settings.entry_min_relative_volume,
             "detail": f"relative volume {analysis['analysis']['relative_volume']:.2f}x (minimum {self.platform.settings.entry_min_relative_volume}x)"},
            {"name": "market_alignment", "passed": alignment["status"] != "CONFLICT",
             "detail": alignment["status"]},
            {"name": "sector_support", "passed": not sector_data.get("available") or sector_data.get("score", 50) >= self.platform.settings.entry_min_sector_score,
             "detail": sector_data.get("rating", "UNAVAILABLE")},
            {"name": "risk_reward", "passed": candidate["trade_plan"]["risk_reward"] >= self.platform.settings.equity_min_risk_reward,
             "detail": f"1:{candidate['trade_plan']['risk_reward']} (minimum 1:{self.platform.settings.equity_min_risk_reward})"},
            {"name": "option_context",
             "passed": bool(option_structure.get("valid")),
             "detail": f"canonical option structure is {option_structure.get('status', 'UNAVAILABLE')}"},
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
        for item in checks:
            if (item["name"] in {"current_month_history", "regime_history"}
                    and self.completed_outcomes < self.platform.settings.calibration_min_outcomes):
                item["counted"] = False
                item["state"] = "NEUTRAL"
                item["detail"] = "not enforced during calibration"
            else:
                item["counted"] = True
        counted = [item for item in checks if item["counted"]]
        passed = sum(bool(item["passed"]) for item in counted)
        percentage = round(passed * 100 / len(counted)) if counted else 0
        return {"ready": False, "passed": passed, "total": len(counted),
                "percentage": percentage, "classification": "UNCLASSIFIED",
                "checks": checks, "next_actions": [item["detail"] for item in counted if not item["passed"]]}

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
            index_score = context.get("score") if index_available else None
            # Candidate quality remains useful when a Yahoo sector index is unavailable.
            combined_score = candidate_score * .6 + float(index_score) * .4 if index_available else None
            rows.append({"sector": sector,
                         "sector_market_score": index_score,
                         "candidate_aggregate_score": round(candidate_score, 2),
                         "combined_context_score": round(combined_score, 2) if combined_score is not None else None,
                         "market_data_status": context.get("status", "UNAVAILABLE"),
                         "ranking_basis": "SECTOR_MARKET_AND_CANDIDATES" if index_available else "CANDIDATE_AGGREGATE_ONLY",
                         "index_score": index_score, "index_available": index_available,
                         "rating": context.get("rating", "UNAVAILABLE"),
                         "candidate_count": len(items),
                         "average_candidate_score": round(candidate_score, 2)})
        rows.sort(key=lambda item: (item["index_available"],
                                    item["combined_context_score"] if item["index_available"] else item["candidate_aggregate_score"],
                                    item["candidate_count"]), reverse=True)
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        return rows

    @staticmethod
    def _canonical_event_clusters(reviewed: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clusters: dict[str, dict[str, Any]] = {}
        for trade in reviewed:
            for event in trade.get("event_risk", {}).get("matched_events", []):
                event_id = str(event.get("canonical_event_id") or event.get("event_id"))
                candidate_score = float(event.get("candidate_event_score", 0))
                existing = clusters.get(event_id)
                if existing is None or candidate_score > float(existing.get("candidate_event_score", 0)):
                    clusters[event_id] = {
                        "event_id": event_id,
                        "category": event.get("category", "NONE"),
                        "title": event.get("title"),
                        "candidate_event_score": candidate_score,
                    }
        return sorted(clusters.values(), key=lambda item: item["candidate_event_score"], reverse=True)

    @staticmethod
    def _event_data_counts(reviewed: list[dict[str, Any]]) -> Counter:
        return Counter(item.get("event_risk", {}).get("event_data_availability_state", "UNAVAILABLE")
                       for item in reviewed)

    @staticmethod
    def _overnight_block_counts(reviewed: list[dict[str, Any]]) -> Counter:
        return Counter(item.get("event_risk", {}).get("overnight_block_cause") or "OTHER_BLOCK"
                       for item in reviewed if not item.get("overnight_hold_allowed", True))

    @staticmethod
    def _conflict_gate(candidate: dict[str, Any], option: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
        """Reject only critical news, execution, and data-quality failures."""
        conflicts = []
        critical_conflicts = []
        if news.get("sentiment") == "BEARISH" or news.get("trade_impact") == "BLOCK":
            conflicts.append("AI news analysis identified material bearish trade risk — score adjustment")
        if news.get("events"):
            conflicts.append("news risk event — score adjustment: " + ", ".join(news["events"]))
        if candidate["action"] in {"BUY", "BUY ON DIP"}:
            if option.get("pcr") is not None and option["pcr"] < .8:
                conflicts.append(f"bearish PCR ({option['pcr']})")
            if option.get("confidence") is not None and option["confidence"] < 50:
                conflicts.append(f"weak option-chain confidence ({option['confidence']}%) — score adjustment only")
        conflicts = critical_conflicts + conflicts
        return {"approved": not critical_conflicts, "conflicts": conflicts,
                "critical_conflicts": critical_conflicts,
                "decision": "APPROVED" if not critical_conflicts else "EXCLUDED"}

    @staticmethod
    def _news_not_requested() -> dict[str, Any]:
        return {
            "available": False, "requested": False, "score": 0,
            "sentiment": "UNAVAILABLE", "confidence": 0, "article_count": 0,
            "events": [], "headlines": [], "analysis_method": "NOT_REQUESTED_BY_POLICY",
            "score_impact": 0, "trade_impact": "NONE",
            "reasons": ["News analysis was deferred because this stock was not in the final news shortlist."],
            "collection_state": "NOT_REQUESTED_BY_POLICY", "analysis_state": "NOT_REQUESTED_BY_POLICY",
            "news_state": "NOT_REQUESTED_BY_POLICY", "readiness_impact": "NOT_APPLICABLE",
        }

    @staticmethod
    def _normalize_news_state(news: dict[str, Any]) -> dict[str, Any]:
        """Map collection/analysis outcomes to the canonical, non-neutral states."""
        count = int(news.get("article_count") or len(news.get("headlines", [])))
        requested = news.get("requested", True)
        explicit = str(news.get("news_state") or news.get("analysis_state") or "").upper()
        if explicit == "STALE_CACHE" or news.get("stale_cache"):
            state = "STALE_CACHE"
        elif not requested:
            state = "NOT_REQUESTED_BY_POLICY"
        elif news.get("fetch_failed") or explicit in {"FETCH_FAILED", "FAILED", "ANALYSIS_FAILED"}:
            state = "FETCH_FAILED"
        elif news.get("available"):
            state = "ANALYZED"
        elif count == 0 and news.get("collection_state") == "FETCHED":
            state = "NO_RELEVANT_NEWS"
        elif count == 0:
            state = "NOT_FETCHED"
        else:
            state = "FETCH_FAILED"
        collection = "FETCHED" if count > 0 or state in {"ANALYZED", "NO_RELEVANT_NEWS"} else state
        sentiment = news.get("sentiment", "UNAVAILABLE")
        if state != "ANALYZED":
            sentiment = "UNAVAILABLE"
        return {**news, "sentiment": sentiment, "collection_state": collection,
                "analysis_state": state, "news_state": state,
                "score_impact": news.get("score_impact", 0) if state == "ANALYZED" else 0,
                "readiness_impact": "SCORED" if state == "ANALYZED" else "UNKNOWN"}

    @staticmethod
    def _option_structure(option: dict[str, Any], settings=None) -> dict[str, Any]:
        if settings is None:
            from src.application.settings import PlatformSettings
            settings = PlatformSettings()
        return OptionStructureValidator.validate(option, settings)

    @staticmethod
    def _option_payoff_metrics(option_trade: dict[str, Any]) -> dict[str, Any]:
        """Represent capped payoff numerically and uncapped long options explicitly."""
        maximum_profit = option_trade.get("maximum_profit")
        maximum_loss = float(option_trade.get("maximum_loss") or 0)
        unlimited_profit = maximum_profit is None and maximum_loss > 0
        ratio = (round(float(maximum_profit) / maximum_loss, 2)
                 if maximum_profit is not None and maximum_loss > 0 else None)
        return {"option_payoff_rr": ratio,
                "option_payoff_profile": "UNLIMITED_PROFIT" if unlimited_profit else "CAPPED",
                "option_payoff_rr_requirement_passed": unlimited_profit}

    def _trade(self, rank: int, candidate: dict[str, Any], market: dict[str, Any],
               sector_strength: dict[str, Any], news: dict[str, Any] | None = None,
               relative_strength: dict[str, Any] | None = None,
               record_recommendation: bool = True) -> dict[str, Any]:
        analysis = self.platform.analyze(candidate["symbol"])
        sector = self.sectors.get_sector(candidate["symbol"])
        sector_data = sector_strength.get(sector, {"available": False, "status": "UNAVAILABLE",
                                                   "score": None, "rating": "UNAVAILABLE"})
        relative_strength = relative_strength or ContextEnrichment(
            self.platform.settings.market_data_source == "kite"
        ).relative_strength(candidate["symbol"])
        option = candidate.get("options", {"available": False})
        seasonality = candidate.get("current_month_seasonality", {})
        regime_history = candidate.get("regime_history", {})
        direction = "BULLISH" if candidate["action"] in {"BUY", "BUY ON DIP", "WATCH"} else "BEARISH"
        normalized_regime = normalize_market_regime(
            market.get("regime", "UNAVAILABLE"), market.get("confidence", 0),
            self.platform.settings.market_low_confidence_threshold,
            self.platform.settings.market_confirmed_confidence_threshold,
        )
        alignment = market_alignment(normalized_regime, market.get("confidence", 0), direction,
                                     self.platform.settings.market_low_confidence_threshold)
        technical = analysis["analysis"]
        contextual_breakout_probability = round(
            (20 if technical["relative_volume"] >= 1.2 else 10 if technical["relative_volume"] >= .9 else 0)
            + (20 if technical["macd"] > technical["macd_signal_line"] else 0)
            + (20 if relative_strength.get("available") and relative_strength.get("score", 0) >= 60 else 0)
            + (20 if sector_data.get("available") and sector_data.get("score", 0) >= 60 else 0)
            + (20 if alignment["status"] == "ALIGNED" else 10 if alignment["status"] in {"UNCERTAIN", "NEUTRAL"} else 0),
            2,
        )
        if technical["relative_volume"] < .9 or technical["macd"] <= technical["macd_signal_line"]:
            contextual_breakout_probability = min(contextual_breakout_probability, 60)
        plan = asdict(TradePlanEngine.generate(
            candidate.get("entry_report", analysis["entry"]),
            breakout_probability=contextual_breakout_probability,
        ))
        adverse_move_risk = {"available": False, "reason": "Not applicable to bearish setup."}
        if direction == "BULLISH":
            target_percent = (float(plan.get("expected_reward") or 0) * 100
                              / max(float(plan.get("entry") or 0), .000001))
            try:
                daily_barrier = AdverseMoveRisk.assess(
                    self.platform.provider.get_data(candidate["symbol"]), target_percent,
                    adverse_percent=self.platform.settings.bullish_max_adverse_move_percent,
                    horizon_days=self.platform.settings.bullish_barrier_horizon_days,
                    minimum_samples=self.platform.settings.bullish_barrier_minimum_samples,
                )
                get_intraday = getattr(self.platform.provider, "get_intraday_history", None)
                if get_intraday is None:
                    adverse_move_risk = {"available": False,
                                         "reason": "15-minute history provider is unavailable.",
                                         "sample_count": 0, "daily_fallback": daily_barrier}
                else:
                    adverse_move_risk = AdverseMoveRisk.assess_intraday(
                        get_intraday(candidate["symbol"], period="6mo", interval="15minute"),
                        target_percent,
                        adverse_percent=self.platform.settings.bullish_max_adverse_move_percent,
                        horizon_days=self.platform.settings.bullish_barrier_horizon_days,
                        minimum_samples=self.platform.settings.bullish_intraday_barrier_minimum_samples,
                        direction="BULLISH",
                    )
                    adverse_move_risk["daily_fallback"] = daily_barrier
            except Exception as exc:
                logger.warning("Intraday barrier study unavailable for %s: %s",
                               candidate["symbol"], exc.__class__.__name__)
                adverse_move_risk = {"available": False,
                                     "reason": f"Barrier study failed: {exc.__class__.__name__}",
                                     "sample_count": 0}
        adverse_hold_probability = adverse_move_risk.get(
            "probability_stays_above_adverse_barrier"
        )
        adverse_risk_passed = (direction != "BULLISH" or (
            adverse_move_risk.get("available")
            and adverse_hold_probability is not None
            and float(adverse_hold_probability)
            >= self.platform.settings.bullish_min_adverse_hold_probability
        ))
        risk_scale = market_risk_scale(
            market.get("confidence", 0), market.get("available", False), alignment["status"],
            self.platform.settings.market_low_confidence_threshold,
            self.platform.settings.market_confirmed_confidence_threshold,
        )
        scaled_risk = {"quantity": 0, "capital_used": 0, "risk_amount": 0, "actual_risk": 0,
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
                "available": False, "score": 0, "sentiment": "UNAVAILABLE",
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
        confirmation_required = setup in {
            "BREAKOUT", "PULLBACK", "REVERSAL CANDIDATE", "TREND FOLLOWING"
        }
        entry_confirmation = EntryConfirmationResult.from_setup(setup_evaluation, confirmation_required)
        entry_quality = setup_evaluation.get("entry_quality", {
            "score": entry_confirmation.score,
            "grade": "A" if entry_confirmation.score >= 85 else "B" if entry_confirmation.score >= 75
                     else "C" if entry_confirmation.score >= 65 else "D",
            "entry_mode": "LEGACY", "extension_band": "UNKNOWN",
            "position_size_guidance": "NORMAL" if entry_confirmation.passed else "ZERO_UNTIL_RETEST",
        })
        atr = float(technical.get("atr") or analysis.get("entry", {}).get("atr") or 0)
        resistance = float(analysis["entry"].get("resistance") or 0)
        clearance_atr = ((resistance - float(plan["entry"])) / atr
                         if atr > 0 and resistance >= float(plan["entry"]) else None)
        breakout_confirmed = bool(analysis["breakout"].get("confirmed"))
        resistance_clear = (clearance_atr is None
                            or clearance_atr >= self.platform.settings.resistance_clearance_min_atr
                            or breakout_confirmed)
        if not resistance_clear:
            entry_confirmation = EntryConfirmationResult(
                False, entry_confirmation.score, entry_confirmation.passed_checks,
                (*entry_confirmation.failed_checks, "resistance_clearance"),
                entry_confirmation.timestamp,
            )
            entry_quality = {
                **entry_quality, "score": min(float(entry_quality["score"]), 64.99), "grade": "D",
                "entry_mode": "WAIT_FOR_CLEARANCE", "position_size_guidance": "ZERO_UNTIL_RETEST",
            }
        entry_eligible = entry_confirmation.passed
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
        momentum_label = setup_evaluation.get("momentum_label", analysis["analysis"]["rsi_signal"])
        quality = self._quality_score(
            analysis, candidate, relative_strength, plan, expectancy, probability, momentum_label,
        )
        stock_selection = ({"passed": True, "checks": [], "failed_checks": [],
                            "blocking_reasons": []}
                           if direction != "BULLISH" else self._bullish_stock_selection_filters(
                               plan=plan, setup=setup, technical=technical,
                               entry_quality=entry_quality, adverse=adverse_move_risk,
                               relative_strength=relative_strength, sector=sector_data,
                               liquidity=candidate["stock_liquidity"], settings=self.platform.settings,
                           ))
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
        rr_tier["enforcement"] = "SOFT_SCORE_INPUT"
        rr_tier["absolute_rejection_min_rr"] = absolute_rr_floor = self.platform.settings.stock_trade_absolute_rr_floor
        rr_tier["executable_trade_min_rr"] = self.platform.settings.equity_min_risk_reward
        rr_tier["absolute_rejection_passed"] = plan["risk_reward"] >= absolute_rr_floor
        rr_tier["executable_trade_passed"] = plan["risk_reward"] >= self.platform.settings.equity_min_risk_reward
        option_structure = OptionStructureValidator.validate(option, self.platform.settings)
        readiness = self._trade_readiness(analysis, candidate, alignment, sector_data, option_structure, setup,
                                          seasonality, regime_history,
                                          entry_confirmation=entry_confirmation)
        market_policy = adaptive_market_policy(normalized_regime)
        execution = self._execution_score(
            analysis, candidate, alignment, option, option_structure, news, seasonality,
            regime_history, direction, plan,
        )
        try:
            event_assessment = self.event_service.assess_candidate(
                {**candidate, "sector": sector, "beta": relative_strength.get("beta")}, self.event_context,
                news_context=news, market_context=market,
                base_readiness=execution["score"],
            ).to_dict()
        except Exception as exc:
            logger.exception("Event risk assessment failed for %s", candidate["symbol"])
            degraded = DailyEventContext([], "FAILED",
                                         [f"Event assessment failed: {exc.__class__.__name__}"], {})
            event_assessment = self.event_service.assess_candidate(
                {**candidate, "sector": sector, "beta": relative_strength.get("beta")}, degraded,
                base_readiness=execution["score"],
            ).to_dict()
        execution_state = self._execution_state(event_assessment["adjusted_readiness"], normalized_regime)
        base_market_quantity = 1
        event_multiplier = event_assessment["position_size_multiplier"]
        event_quantity = 1
        scaled_risk.update({"base_market_adjusted_quantity": 0,
                            "event_position_multiplier": event_multiplier,
                            "effective_risk_percent": round(scaled_risk["effective_risk_percent"] * event_multiplier, 3)})
        event_adjusted_probability = round(min(95, max(
            0, probability + event_assessment["probability_adjustment"]
        )), 2)
        if short_put_approved and "BLOCK_SHORT_PREMIUM" in event_assessment["strategy_restrictions"]:
            short_put_approved = False
            short_put = {**short_put, "available": False, "rejection_code": "EVENT_RISK_BLOCK",
                         "rejection_reasons": ["Event risk blocks short-premium strategies."]}
            option = {**option, "short_put": short_put}
        readiness.update({"base_percentage": execution["score"],
                          "percentage": execution_state["score"],
                          "classification": execution_state["status"],
                          "label": execution_state["label"],
                          "policy": execution_state["policy"],
                          "execute_threshold": execution_state["execute_threshold"],
                          "ready": execution_state["status"] == "EXECUTE",
                          "weighted": True})
        readiness["checks"].append({
            "name": "bullish_3_percent_adverse_barrier", "passed": adverse_risk_passed,
            "counted": direction == "BULLISH", "state": "PASS" if adverse_risk_passed else "FAIL",
            "detail": (f"{adverse_hold_probability}% probability of staying above -"
                       f"{self.platform.settings.bullish_max_adverse_move_percent}%"
                       if adverse_hold_probability is not None else adverse_move_risk.get("reason")),
        })
        confirmation_approved = entry_eligible or not confirmation_required
        strategy_eligibility = combine_strategy_eligibility(
            confirmation_approved, plan["risk_reward"],
            self.platform.settings.equity_min_risk_reward, short_put_approved,
        )
        risk_reward_rejects_setup = plan["risk_reward"] < absolute_rr_floor
        equity_execution_failure = (
            risk_reward_rejects_setup or plan["stop_loss"] <= 0
        )
        critical_failure = (
            not validation["approved"]
            or (equity_execution_failure and not short_put_approved)
        )
        event_size_failure = base_market_quantity > 0 and event_quantity <= 0
        if critical_failure or execution_state["status"] == "IGNORE":
            status = "REJECTED"
        elif (event_assessment["hard_block"] or event_size_failure
              or not confirmation_approved or not adverse_risk_passed
              or not stock_selection["passed"]
              or execution_state["status"] != "EXECUTE"):
            status = "WATCHLIST"
        else:
            status = "TRADE"
        status_reasons = list(validation["critical_conflicts"])
        if plan["risk_reward"] < absolute_rr_floor:
            status_reasons.append(
                f"Risk/reward {plan['risk_reward']}:1 is below the absolute {absolute_rr_floor} minimum."
            )
            status_reasons.extend(plan.get("diagnostics", []))
        elif plan["risk_reward"] < self.platform.settings.equity_min_risk_reward:
            status_reasons.append(
                f"Risk/reward {plan['risk_reward']}:1 passes the {absolute_rr_floor} absolute rejection floor "
                f"but is below the {self.platform.settings.equity_min_risk_reward} executable-trade minimum."
            )
        if plan["stop_loss"] <= 0:
            status_reasons.append("Stop-loss is invalid.")
        if execution_state["status"] == "IGNORE":
            status_reasons.append(f"Execution readiness {execution_state['score']} is below the wait threshold.")
        if status == "WATCHLIST":
            if event_assessment["hard_block"]:
                status_reasons.append(event_assessment["block_reason"] or "Event risk blocks a new position.")
            if event_size_failure:
                status_reasons.append("Event-adjusted position size is below one share or valid lot.")
            if execution_state["status"] != "EXECUTE":
                status_reasons.append(
                    f"Execution readiness {execution_state['score']} is {execution_state['status']}; "
                    f"{execution_state['execute_threshold']} is required to execute under {execution_state['policy']} policy."
                )
            if not confirmation_approved:
                missing = setup_evaluation.get("stage_2", {}).get("missing", [])
                status_reasons.append("Entry confirmation missing: " + ", ".join(missing))
            if not adverse_risk_passed:
                if adverse_hold_probability is None:
                    status_reasons.append("Historical 3% adverse-move probability is unavailable: "
                                          + adverse_move_risk.get("reason", "insufficient evidence"))
                else:
                    status_reasons.append(
                        f"Only {adverse_hold_probability}% of comparable bullish windows stayed above the "
                        f"-{self.platform.settings.bullish_max_adverse_move_percent}% barrier; "
                        f"{self.platform.settings.bullish_min_adverse_hold_probability}% is required."
                    )
            status_reasons.extend(stock_selection["blocking_reasons"])
            if option_status in {"CONFLICT", "UNRELIABLE"} and not budget_only_option_failure:
                rejection = self._option_rejection(option)
                status_reasons.append(rejection.get("reason", f"Option confirmation is {option_status}."))
            if (market.get("confidence", 0) >= self.platform.settings.market_confirmed_confidence_threshold
                    and alignment["status"] == "CONFLICT"):
                status_reasons.append("Trade direction conflicts with a confirmed market regime.")
        equity_eligible = (
            strategy_eligibility["equity_approved"]
            and plan["stop_loss"] > 0
            and validation["approved"]
            and not event_assessment["hard_block"]
            and status == "TRADE"
        )
        eligibility = {"eligible": status == "TRADE", "status": status,
                       "blocking_reasons": status_reasons,
                       "model_confidence_does_not_imply_eligibility": True}
        option_execution_valid = False
        option_directional_support = ("STRONG" if option_status == "CONFIRMED" else
                                      "WEAK" if option_status in {"CONFLICT", "UNRELIABLE"} else
                                      "NEUTRAL")
        option_payoff = self._option_payoff_metrics(option_trade)
        trade = {
            "rank": rank,
            "symbol": candidate["symbol"],
            "sector": sector,
            "current_price": candidate["current_price"],
            "ai_score": unified_score,
            "technical_score": candidate["technical_score"],
            "quality_score": quality["score"],
            "quality_grade": quality["grade"],
            "quality_label": quality["label"],
            "quality_factors": quality["factors"],
            "execution_readiness_score": execution_state["score"],
            "execution_status": execution_state["status"],
            "execution_label": execution_state["label"],
            "confidence": candidate["confidence"],
            "model_confidence": {"decision_confidence": candidate["confidence"],
                                 "estimated_probability": probability,
                                 "calibrated_probability": calibrated_probability,
                                 "event_adjusted_probability": event_adjusted_probability},
            "adverse_move_risk": {**adverse_move_risk, "passed": adverse_risk_passed,
                                  "minimum_hold_probability":
                                      self.platform.settings.bullish_min_adverse_hold_probability},
            "stock_selection_filters": stock_selection,
            "trade_eligibility": eligibility,
            "entry_confirmation": entry_confirmation.to_dict(),
            "entry_quality_score": entry_quality["score"],
            "entry_quality_grade": entry_quality["grade"],
            "entry_mode": entry_quality["entry_mode"],
            "extension_band": entry_quality["extension_band"],
            "position_size_guidance": entry_quality["position_size_guidance"],
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
            "execution_score": execution,
            "expected_value": expectancy,
            "current_month_seasonality": {**seasonality, "score_adjustment": round(seasonality_adjustment, 2)},
            "regime_history": {**regime_history, "score_adjustment": round(regime_adjustment, 2)},
            "risk_policy": {"configured_risk_percent": self.platform.settings.risk_percent,
                            "effective_risk_percent": scaled_risk["effective_risk_percent"],
                            "market_confidence": market.get("confidence", 0), "position_scale": risk_scale,
                            "event_position_scale": event_multiplier,
                            "entry_position_scale": (.5 if entry_quality.get("position_size_guidance") == "REDUCED"
                                                     else 0 if entry_quality.get("position_size_guidance") == "ZERO_UNTIL_RETEST"
                                                     else 1),
                            "combined_position_scale": round(
                                risk_scale * event_multiplier *
                                (.5 if entry_quality.get("position_size_guidance") == "REDUCED"
                                 else 0 if entry_quality.get("position_size_guidance") == "ZERO_UNTIL_RETEST"
                                 else 1), 3)},
            "risk_reward_policy": rr_tier,
            "risk_reward": {
                "nearest_target_rr": round(float(plan.get("nearest_target_reward", 0)) / float(plan["risk"]), 2)
                if float(plan["risk"]) > 0 else 0,
                "strategy_target_rr": round(float(plan["risk_reward"]), 2),
                "expected_value_r": expectancy["risk_multiple"],
                **option_payoff,
                "configured_approval_ratio": self.platform.settings.equity_min_risk_reward,
                "resistance_clearance_atr": round(clearance_atr, 3) if clearance_atr is not None else None,
                "resistance_clearance_passed": resistance_clear,
            },
            "option_budget_policy": {"capital_available": self.platform.settings.option_capital,
                                     "risk_per_trade": self.platform.settings.option_risk_per_trade,
                                     "confidence_adjusted_risk": scaled_option_risk,
                                     "stock_eligibility_independent": True},
            "probability": probability,
            "base_probability": probability,
            "event_adjusted_probability": event_adjusted_probability,
            "strategy": (short_put.get("strategy") if short_put_approved
                         else option.get("strategy") if option.get("available")
                         else "Swing Breakout" if analysis["breakout"]["confirmed"]
                         else candidate["action"]),
            "strategy_priority": ["SHORT_PUT", "BULL_PUT_SPREAD", "CASH_SECURED_PUT", "BULL_CALL_SPREAD"],
            "confidence_grade": {"grade": quality["grade"], "label": quality["label"],
                                 "deprecated": True, "maps_to": "quality_grade"},
            "status": status,
            "setup": setup,
            "market_alignment": alignment,
            "option_status": option_status,
            "option_execution_valid": option_execution_valid,
            "option_directional_support": option_directional_support,
            "option_context": {"execution": "APPROVED" if option_execution_valid else "UNAVAILABLE_OR_REJECTED",
                               "directional_support": option_directional_support,
                               "equity_blocking": False},
            "option_structure": option_structure,
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
                "targets": [plan["target1"], plan["target2"], plan["target3"]],
            },
            "risk": scaled_risk,
            "stock_liquidity": candidate["stock_liquidity"],
            "trust": candidate["trust"],
            "option_strategy": option,
            "short_put_strategy": short_put,
            "news": news,
            "market_context": {**market, "regime": normalized_regime},
            "event_risk": event_assessment,
            "overnight_hold_allowed": event_assessment["overnight_hold_allowed"],
            "overnight_risk_reason": event_assessment["overnight_risk_reason"],
            "strategy_restrictions": event_assessment["strategy_restrictions"],
            "sector_context": {"sector": sector, **sector_data},
            "relative_strength": relative_strength,
            "ai_reasoning": reasons,
            "calibrated_probability": calibrated_probability,
            "validation": validation,
            "selection_stability": candidate.get("selection_stability", {
                "status": "NO_HISTORY", "eligible": True, "appearances": 0,
                "runs_reviewed": 0, "score": 100.0,
            }),
        }
        entry_selection = classify_entry_timing(
            current_price=trade["current_price"], levels=trade["levels"],
            setup_category=setup_evaluation.get("stage_1", {}).get("category", setup),
            breakout_confirmed=bool(analysis["breakout"].get("confirmed")),
            entry_confirmed=entry_confirmation.passed, direction=direction,
            ema20=analysis["analysis"].get("ema20"), atr=analysis["analysis"].get("atr"),
            entry_zone_below_atr=self.platform.settings.entry_zone_below_atr,
            entry_zone_above_atr=self.platform.settings.entry_zone_above_atr,
        )
        trade["entry_selection"] = entry_selection
        trade["selection_status"] = entry_selection["status"]
        trade["selection_reason"] = entry_selection["reason"]
        stability = trade["selection_stability"]
        active_position_block = trade["symbol"] in self.excluded_symbols
        stability_pending = not bool(stability.get("eligible", True))
        if active_position_block:
            trade["entry_selection"] = {**trade["entry_selection"], "status": "AVOID",
                                         "reason": "This stock already has an active tracked position."}
        elif stability_pending:
            trade["entry_selection"] = {
                **trade["entry_selection"], "status": "WAIT FOR CONFIRMATION",
                "reason": "Candidate is new or not yet persistent; keep it on the watchlist until confirmed.",
            }
        trade["selection_status"] = trade["entry_selection"]["status"]
        trade["selection_reason"] = trade["entry_selection"]["reason"]
        if entry_selection["status"] != "BUY NOW":
            status_reasons.append(entry_selection["reason"])
        if active_position_block or stability_pending:
            status_reasons.append(trade["selection_reason"])
        news_complete = news["news_state"] in {"ANALYZED", "NO_RELEVANT_NEWS",
                                                "NOT_REQUESTED_BY_POLICY"}
        event_complete = event_assessment.get("freshness_state") in {"FRESH", "DELAYED", "STALE"}
        offline_waiver = self.platform.settings.market_data_source != "kite"
        analysis_waivers = {
            "news": {"waived": offline_waiver, "reason": "Explicit cache-mode waiver" if offline_waiver else None},
            "event": {"waived": offline_waiver, "reason": "Explicit cache-mode waiver" if offline_waiver else None},
        }
        relative_strength_score = (float(relative_strength["score"])
                                   if relative_strength.get("score") is not None else None)
        regime_rs_minimum = (self.platform.settings.bearish_bullish_trade_min_relative_strength
                             if normalized_regime in {"BEARISH", "STRONG_BEARISH"} and direction == "BULLISH" else
                             self.platform.settings.uncertain_bullish_trade_min_relative_strength
                             if normalized_regime.startswith("UNCERTAIN") and direction == "BULLISH" else 0)
        regime_rs_complete = regime_rs_minimum == 0 or (
            bool(relative_strength.get("available")) and relative_strength_score is not None
        )
        regime_rs_passed = (regime_rs_minimum == 0 or not regime_rs_complete
                            or relative_strength_score >= regime_rs_minimum)
        if not regime_rs_complete:
            status_reasons.append(
                f"Relative strength is {relative_strength.get('status', 'UNAVAILABLE')}; "
                "execution waits for real relative-strength evidence without using a fallback score."
            )
        elif not regime_rs_passed:
            status_reasons.append(
                f"Relative strength {relative_strength_score if relative_strength_score is not None else 'UNAVAILABLE'} "
                f"is below the {regime_rs_minimum} minimum for "
                f"a bullish trade in {normalized_regime}."
            )
        decision = FinalDecisionEngine.decide(
            direction=direction,
            entry=entry_confirmation,
            readiness_status=("WAIT" if (not regime_rs_complete
                                          or entry_selection["status"] != "BUY NOW"
                                          or stability_pending)
                              else execution_state["status"]),
            eligible=bool(equity_eligible and regime_rs_passed),
            hard_block=bool(event_assessment["hard_block"] or active_position_block),
            critical_failure=bool(critical_failure),
            news_complete=news_complete or not self.platform.settings.news_analysis_required_for_execution,
            event_complete=event_complete,
            news_waived=analysis_waivers["news"]["waived"],
            event_waived=analysis_waivers["event"]["waived"],
            reasons=status_reasons,
        )
        final_action = decision.action.value
        executable = decision.executable
        if final_action in {"REJECT", "NO_TRADE"}:
            trade["entry_selection"] = {
                **entry_selection, "status": "AVOID",
                "reason": ((decision.rejection_reasons or decision.reasons or
                            ("Final execution gates did not pass.",))[0]),
            }
        elif not executable and entry_selection["status"] == "BUY NOW":
            trade["entry_selection"] = {
                **entry_selection, "status": "AVOID",
                "reason": ((decision.reasons or
                            ("Entry timing is valid, but final execution gates did not pass.",))[0]),
            }
        trade["selection_status"] = trade["entry_selection"]["status"]
        trade["selection_reason"] = trade["entry_selection"]["reason"]
        trade["final_decision"] = decision.to_dict()
        trade["analysis_waivers"] = analysis_waivers
        trade["market_policy"] = {**trade["market_policy"], "relative_strength_minimum": regime_rs_minimum,
                                  "relative_strength_complete": regime_rs_complete,
                                  "relative_strength_passed": regime_rs_passed}
        trade["final_action"] = final_action
        trade["action"] = final_action
        trade["recommendation"] = final_action
        trade["trade_eligibility"] = {
            **eligibility, "eligible": executable,
            "status": "TRADE" if executable else "REJECTED" if final_action == "REJECT" else "WATCHLIST",
            "blocking_reasons": list(decision.rejection_reasons or decision.reasons),
        }
        trade["status"] = trade["trade_eligibility"]["status"]
        base_risk = PositionSizingEngine.calculate(
            self.platform.settings.capital, self.platform.settings.risk_percent,
            plan["entry"], plan["stop_loss"],
        )
        entry_position_multiplier = (.5 if entry_quality.get("position_size_guidance") == "REDUCED"
                                     else 0.0 if entry_quality.get("position_size_guidance") == "ZERO_UNTIL_RETEST"
                                     else 1.0)
        base_market_quantity = int(base_risk.get("quantity", 0) * risk_scale * entry_position_multiplier)
        planned_quantity = int(base_market_quantity * event_multiplier)
        planned_position = {
            **base_risk,
            "base_market_adjusted_quantity": base_market_quantity,
            "quantity": planned_quantity,
            "capital_used": round(base_risk.get("capital_used", 0) * risk_scale * event_multiplier
                                  * entry_position_multiplier, 2),
            "risk_amount": round(base_risk.get("risk_amount", 0) * risk_scale * event_multiplier
                                 * entry_position_multiplier, 2),
            "actual_risk": round(base_risk.get("actual_risk", 0) * risk_scale * event_multiplier
                                 * entry_position_multiplier, 2),
            "market_confidence_scale": risk_scale, "event_position_multiplier": event_multiplier,
            "entry_position_multiplier": entry_position_multiplier,
            "effective_risk_percent": round(self.platform.settings.risk_percent * risk_scale
                                            * event_multiplier * entry_position_multiplier, 3),
        }
        trade["planned_position_if_confirmed"] = planned_position
        if executable and planned_quantity > 0:
            trade["risk"] = dict(planned_position)
        else:
            trade["risk"] = {**planned_position, "quantity": 0, "capital_used": 0,
                             "risk_amount": 0, "actual_risk": 0}
        if executable and planned_quantity <= 0:
            failed_decision = FinalDecisionEngine.decide(
                direction=direction, entry=entry_confirmation, readiness_status="IGNORE",
                eligible=False, hard_block=False, critical_failure=True,
                news_complete=news_complete, event_complete=event_complete,
                news_waived=analysis_waivers["news"]["waived"],
                event_waived=analysis_waivers["event"]["waived"],
                reasons=[*status_reasons, "Eligible risk budget produces a position below one share."],
            )
            executable = False
            final_action = failed_decision.action.value
            trade["final_decision"] = failed_decision.to_dict()
            trade["final_action"] = trade["action"] = trade["recommendation"] = final_action
            trade["status"] = "REJECTED"
            trade["trade_eligibility"] = {**trade["trade_eligibility"], "eligible": False,
                                           "status": "REJECTED",
                                           "blocking_reasons": list(failed_decision.rejection_reasons)}
            trade["entry_selection"] = {**trade["entry_selection"], "status": "AVOID",
                                         "reason": "Risk budget is too small for one valid share or lot."}
            trade["selection_status"] = "AVOID"
            trade["selection_reason"] = trade["entry_selection"]["reason"]
        restrictions = set(event_assessment["strategy_restrictions"])
        option_reasons = []
        if not option_structure["valid"]: option_reasons.extend(option_structure["rejection_codes"])
        if not entry_confirmation.passed: option_reasons.append("ENTRY_CONFIRMATION_FAILED")
        if execution_state["score"] < self.platform.settings.option_approval_min_readiness:
            option_reasons.append("READINESS_TOO_LOW")
        option_payoff_rr = trade["risk_reward"]["option_payoff_rr"]
        unlimited_option_profit = trade["risk_reward"]["option_payoff_profile"] == "UNLIMITED_PROFIT"
        if (not unlimited_option_profit
                and (option_payoff_rr is None
                     or option_payoff_rr < self.platform.settings.option_min_payoff_risk_reward)):
            option_reasons.append("OPTION_PAYOFF_RR_TOO_LOW")
        trade["risk_reward"]["option_payoff_rr_requirement_passed"] = (
            unlimited_option_profit or (option_payoff_rr is not None
                                        and option_payoff_rr >= self.platform.settings.option_min_payoff_risk_reward)
        )
        if final_action not in {"BUY", "SELL"}: option_reasons.append("FINAL_ACTION_INCOMPATIBLE")
        if alignment["status"] == "CONFLICT": option_reasons.append("MARKET_ALIGNMENT_CONFLICT")
        if event_assessment["hard_block"] or "BLOCK_OPTIONS" in restrictions:
            option_reasons.append("EVENT_RESTRICTION")
        option_approved = not option_reasons
        trade["option_trade_approval"] = {
            "result_type": "OptionTradeApprovalResult",
            "status": "APPROVED" if option_approved else "REJECTED",
            "approved": option_approved,
            "rejection_codes": list(dict.fromkeys(option_reasons)),
            "underlying_confirmation": entry_confirmation.passed,
            "minimum_readiness": self.platform.settings.option_approval_min_readiness,
            "minimum_payoff_rr": self.platform.settings.option_min_payoff_risk_reward,
        }
        trade["option_execution_valid"] = option_approved
        trade["option_context"] = {**trade["option_context"],
                                   "execution": "APPROVED" if option_approved else "REJECTED"}
        try:
            FinalConsistencyValidator.validate(trade)
            trade["consistency_validation"] = {"passed": True, "errors": []}
        except Exception as exc:
            logger.error("Consistency validation failed for %s: %s", trade["symbol"], exc)
            failed_decision = FinalDecisionEngine.decide(
                direction=direction, entry=entry_confirmation, readiness_status="IGNORE",
                eligible=False, hard_block=False, critical_failure=True, news_complete=False,
                reasons=[f"Consistency validation failed: {exc}"],
            )
            trade["final_decision"] = failed_decision.to_dict()
            trade["final_action"] = trade["action"] = trade["recommendation"] = failed_decision.action.value
            trade["status"] = "REJECTED"
            trade["trade_eligibility"] = {**trade["trade_eligibility"], "eligible": False,
                                           "status": "REJECTED",
                                           "blocking_reasons": [f"Consistency validation failed: {exc}"]}
            trade["risk"] = {**trade["risk"], "quantity": 0, "capital_used": 0,
                             "risk_amount": 0, "actual_risk": 0}
            trade["option_trade_approval"] = {**trade["option_trade_approval"], "status": "REJECTED",
                                               "approved": False,
                                               "rejection_codes": ["CONSISTENCY_VALIDATION_FAILED"]}
            trade["option_execution_valid"] = False
            trade["option_context"] = {**trade["option_context"], "execution": "REJECTED"}
            trade["consistency_validation"] = {"passed": False, "errors": [str(exc)]}
            trade["entry_selection"] = {**trade["entry_selection"], "status": "AVOID",
                                         "reason": f"Consistency validation failed: {exc}"}
            trade["selection_status"] = "AVOID"
            trade["selection_reason"] = trade["entry_selection"]["reason"]
            FinalConsistencyValidator.validate(trade)
        if executable and record_recommendation:
            trade["recommendation_id"] = self.outcomes.record_recommendation(trade)
        else:
            trade["recommendation_id"] = None
        if record_recommendation:
            trade["journal_path"] = str(self.journal.append(self.run_id, trade))
        return trade

    def generate(self, limit: int = 5, minimum_score: int = 40) -> dict[str, Any]:
        started = perf_counter()
        self.run_id = str(uuid4())
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
        enrichment = ContextEnrichment(
            self.platform.settings.market_data_source == "kite",
            sector_history_provider=(self.platform.provider
                                     if self.platform.settings.market_data_source == "kite" else None),
        )
        market_context, sector_strength = enrichment.market_and_sectors()
        try:
            self.event_context = self.event_service.build_daily_context(market_context)
        except Exception as exc:
            logger.exception("Shared event context failed")
            self.event_context = DailyEventContext(
                [], "FAILED", [f"Event context failed: {exc.__class__.__name__}"],
                {"event_context_fetch_seconds": 0, "event_sources_requested": 0,
                 "events_detected": 0, "event_clusters_created": 0},
            )
        candidates = ranked["suggestions"]
        if deferred_enrichment:
            enrich_started = perf_counter()
            if len(candidates) > 1:
                with ThreadPoolExecutor(max_workers=min(2, len(candidates))) as executor:
                    candidates = list(executor.map(
                        lambda candidate: self.platform.enrich_candidate(candidate, self.option_month),
                        candidates,
                    ))
            else:
                candidates = [self.platform.enrich_candidate(candidate, self.option_month)
                              for candidate in candidates]
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
        relative_strength_distribution = enrichment.finalize_relative_strength(strengths)
        if relative_strength_distribution.get("warning"):
            logger.warning(relative_strength_distribution["warning"])
        strength_by_symbol = {
            candidate["symbol"]: strength for candidate, strength in zip(candidates, strengths)
        }
        recent_runs = self.journal.recent_selected_symbols(
            self.platform.settings.selection_stability_lookback_runs
        )
        for candidate in candidates:
            symbol = str(candidate["symbol"]).upper().removesuffix(".NS")
            appearances = sum(symbol in symbols for symbols in recent_runs)
            required = min(
                self.platform.settings.selection_stability_min_appearances,
                len(recent_runs),
            )
            enough_history = len(recent_runs) >= self.platform.settings.selection_stability_min_appearances
            candidate["selection_stability"] = {
                "status": ("NEW_NO_HISTORY" if not recent_runs else
                           "STABLE" if appearances >= required else
                           "BUILDING_HISTORY" if not enough_history else "UNSTABLE"),
                "eligible": not enough_history or appearances >= required,
                "appearances": appearances,
                "runs_reviewed": len(recent_runs),
                "required_appearances": required,
                "score": round(100 * (appearances + 1) / (len(recent_runs) + 1), 2),
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
        preliminary.sort(key=self._ranking_key, reverse=True)
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
                        relative_strength=strength_by_symbol[candidate["symbol"]],
                        record_recommendation=False)
            for index, candidate in enumerate(candidates, start=1)
        ]
        reviewed.sort(key=self._ranking_key, reverse=True)
        sector_deferred = self._apply_sector_limit(
            reviewed, self.platform.settings.selection_max_trades_per_sector
        )
        for trade in reviewed:
            trade["recommendation_id"] = (
                self.outcomes.record_recommendation(trade) if trade["status"] == "TRADE" else None
            )
            trade["journal_path"] = str(self.journal.append(self.run_id, trade))
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
        market = normalize_market_regime(
            market_context["regime"], market_context["confidence"],
            self.platform.settings.market_low_confidence_threshold,
            self.platform.settings.market_confirmed_confidence_threshold,
        ) if market_context["available"] else ("BULLISH" if bullish > len(trades) / 2 else "NEUTRAL")
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
        canonical_event_clusters = self._canonical_event_clusters(reviewed)
        event_data_counts = self._event_data_counts(reviewed)
        overnight_causes = self._overnight_block_counts(reviewed)
        context_statistics = {
            "news": {
                "passed": sum(1 for item in reviewed if item["news"].get("available") and item["news"].get("sentiment") != "BEARISH" and item["news"].get("trade_impact") != "BLOCK" and not item["news"].get("events")),
                "failed": sum(1 for item in reviewed if item["news"].get("available") and (item["news"].get("sentiment") == "BEARISH" or item["news"].get("trade_impact") == "BLOCK" or item["news"].get("events"))),
                "unavailable": sum(1 for item in reviewed if not item["news"].get("available")),
                "fetched": sum(1 for item in reviewed if item["news"].get("collection_state") == "FETCHED"),
                "not_fetched": sum(1 for item in reviewed if item["news"].get("collection_state") == "NOT_FETCHED"),
                "analysis_failed": sum(1 for item in reviewed if item["news"].get("analysis_state") == "FAILED"),
                "not_requested_by_policy": sum(1 for item in reviewed if item["news"].get("news_state") == "NOT_REQUESTED_BY_POLICY"),
                "fetch_failed": sum(1 for item in reviewed if item["news"].get("news_state") == "FETCH_FAILED"),
                "no_relevant_news": sum(1 for item in reviewed if item["news"].get("news_state") == "NO_RELEVANT_NEWS"),
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
        risk_valid = sum(1 for item in reviewed if item["levels"]["risk_reward"] >= self.platform.settings.stock_trade_absolute_rr_floor and item["levels"]["stop_loss"] > 0)
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
            {"stage": "event_risk", "input": len(reviewed),
             "passed": sum(1 for item in reviewed if not item["event_risk"]["hard_block"]),
             "rejected": sum(1 for item in reviewed if item["event_risk"]["hard_block"])},
            {"stage": "final_trade", "input": len(reviewed), "passed": len(trades), "rejected": len(reviewed) - len(trades)},
        ]
        event_timings = self.event_context.timings
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
            **event_timings,
            "event_total_seconds": round(
                float(event_timings.get("event_context_fetch_seconds", 0))
                + float(event_timings.get("event_candidate_scoring_seconds", 0)), 3
            ),
        }
        logger.info("Daily report stages completed: %s", timings)
        return {
            "report_type": "daily_trading_assistant",
            "run_id": self.run_id,
            "date": date.today().isoformat(),
            "ranking_mode": self.platform.settings.candidate_ranking_mode,
            "option_month_filter": self.option_month,
            "market": {**market_context, "regime": market, "confidence": market_context["confidence"] if market_context["available"] else round(sum(item["confidence"] for item in trades) / len(trades), 2) if trades else 0},
            "trades": trades,
            "watchlist": watchlist,
            "sector_ranking": sector_ranking,
            "filter_stages": filter_stages,
            "context_statistics": context_statistics,
            "relative_strength_distribution": relative_strength_distribution,
            "dependency_health": AISentimentAnalyzer(
                model=self.platform.settings.news_ai_model,
                spacy_model=self.platform.settings.news_spacy_model,
            ).dependency_health(),
            "historical_learning": historical_learning,
            "event_context": {"data_state": self.event_context.data_state,
                              "warnings": self.event_context.warnings,
                              "events_detected": event_timings.get("events_detected", 0),
                              "event_clusters_created": event_timings.get("event_clusters_created", 0),
                              "canonical_clusters": canonical_event_clusters},
            "timings": timings,
            "rejected": [
                {"symbol": trade["symbol"], "technical_score": trade["technical_score"],
                 "status": trade["status"], "final_action": trade["final_action"],
                 "current_price": trade["current_price"], "levels": trade["levels"],
                 "selection_status": trade["selection_status"],
                 "selection_reason": trade["selection_reason"],
                 "entry_selection": trade["entry_selection"],
                 "reasons": trade["status_reasons"] or trade["validation"]["conflicts"]}
                for trade in rejected
            ],
            "summary": {
                "market": market,
                "best_sector": next((row["sector"] for row in sector_ranking if row["index_available"]), "UNAVAILABLE"),
                "highest_ranked_candidate_sector": (max(sector_ranking,
                    key=lambda row: row["candidate_aggregate_score"])["sector"] if sector_ranking else "NOT AVAILABLE"),
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
                "sector_duplicates_deferred": sector_deferred,
                "active_positions_excluded": sum(
                    item["symbol"] in self.excluded_symbols for item in reviewed
                ),
                "unstable_candidates_deferred": sum(
                    not item.get("selection_stability", {}).get("eligible", True)
                    for item in reviewed
                ),
                "option_month_filter": self.option_month or "AUTO_NEAREST",
                "event_risk_reviewed": len(reviewed),
                "event_risk_very_low": sum(1 for item in reviewed if item["event_risk"]["event_risk_level"] == "VERY_LOW"),
                "event_risk_low": sum(1 for item in reviewed if item["event_risk"]["event_risk_level"] == "LOW"),
                "event_risk_medium": sum(1 for item in reviewed if item["event_risk"]["event_risk_level"] == "MEDIUM"),
                "event_risk_high": sum(1 for item in reviewed if item["event_risk"]["event_risk_level"] == "HIGH"),
                "event_risk_extreme": sum(1 for item in reviewed if item["event_risk"]["event_risk_level"] == "EXTREME"),
                "event_blocked_candidates": sum(1 for item in reviewed if item["event_risk"]["hard_block"]),
                "event_reduced_positions": sum(1 for item in reviewed if item["event_risk"]["position_size_multiplier"] < 1),
                "overnight_blocked_candidates": sum(1 for item in reviewed if not item["overnight_hold_allowed"]),
                "overnight_event_level_blocks": overnight_causes.get("EVENT_LEVEL_BLOCK", 0),
                "overnight_gap_risk_blocks": overnight_causes.get("GAP_RISK_BLOCK", 0),
                "overnight_market_policy_blocks": overnight_causes.get("MARKET_POLICY_BLOCK", 0),
                "overnight_other_blocks": overnight_causes.get("OTHER_BLOCK", 0),
                "event_data_complete": event_data_counts.get("COMPLETE", 0),
                "event_data_partial": event_data_counts.get("PARTIAL", 0),
                "event_data_unavailable": event_data_counts.get("UNAVAILABLE", 0),
                "event_data_failed": event_data_counts.get("FAILED", 0),
                "event_data_not_requested": event_data_counts.get("NOT_REQUESTED", 0),
                "raw_events_detected": event_timings.get("events_detected", 0),
                "candidate_impacting_events": len(canonical_event_clusters),
                "background_market_wide_events": sum(
                    1 for event in self.event_context.events
                    if event.category.value == "MARKET_WIDE"
                ),
                "common_event_category": (canonical_event_clusters[0]["category"]
                                          if canonical_event_clusters else None),
                "highest_risk_candidate": max(reviewed, key=lambda item: item["event_risk"]["event_risk_score"])["symbol"] if reviewed else None,
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
