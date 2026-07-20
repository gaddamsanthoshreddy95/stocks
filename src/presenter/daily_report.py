"""Human-readable CLI presentation for the final daily trade report."""

from __future__ import annotations

from typing import Any


class DailyReportPresenter:
    """Render a report returned by ``TradingPlatform.daily_report``."""

    @staticmethod
    def _money(value: Any) -> str:
        return "N/A" if value is None else f"₹{float(value):,.2f}"

    @classmethod
    def render(cls, report: dict[str, Any]) -> str:
        line = "=" * 68
        market, summary = report["market"], report["summary"]
        rows = [line, "                    AI TRADING ASSISTANT", line,
                f"DATE                 : {report['date']}",
                f"MARKET               : {market['regime']}",
                f"MARKET CONFIDENCE    : {market['confidence']}%", line]
        for trade in [*report["trades"], *report.get("watchlist", [])]:
            levels, risk = trade["levels"], trade["risk"]
            rows.extend([
                f"{trade['status']} #{trade['rank']} — {trade['symbol']}", "-" * 68,
                f"Setup / action       : {trade['setup']} / {trade['action']}",
                f"Market alignment     : {trade['market_alignment']['status']}",
                f"Sector               : {trade['sector']}",
                f"Current price        : {cls._money(trade['current_price'])}",
                f"AI score             : {trade['ai_score']}/100",
                f"Confidence grade     : {trade.get('confidence_grade', {}).get('grade', 'N/A')} — {trade.get('confidence_grade', {}).get('label', 'Unrated')}",
                f"News sentiment       : {trade['news']['sentiment']} ({trade['news']['score']:+.1f})",
                f"News collection      : {trade['news'].get('collection_state', 'UNKNOWN')} ({trade['news'].get('article_count', 0)} articles)",
                f"News sentiment state : {trade['news'].get('analysis_state', 'UNKNOWN')}",
                f"News analysis        : {trade['news'].get('analysis_method', 'UNAVAILABLE')} / {trade['news'].get('model', 'N/A')}",
                f"News materiality     : {trade['news'].get('materiality', 'UNAVAILABLE')} / impact {trade['news'].get('trade_impact', 'NONE')}",
                f"Model confidence     : {trade['model_confidence']['decision_confidence']}%",
                f"Estimated probability: {trade['model_confidence']['estimated_probability']}%",
                f"Trade eligibility    : {trade['trade_eligibility']['status']}",
                f"Strategy             : {trade['strategy']} ({trade['time_frame']})",
                f"Trend / momentum     : {trade['technical']['trend']} / {trade['technical']['momentum']}",
                f"Stock liquidity      : {trade['stock_liquidity']['status']} ({trade['stock_liquidity']['average_daily_turnover_crore']} cr avg/day)",
                f"F&O trust filter      : {trade['trust']['status']} ({trade['trust']['score']}/100)",
                f"Sector / relative str: {trade['sector_context']['rating']}"
                f"{' (ignored)' if not trade['sector_context'].get('available') else ''} / "
                f"{trade['relative_strength']['rating']}",
                f"Support / resistance : {cls._money(levels['support'])} / {cls._money(levels['resistance'])}",
                f"Entry / stop         : {cls._money(levels['entry'])} / {cls._money(levels['stop_loss'])}",
                f"Targets              : {cls._money(levels['target_1'])}, {cls._money(levels['target_2'])}, {cls._money(levels['target_3'])}",
                f"Target model         : {levels.get('target_basis', 'NEAREST_RESISTANCE')} ({levels.get('breakout_probability', 0)}% breakout probability)",
                f"Expected reward      : {cls._money(levels.get('expected_reward'))} (nearest target {cls._money(levels.get('nearest_target_reward'))})",
                f"Expected risk/reward : 1:{levels['risk_reward']}",
                f"Expected value       : {cls._money(trade.get('expected_value', {}).get('amount'))} ({trade.get('expected_value', {}).get('risk_multiple', 0):+.3f}R)",
                f"Trade readiness      : {trade['trade_readiness']['percentage']}% — {trade['trade_readiness']['classification']}",
                f"Adaptive policy      : {trade.get('market_policy', {}).get('profile', 'N/A')} — score {trade.get('market_policy', {}).get('trade_score_minimum', 'N/A')}+, readiness {trade.get('market_policy', {}).get('readiness_minimum', 'N/A')}%+",
                f"Position size        : {risk['quantity']} shares (max risk {cls._money(risk['risk_amount'])})",
                f"Risk scaling         : {trade['risk_policy']['position_scale'] * 100:.0f}% position; {trade['risk_policy']['effective_risk_percent']}% effective risk",
                f"Option account       : {cls._money(trade['option_budget_policy']['capital_available'])} capital; {cls._money(trade['option_budget_policy']['risk_per_trade'])} max risk/trade",
            ])
            seasonal = trade.get("current_month_seasonality", {})
            rows.append(
                f"{seasonal.get('month_name', 'Month')} history        : "
                f"avg {seasonal.get('average_return_percent', 'N/A')}%, "
                f"median {seasonal.get('median_return_percent', 'N/A')}%, "
                f"wins {seasonal.get('win_rate_percent', 'N/A')}% "
                f"({seasonal.get('sample_years', 0)} years; {seasonal.get('sample_quality', 'INSUFFICIENT')})"
            )
            rows.append(
                f"Current month MTD    : {seasonal.get('current_mtd_return_percent', 'N/A')}% "
                f"({seasonal.get('versus_history', 'UNAVAILABLE')}; score impact {seasonal.get('score_adjustment', 0):+.2f})"
            )
            regime = trade.get("regime_history", {})
            rows.append(
                f"Regime history       : {regime.get('regime', 'UNAVAILABLE')} — "
                f"{regime.get('win_rate_percent', 'N/A')}% win rate, "
                f"{regime.get('sample_count', 0)} samples ({regime.get('sample_quality', 'INSUFFICIENT')}); "
                f"score impact {regime.get('score_adjustment', 0):+.2f}"
            )
            option = trade["option_strategy"]
            if option.get("available"):
                plan = option.get("trade", {})
                rows.append(f"Option strategy      : {plan.get('strategy', option.get('strategy', 'N/A'))}")
                rows.append(f"PCR / Max pain       : {option.get('pcr', 'N/A')} / {option.get('max_pain', 'N/A')}")
                rows.append(f"IV / option confidence: {option.get('iv_rank', 'N/A')} / {option.get('confidence', 'N/A')}%")
                rows.append(f"Option hedge target  : {cls._money(plan.get('hedge_strike_target'))} (technical level)")
                validation = option.get("entry_validation", {})
                rows.append(f"Option entry check   : {'APPROVED' if validation.get('approved') else 'BLOCKED'}")
                for warning in validation.get("warnings", []):
                    rows.append(f"Option warning       : {warning}")
                for leg in plan.get("legs", []):
                    rows.append(f"  {leg['side']} {leg['quantity']} × {leg['strike']} {leg['option_type']} @ {cls._money(leg['premium'])}")
                rows.append(f"Option max loss      : {cls._money(plan.get('maximum_loss'))}")
            else:
                rejection = option.get("rejection") or {}
                rows.append(f"Option strategy      : rejected [{rejection.get('code', option.get('error_type', 'UNAVAILABLE'))}]")
                rows.append(f"Option rejection     : {rejection.get('reason', option.get('reason', 'No executable option structure'))}")
                if rejection.get("code") == "RISK_BUDGET_EXCEEDED":
                    rows.append(f"Option budget        : needs {cls._money(rejection.get('required_budget'))}; available {cls._money(rejection.get('available_budget'))}")
                if rejection.get("maximum_loss") is not None:
                    rows.append(f"Option max loss      : {cls._money(rejection.get('maximum_loss'))}")
                if rejection.get("capital_required") is not None:
                    rows.append(f"Option capital       : needs {cls._money(rejection.get('capital_required'))}; available {cls._money(rejection.get('capital_available'))}")
            if trade["status"] == "WATCHLIST":
                readiness = trade["trade_readiness"]
                for check in readiness["checks"]:
                    rows.append(f"  {'✓' if check['passed'] else '✗'} {check['name']}: {check['detail']}")
            for diagnostic in levels.get("target_diagnostics", []):
                rows.append(f"Target diagnostic    : {diagnostic}")
            short_put = trade.get("short_put_strategy", {})
            if short_put.get("available"):
                candidate = short_put["candidate"]
                sold = short_put["sold_leg"]
                hedge = short_put.get("hedge_leg")
                rows.extend([
                    "SHORT PUT OPPORTUNITY", "-" * 68,
                    f"Underlying setup      : {short_put['underlying_setup']}",
                    f"Underlying price      : {cls._money(candidate['spot_price'])}",
                    f"Expiry / DTE          : {candidate['expiry']} / {candidate['dte']}",
                    f"Preferred strategy    : {short_put['strategy']}",
                    f"Sold Put              : {sold['strike']} PE @ {cls._money(sold['premium'])}",
                    f"Hedge Put             : {hedge['strike']} PE @ {cls._money(hedge['premium'])}" if hedge else "Hedge Put             : NONE — cash-secured assignment obligation",
                    f"Distance OTM          : {cls._money(candidate['strike_distance'])} / {candidate['strike_distance_percent']}%",
                    f"Distance below support: {cls._money(candidate['distance_below_support'])}",
                    f"ATR / coverage        : {cls._money(candidate['atr'])} / {candidate['atr_coverage']}x",
                    f"Delta                 : {candidate['delta'] if candidate['delta'] is not None else 'UNAVAILABLE'}",
                    f"Probability OTM       : {candidate['probability_otm'] if candidate['probability_otm'] is not None else 'UNAVAILABLE'}% [{candidate['probability_source']} — {candidate['probability_quality']}]",
                    f"Historical calibration: {candidate.get('historical_calibration', 'UNAVAILABLE')}",
                    f"OI / change reliability: {candidate['open_interest']} / {'RELIABLE' if candidate.get('change_in_oi_reliable') else 'UNAVAILABLE'}",
                    f"Option liquidity      : {short_put['evaluation']['liquidity_status']}",
                    f"Net credit            : {cls._money(short_put['net_credit'])}",
                    f"Maximum profit / loss : {cls._money(short_put['maximum_profit'])} / {cls._money(short_put['maximum_loss'])}",
                    f"Breakeven             : {cls._money(short_put['breakeven'])}",
                    f"Capital or margin     : {cls._money(short_put['capital_required'])}",
                    f"Margin source         : {short_put.get('margin_source', 'DEFINED_MAXIMUM_LOSS')}",
                    f"Exposure checks       : {short_put.get('exposure_status', 'AVAILABLE_CAPITAL_CHECKED')}",
                    f"Return risk / margin  : {short_put['return_on_risk_percent']}% / {short_put['return_on_margin_percent']}%",
                    f"Lots                  : {short_put['lots']}",
                    "Short Put eligibility : APPROVED",
                ])
                for warning in short_put.get("warnings", []):
                    rows.append(f"Short Put warning     : {warning}")
            elif short_put:
                rows.append(f"Short Put eligibility : REJECTED [{short_put.get('rejection_code', 'UNKNOWN')}]")
                for reason in short_put.get("rejection_reasons", [])[:3]:
                    rows.append(f"Short Put rejection   : {reason}")
            strike_search = short_put.get("strike_search", {}) if short_put else {}
            if strike_search:
                rows.append(
                    f"Short Put strike scan : {strike_search['evaluated']} contracts evaluated "
                    f"in {strike_search['band']['lower']}–{strike_search['band']['upper']} band; "
                    f"selected {strike_search['selected_strike']}"
                )
                rows.extend([
                    "SHORT PUT STRIKE EVALUATION", "-" * 112,
                    f"{'Strike':>8} {'Premium':>10} {'Delta':>9} {'ATR':>8} {'Prob OTM':>10} {'Model':>16} {'Score':>8}  Result",
                ])
                for item in sorted(strike_search.get("evaluations", []), key=lambda row: row["strike"]):
                    delta = "N/A" if item.get("delta") is None else f"{item['delta']:.3f}"
                    probability = "N/A" if item.get("probability_otm") is None else f"{item['probability_otm']:.1f}%"
                    result = ("PASS" if item.get("result") == "PASS" else
                              "REJECT: " + ", ".join(item.get("rejection_codes", [])))
                    rows.append(
                        f"{item['strike']:>8g} {cls._money(item.get('premium')):>10} {delta:>9} "
                        f"{item.get('atr_coverage', 0):>7.2f}x {probability:>10} "
                        f"{item.get('probability_source', 'N/A'):>16} {item.get('score', 0):>7.1f}  {result}"
                    )
            rows.append("Reasons              : " + "; ".join(trade["ai_reasoning"][:3]))
            if trade["validation"]["conflicts"]:
                rows.append("Conflicts            : " + "; ".join(trade["validation"]["conflicts"]))
            for headline in trade["news"].get("headlines", [])[:2]:
                rows.append(f"News                 : {headline['title']}")
            rows.append(line)
        rows.extend([
            "TODAY'S MARKET SUMMARY", "-" * 68,
            f"Stocks scanned       : {summary['stocks_scanned']}",
            f"Stocks shortlisted   : {summary['stocks_shortlisted']}",
            f"Successfully analysed: {summary.get('analysis_succeeded', 0)}",
            f"Failed analysis      : {summary.get('analysis_failed', 0)}",
            f"Technical candidates : {summary.get('technical_passed', 0)}",
            f"Liquidity-qualified  : {summary.get('liquidity_passed', 0)}",
            f"Trust-qualified      : {summary.get('trust_passed', 0)}",
            f"Context-reviewed     : {summary.get('context_reviewed', 0)}",
            f"Risk-valid           : {summary.get('risk_valid', 0)}",
            f"Market-aligned       : {summary.get('market_aligned', 0)}",
            f"Watchlist candidates : {summary.get('watchlisted', 0)}",
            f"Option-confirmed     : {summary.get('option_confirmed', 0)}",
            f"Conflicts rejected   : {summary.get('conflicts_rejected', 0)}",
            f"Trades generated     : {summary['trades_generated']}",
            f"Short Put reviewed   : {summary.get('short_put_reviewed', 0)}",
            f"Short Put approved   : {summary.get('short_put_approved', 0)}",
            f"Cash-secured approved: {summary.get('cash_secured_put_approved', 0)}",
            f"Bull Put approved    : {summary.get('bull_put_spread_approved', 0)}",
            f"Short Put rejected   : {summary.get('short_put_rejected', 0)}",
            f"Avg probability OTM  : {summary.get('average_probability_otm', 'N/A')}%",
            f"Avg OTM distance     : {summary.get('average_otm_distance', 'N/A')}%",
            f"Avg short-Put ROR    : {summary.get('average_short_put_return_on_risk', 'N/A')}%",
            f"Common SP rejection  : {summary.get('most_common_short_put_rejection', 'N/A')}",
            f"Best sector          : {summary['best_sector']}",
            f"Best option strategy : {summary['best_option_strategy']}",
            f"Average trade probability: {summary['average_probability']}%" if summary.get("average_probability") is not None else "Average trade probability: N/A",
            f"Average watchlist probability: {summary['average_watchlist_probability']}%" if summary.get("average_watchlist_probability") is not None else "Average watchlist probability: N/A",
            f"Market risk          : {summary['market_risk']}",
            f"Recommendation       : {summary['recommendation']}", line,
            "EXECUTION TIMINGS", "-" * 68,
            *[f"{name:<32}: {value}{' stocks' if name.endswith('_requested') or name == 'candidates_enriched' else 's'}"
              for name, value in report.get("timings", {}).items()],
            line,
            "FILTER FUNNEL", "-" * 68,
            *[f"{item['stage']:<20}: {item['input']} in / {item['passed']} passed / {item['rejected']} rejected"
              + (f" / {item['deferred']} deferred" if item.get('deferred') else "")
              for item in report.get("filter_stages", [])],
            line,
            "CONTEXT REVIEW", "-" * 68,
            *[f"{name:<20}: {values['passed']} passed / {values['failed']} failed / {values['unavailable']} unavailable"
              for name, values in report.get("context_statistics", {}).items()],
            line,
            "SECTOR RANKING", "-" * 68,
            *[f"#{item['rank']} {item['sector']}: {item['score']}/100 ({item['rating']}, {item['candidate_count']} candidates)"
              for item in report.get("sector_ranking", [])[:10]],
            line,
            "HISTORICAL LEARNING", "-" * 68,
            f"Completed outcomes   : {report.get('historical_learning', {}).get('completed_outcomes', 0)}",
            f"Calibration stage    : {report.get('historical_learning', {}).get('calibration_stage', 'CALIBRATING')} / target {report.get('historical_learning', {}).get('recommended_validation_samples', 200)} outcomes",
            f"Mean Brier score     : {report.get('historical_learning', {}).get('mean_brier_score', 'N/A')}",
            f"Average MFE / MAE    : {report.get('historical_learning', {}).get('average_mfe_percent', 'N/A')}% / {report.get('historical_learning', {}).get('average_mae_percent', 'N/A')}%",
            *[f"{item['symbol']}: {item['recommendations']} recommendations, {item['wins']}W/{item['losses']}L, {item['win_rate_percent']}% ({item['confidence']})"
              for item in report.get("historical_learning", {}).get("by_symbol", [])[:5]],
            *[f"{item['setup']} in {item['market_regime']}: {item['recommendations']} samples, {item['win_rate_percent']}% ({item['confidence']})"
              for item in report.get("historical_learning", {}).get("by_setup_and_regime", [])[:5]],
            line,
            *(
                ["REJECTED CANDIDATES", "-" * 68]
                + [f"{item['symbol']}: {'; '.join(item['reasons'])}" for item in report.get("rejected", [])[:10]]
                + [line]
                if report.get("rejected") else []
            ),
            "Research and paper-trading use only; no order has been placed.",
        ])
        return "\n".join(rows)
