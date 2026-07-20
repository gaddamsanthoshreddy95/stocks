from __future__ import annotations

from dataclasses import asdict
from datetime import date
from math import floor
from math import erf, sqrt

from src.options.short_put.models import (
    ShortPutCandidate, ShortPutEvaluation, ShortPutLeg, ShortPutRejection,
    ShortPutRejectionCode, ShortPutStrategyPlan,
)
from src.options.short_put.strike_selector import ShortPutStrikeSelector
from src.workflow.final_decision import EntryConfirmationResult
from src.options.black_scholes import price_and_greeks, probability_expiring_otm
from src.options.short_put.event_risk import ShortPutEventRiskEvaluator


class ShortPutStrategyEngine:
    """Evaluate a bullish underlying independently from equity reward/risk."""

    @staticmethod
    def _reject(symbol: str, setup: str, code: str, reason: str, warnings=None) -> ShortPutStrategyPlan:
        rejection = ShortPutRejection(code, reason)
        evaluation = ShortPutEvaluation("UNAVAILABLE", "REJECTED", False, [rejection], warnings or [])
        return ShortPutStrategyPlan(
            False, symbol, setup, evaluation=evaluation, rejection_code=code,
            rejection_reasons=[reason], warnings=warnings or [],
        )

    @staticmethod
    def _underlying_eligible(analysis: dict) -> tuple[bool, str, str | None]:
        setup_eval = analysis.get("setup_evaluation", {})
        confirmation = EntryConfirmationResult.from_setup(setup_eval, required=True)
        category = setup_eval.get("stage_1", {}).get("category", "WATCHLIST")
        technical = analysis.get("analysis", {})
        trend = technical.get("trend", "NEUTRAL")
        eligible_categories = {"TREND FOLLOWING", "BREAKOUT", "PULLBACK"}
        price_structure_ok = float(technical.get("current_price", 0)) >= float(technical.get("ema50", float("inf")))
        momentum_ok = float(technical.get("macd", 0)) > float(technical.get("macd_signal_line", 0))
        volume_ok = float(technical.get("relative_volume", 0)) >= .75
        if (category in eligible_categories and confirmation.passed and "BULLISH" in trend
                and price_structure_ok and momentum_ok and volume_ok):
            return True, category, None
        if category == "REVERSAL CANDIDATE":
            checks = setup_eval.get("stage_2", {}).get("checks", {})
            confirmed = (
                checks.get("bullish_reversal_candle") and checks.get("macd_above_signal")
                and checks.get("volume_above_1_2x") and setup_eval.get("stage_1", {}).get("evidence", {}).get("support_nearby")
            )
            return bool(confirmed), "CONFIRMED_REVERSAL" if confirmed else category, None if confirmed else "REVERSAL_NOT_CONFIRMED"
        return False, category, "UNDERLYING_NOT_BULLISH"

    @staticmethod
    def _probability(put, spot: float, dte: int, atr: float):
        effective_delta = put.delta
        delta_source = put.greeks_source or ("BROKER_DELTA" if put.delta is not None else None)
        if put.implied_volatility:
            probability = probability_expiring_otm(
                spot, put.strike, max(dte, 1) / 365, .07,
                put.implied_volatility / 100, "PE",
            )
            if effective_delta is None:
                greeks = price_and_greeks(spot, put.strike, max(dte, 1) / 365, .07,
                                          put.implied_volatility / 100, "PE")
                effective_delta = greeks["delta"] if greeks else None
                delta_source = "BLACK_SCHOLES"
            if probability is not None:
                return effective_delta, probability, "BLACK_SCHOLES", "MEDIUM"
        if effective_delta is not None:
            return effective_delta, round((1 - abs(effective_delta)) * 100, 2), \
                delta_source or "DELTA_PROXY", "MEDIUM"
        if atr > 0:
            z_score = (spot - put.strike) / (atr * sqrt(max(dte, 1)))
            probability = round((.5 * (1 + erf(z_score / sqrt(2)))) * 100, 2)
            return None, probability, "ATR_FALLBACK", "LOW"
        return None, None, "UNAVAILABLE", "UNAVAILABLE"

    @classmethod
    def _strike_evaluation(cls, put, spot: float, dte: int, support: float | None,
                           atr: float, settings) -> dict:
        midpoint = (put.bid + put.ask) / 2 if put.bid > 0 and put.ask > 0 else 0
        spread = ((put.ask - put.bid) / midpoint * 100) if midpoint > 0 else float("inf")
        coverage = (spot - put.strike) / atr if atr > 0 else 0
        delta, probability, source, quality = cls._probability(put, spot, dte, atr)
        failures = []
        checks = (
            (put.bid > 0 and put.ask > 0 and put.ask >= put.bid and not put.quote_is_stale, "INVALID_QUOTES"),
            (put.open_interest >= settings.short_put_min_open_interest, "OPEN_INTEREST_TOO_LOW"),
            (put.volume >= settings.short_put_min_volume, "VOLUME_TOO_LOW"),
            (spread <= settings.short_put_max_bid_ask_spread_percent, "BID_ASK_SPREAD_TOO_WIDE"),
            (put.bid >= settings.short_put_min_premium, "PREMIUM_TOO_LOW"),
            (coverage >= settings.short_put_min_atr_coverage, "ATR_COVERAGE_TOO_LOW"),
            (not settings.short_put_require_strike_below_support or (support is not None and put.strike < support), "STRIKE_ABOVE_SUPPORT"),
            (delta is not None, "GREEKS_UNAVAILABLE"),
            (bool(put.implied_volatility), "IV_UNAVAILABLE"),
            (probability is not None and probability >= settings.short_put_min_probability_otm, "PROBABILITY_TOO_LOW"),
        )
        failures.extend(code for passed, code in checks if not passed)
        if delta is not None and not settings.short_put_target_delta_min <= abs(delta) <= settings.short_put_target_delta_max:
            failures.append("DELTA_OUT_OF_RANGE")
        score = max(0, 100 - len(failures) * 15)
        if probability is not None:
            score += min(10, max(0, probability - settings.short_put_min_probability_otm) / 2)
        return {
            "strike": put.strike, "expiry": put.expiry, "dte": dte,
            "strike_distance_percent": round((spot - put.strike) / spot * 100, 2) if spot else None,
            "premium": put.bid, "delta": delta, "atr_coverage": round(coverage, 2),
            "probability_otm": probability, "probability_source": source,
            "probability_quality": quality, "spread_percent": round(spread, 2),
            "open_interest": put.open_interest, "volume": put.volume,
            "score": round(min(100, score), 2), "result": "PASS" if not failures else "REJECT",
            "rejection_codes": failures,
            "recommendations": (
                ["Wait for IV expansion.", "Evaluate the next monthly expiry for a better executable credit."]
                if "PREMIUM_TOO_LOW" in failures else []
            ),
        }

    @classmethod
    def apply_context(cls, plan: dict, market_alignment: dict, sector: dict,
                      news: dict, settings) -> dict:
        """Apply context that only exists after the option plan was built."""
        if not plan.get("available"):
            return plan
        failures = []
        if news.get("sentiment") == "BEARISH" or news.get("trade_impact") == "BLOCK":
            failures.append((ShortPutRejectionCode.NEGATIVE_NEWS, "Severe negative news blocks short-Put selling."))
        if settings.short_put_event_risk_block and news.get("events"):
            failures.append((ShortPutRejectionCode.EVENT_RISK, "Known major event risk occurs before expiry."))
        if market_alignment.get("status") == "CONFLICT":
            failures.append((ShortPutRejectionCode.MARKET_CONTEXT_CONFLICT, "Underlying direction conflicts with the confirmed market regime."))
        if sector.get("available") and sector.get("score", 50) < 50:
            failures.append((ShortPutRejectionCode.SECTOR_CONTEXT_WEAK, "Sector context is too weak for bullish premium selling."))
        if not failures:
            return plan
        evaluation = {**(plan.get("evaluation") or {}), "approved": False, "risk_status": "REJECTED"}
        evaluation["rejections"] = [
            *(evaluation.get("rejections") or []),
            *[{"code": code, "reason": reason} for code, reason in failures],
        ]
        return {
            **plan, "available": False, "evaluation": evaluation,
            "rejection_code": failures[0][0],
            "rejection_reasons": [*plan.get("rejection_reasons", []), *[reason for _, reason in failures]],
        }

    @classmethod
    def evaluate(cls, symbol: str, analysis: dict, chains: list, settings,
                 news: dict | None = None, event_data_available: bool = False,
                 corporate_events: list[dict] | None = None,
                 exposure_context: dict | None = None) -> dict:
        eligible, setup, underlying_code = cls._underlying_eligible(analysis)
        if not eligible:
            reason = "Bullish setup and entry confirmation are insufficient for short-Put risk."
            return asdict(cls._reject(symbol, setup, underlying_code or "UNDERLYING_NOT_BULLISH", reason))
        news = news or {}
        if news.get("sentiment") == "BEARISH" or news.get("trade_impact") == "BLOCK":
            return asdict(cls._reject(symbol, setup, ShortPutRejectionCode.NEGATIVE_NEWS, "Severe negative news blocks short-Put selling."))

        spot = float(analysis["analysis"]["current_price"])
        support = analysis.get("entry", {}).get("support")
        atr = float(analysis["analysis"].get("atr") or analysis.get("entry", {}).get("atr") or 0)
        ranked_strikes, strike_band, error = ShortPutStrikeSelector.ranked_candidates(
            chains, spot, settings, support, atr,
        )
        warnings = []
        if error:
            return asdict(cls._reject(symbol, setup, error, "No actual Put contract satisfies the configured expiry/OTM band.", warnings))
        evaluated_rows = [
            (row, cls._strike_evaluation(row[-1], spot, row[-2], support, atr, settings))
            for row in ranked_strikes
        ]
        # Select only after every scanned contract has a complete evaluation.
        # Passing contracts always outrank rejected ones; if all fail, choose
        # the closest-to-eligible contract so the final rejection is useful.
        selected_row, _ = min(
            evaluated_rows,
            key=lambda pair: (
                len(pair[1]["rejection_codes"]), -pair[1]["score"], *pair[0][:8]
            ),
        )
        strike_evaluations = [evaluation for _, evaluation in evaluated_rows]
        all_strikes_rejected = not any(item["result"] == "PASS" for item in strike_evaluations)
        *_, chain, dte, sold = selected_row
        strike_search = {
            "evaluated": len(ranked_strikes),
            "band": {"lower": round(strike_band[0], 2), "upper": round(strike_band[1], 2)},
            "selected_strike": None if all_strikes_rejected else sold.strike,
            "best_rejected_candidate": (next(evaluation for row, evaluation in evaluated_rows
                                               if row is selected_row)
                                        if all_strikes_rejected else None),
            "selection_rule": "Fewest eligibility failures, then delta fit, probability, spread, OI, volume and executable premium.",
            "exhaustive_within_band": True,
            "includes_adjacent_strikes": any(
                item["strike"] < strike_band[0] or item["strike"] > strike_band[1]
                for item in strike_evaluations
            ),
            "evaluations": strike_evaluations,
        }
        event_risk = ShortPutEventRiskEvaluator.evaluate(chain.expiry, news, corporate_events)
        if event_risk["confirmed_risk"] and settings.short_put_event_risk_block:
            return asdict(cls._reject(symbol, setup, ShortPutRejectionCode.EVENT_RISK, "Known major event occurs before expiry."))
        if event_risk["status"] == "EVENT_DATA_UNAVAILABLE":
            warnings.append("EVENT_DATA_UNAVAILABLE")
        midpoint = (sold.bid + sold.ask) / 2 if sold.bid > 0 and sold.ask > 0 else 0
        spread = ((sold.ask - sold.bid) / midpoint * 100) if midpoint > 0 else float("inf")
        premium = sold.bid  # executable credit, not optimistic midpoint
        buffer = spot - sold.strike
        atr_coverage = buffer / atr if atr > 0 else 0
        below_support = (float(support) - sold.strike) if support is not None else None
        effective_delta, probability, probability_source, probability_quality = cls._probability(
            sold, spot, dte, atr
        )
        candidate = ShortPutCandidate(
            symbol, spot, chain.expiry, dte, sold.strike, round(buffer, 2), round(buffer / spot * 100, 2),
            support, round(below_support, 2) if below_support is not None else None, atr, round(atr_coverage, 2),
            premium, sold.bid, sold.ask, round(spread, 2), sold.volume, sold.open_interest,
            sold.change_in_oi, sold.implied_volatility or None, effective_delta, probability,
            probability_source, probability_quality, sold.lot_size, sold.change_in_oi_reliable,
        )

        rejections: list[ShortPutRejection] = []
        def require(condition: bool, code: ShortPutRejectionCode, reason: str):
            if not condition:
                rejections.append(ShortPutRejection(code, reason))
        require(sold.bid > 0 and sold.ask > 0 and sold.ask >= sold.bid and not sold.quote_is_stale,
                ShortPutRejectionCode.INVALID_QUOTES, "Put quote is zero, crossed, or stale.")
        require(sold.open_interest >= settings.short_put_min_open_interest, ShortPutRejectionCode.OPEN_INTEREST_TOO_LOW, "Open interest is below the configured minimum.")
        require(sold.volume >= settings.short_put_min_volume, ShortPutRejectionCode.VOLUME_TOO_LOW, "Option volume is below the configured minimum.")
        require(spread <= settings.short_put_max_bid_ask_spread_percent, ShortPutRejectionCode.BID_ASK_SPREAD_TOO_WIDE, "Bid/ask spread is too wide.")
        require(premium >= settings.short_put_min_premium, ShortPutRejectionCode.PREMIUM_TOO_LOW, "Executable bid premium is too low.")
        require(atr_coverage >= settings.short_put_min_atr_coverage, ShortPutRejectionCode.ATR_COVERAGE_TOO_LOW, "Strike buffer has insufficient ATR coverage.")
        if settings.short_put_require_strike_below_support:
            require(support is not None and sold.strike < support, ShortPutRejectionCode.STRIKE_ABOVE_SUPPORT, "Sold Put strike is not below technical support.")
        require(effective_delta is not None, ShortPutRejectionCode.GREEKS_UNAVAILABLE, "Delta is unavailable; only a low-confidence fallback is possible.")
        if effective_delta is not None:
            require(settings.short_put_target_delta_min <= abs(effective_delta) <= settings.short_put_target_delta_max, ShortPutRejectionCode.DELTA_OUT_OF_RANGE, "Absolute Put delta is outside the configured range.")
        require(bool(sold.implied_volatility), ShortPutRejectionCode.IV_UNAVAILABLE, "IV is unavailable; model probability quality is insufficient.")
        require(probability is not None and probability >= settings.short_put_min_probability_otm, ShortPutRejectionCode.PROBABILITY_TOO_LOW, "Estimated probability OTM is below the configured minimum.")

        puts = sorted((put for put in chain.puts if put.strike < sold.strike), key=lambda item: item.strike, reverse=True)
        hedge_index = max(settings.short_put_hedge_width_steps - 1, 0)
        hedge = puts[hedge_index] if len(puts) > hedge_index else None
        use_spread = settings.short_put_prefer_credit_spread or not settings.short_put_allow_naked
        strategy = "BULL_PUT_SPREAD" if use_spread else "CASH_SECURED_PUT"
        if use_spread and hedge is None:
            rejections.append(ShortPutRejection(ShortPutRejectionCode.NO_VALID_HEDGE, "No actual lower-strike hedge Put is available."))

        lot_size = max(1, sold.lot_size)
        hedge_premium = hedge.ask if hedge and hedge.ask > 0 else 0
        net_credit = premium - hedge_premium if use_spread else premium
        if use_spread:
            require(hedge is not None and hedge.bid > 0 and hedge.ask > 0 and net_credit > 0 and hedge.strike < sold.strike,
                    ShortPutRejectionCode.NO_VALID_HEDGE, "Hedge quote/order is invalid or does not produce a positive credit.")
            width = sold.strike - hedge.strike if hedge else 0
            max_loss_per_lot = max(0, (width - net_credit) * lot_size)
            capital_per_lot = max_loss_per_lot
        else:
            max_loss_per_lot = (sold.strike - premium) * lot_size
            capital_per_lot = sold.strike * lot_size
        max_profit_per_lot = max(0, net_credit * lot_size)
        exposure = exposure_context or {}
        portfolio_exposure = float(exposure.get("portfolio_exposure_percent", 0))
        sector_exposure = float(exposure.get("sector_exposure_percent", 0))
        correlated_exposure = exposure.get("correlated_exposure_percent")
        require(portfolio_exposure < settings.short_put_max_portfolio_exposure_percent,
                ShortPutRejectionCode.PORTFOLIO_EXPOSURE_EXCEEDED, "Portfolio exposure limit has been reached.")
        require(sector_exposure < settings.short_put_max_sector_exposure_percent,
                ShortPutRejectionCode.SECTOR_EXPOSURE_EXCEEDED, "Sector exposure limit has been reached.")
        if correlated_exposure is None:
            warnings.append("CORRELATION_DATA_UNAVAILABLE")
        else:
            require(float(correlated_exposure) < settings.short_put_max_correlated_exposure_percent,
                    ShortPutRejectionCode.PORTFOLIO_EXPOSURE_EXCEEDED, "Correlated exposure limit has been reached.")
        broker_margin_per_lot = exposure.get("broker_margin_per_lot")
        if broker_margin_per_lot is not None and float(broker_margin_per_lot) > 0:
            capital_per_lot = max(capital_per_lot, float(broker_margin_per_lot))
            margin_source = "BROKER_MARGIN"
        else:
            margin_source = "ASSIGNMENT_OBLIGATION" if not use_spread else "DEFINED_MAXIMUM_LOSS"
            if use_spread and exposure.get("broker_margin_available") is False:
                warnings.append("BROKER_MARGIN_UNAVAILABLE_USING_MAXIMUM_LOSS")
        available_capital = min(settings.option_capital, float(exposure.get("available_capital", settings.option_capital)))
        lots_by_risk = floor(min(settings.short_put_max_risk_per_trade, settings.option_risk_per_trade) / max_loss_per_lot) if max_loss_per_lot > 0 else 0
        lots_by_capital = floor(available_capital / capital_per_lot) if capital_per_lot > 0 else 0
        lots = min(lots_by_risk, lots_by_capital)
        require(lots_by_risk >= 1, ShortPutRejectionCode.MAX_LOSS_EXCEEDED, "Maximum loss for one lot exceeds the option risk budget.")
        require(lots_by_capital >= 1, ShortPutRejectionCode.CAPITAL_INSUFFICIENT, "Available capital cannot fund one lot.")
        maximum_profit = max_profit_per_lot * lots
        maximum_loss = max_loss_per_lot * lots
        capital_required = capital_per_lot * lots
        ror = maximum_profit / maximum_loss * 100 if maximum_loss > 0 else 0
        rom = maximum_profit / capital_required * 100 if capital_required > 0 else 0
        annualized_rom = rom * 365 / max(dte, 1)
        require(ror >= settings.short_put_min_return_on_risk_percent, ShortPutRejectionCode.MAX_LOSS_EXCEEDED, "Return on risk is below the configured minimum.")
        require(rom >= settings.short_put_min_return_on_margin_percent, ShortPutRejectionCode.MAX_LOSS_EXCEEDED, "Return on margin is below the configured minimum.")

        approved = not rejections
        actionable_recommendations = []
        if any(item.code == ShortPutRejectionCode.PREMIUM_TOO_LOW for item in rejections):
            actionable_recommendations.extend([
                "Wait for IV expansion before selling this strike.",
                "Evaluate the next monthly expiry for a higher executable premium.",
            ])
        evaluation = ShortPutEvaluation("GOOD" if spread <= 5 else "ACCEPTABLE", "DEFINED_RISK" if use_spread else "LARGE_DOWNSIDE_RISK", approved, rejections, warnings)
        quantity = lots * lot_size
        sold_leg = ShortPutLeg("SELL", sold.symbol, sold.strike, premium, sold.bid, sold.ask, quantity)
        hedge_leg = ShortPutLeg("BUY", hedge.symbol, hedge.strike, hedge_premium, hedge.bid, hedge.ask, quantity) if hedge and use_spread else None
        plan = ShortPutStrategyPlan(
            approved, symbol, setup, strategy, candidate, sold_leg, hedge_leg, round(net_credit, 2),
            round(maximum_profit, 2), round(maximum_loss, 2), round(capital_required, 2), round(ror, 2),
            round(rom, 2), round(sold.strike - net_credit, 2), lots, evaluation,
            rejections[0].code if rejections else None, [item.reason for item in rejections], warnings,
            margin_source, "PORTFOLIO_SECTOR_AND_CAPITAL_CHECKED", strike_search,
            round(annualized_rom, 2), actionable_recommendations,
        )
        if all_strikes_rejected:
            plan.best_rejected_candidate = strike_search["best_rejected_candidate"]
            plan.candidate = None
            plan.sold_leg = None
            plan.hedge_leg = None
        return asdict(plan)
