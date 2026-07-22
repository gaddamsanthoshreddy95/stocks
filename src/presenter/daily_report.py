"""Human-readable CLI presentation for the final daily trade report."""

from __future__ import annotations

from typing import Any


class DailyReportPresenter:
    """Render a report returned by ``TradingPlatform.daily_report``."""

    @staticmethod
    def _money(value: Any) -> str:
        return "N/A" if value is None else f"₹{float(value):,.2f}"

    @staticmethod
    def _timing_metric(name: str, value: Any) -> str:
        count_metrics = {"news_stocks_requested", "candidates_enriched",
                         "event_sources_requested", "event_sources_available",
                         "events_detected", "event_clusters_created"}
        if name in count_metrics:
            return f"{name:<32}: {value} count"
        if name.endswith("_seconds"):
            return f"{name:<32}: {value}s"
        return f"{name:<32}: {value}"

    @staticmethod
    def _option_approval_status(trade: dict[str, Any]) -> str:
        """Read the sole canonical option-trade approval result."""
        return str((trade.get("option_trade_approval") or {}).get("status", "UNAVAILABLE"))

    @classmethod
    def render(cls, report: dict[str, Any]) -> str:
        line = "=" * 68
        market, summary = report["market"], report["summary"]
        rows = [line, "                    AI TRADING ASSISTANT", line,
                f"DATE                 : {report['date']}",
                f"MARKET               : {market['regime']}",
                f"MARKET CONFIDENCE    : {market['confidence']}%",
                f"RANKING MODE         : {report.get('ranking_mode', 'EXPECTED_VALUE')}", line]
        for trade in [*report["trades"], *report.get("watchlist", [])]:
            levels, risk = trade["levels"], trade["risk"]
            rows.extend([
                f"{trade['status']} #{trade['rank']} — {trade['symbol']}", "-" * 68,
                "QUALITY",
                f"Quality grade        : {trade.get('quality_grade', 'N/A')} — {trade.get('quality_label', 'Unrated')}",
                f"Quality score        : {trade.get('quality_score', 0)}/100",
                "SETUP",
                f"Setup                : {trade['setup']}",
                "ENTRY CONFIRMATION",
                f"Confirmation         : {'PASSED' if trade.get('entry_confirmation', {}).get('passed') else 'FAILED'} "
                f"({trade.get('entry_confirmation', {}).get('score', 0)}%)",
                "MARKET AND NEWS CONTEXT",
                f"Market alignment     : {trade['market_alignment']['status']}",
                f"Sector               : {trade['sector']}",
                f"Current price        : {cls._money(trade['current_price'])}",
                f"AI score             : {trade['ai_score']}/100",
                f"News sentiment       : {trade['news']['sentiment']} ({trade['news']['score']:+.1f})",
                f"News collection      : {trade['news'].get('collection_state', 'UNKNOWN')} ({trade['news'].get('article_count', 0)} articles)",
                f"News sentiment state : {trade['news'].get('analysis_state', 'UNKNOWN')}",
                f"News analysis        : {trade['news'].get('analysis_method', 'UNAVAILABLE')} / {trade['news'].get('model', 'N/A')}",
                f"News materiality     : {trade['news'].get('materiality', 'UNAVAILABLE')} / impact {trade['news'].get('trade_impact', 'NONE')}",
                f"Model confidence     : {trade['model_confidence']['decision_confidence']}%",
                f"Estimated probability: {trade['model_confidence']['estimated_probability']}%",
                f"Stay above adverse   : {trade.get('adverse_move_risk', {}).get('probability_stays_above_adverse_barrier', 'N/A')}% "
                f"(barrier -{trade.get('adverse_move_risk', {}).get('adverse_barrier_percent', 3)}%)",
                f"Target before adverse: {trade.get('adverse_move_risk', {}).get('probability_target_before_adverse_barrier', 'N/A')}%",
                f"No overnight >barrier: {trade.get('adverse_move_risk', {}).get('probability_no_overnight_gap_beyond_barrier', 'N/A')}%",
                f"Path data resolution  : {trade.get('adverse_move_risk', {}).get('data_resolution', 'UNAVAILABLE')}",
                f"Barrier samples      : {trade.get('adverse_move_risk', {}).get('sample_count', 0)}",
                f"Stock-selection gate : {'PASSED' if trade.get('stock_selection_filters', {}).get('passed') else 'FAILED'}",
                f"Failed stock filters : {', '.join(trade.get('stock_selection_filters', {}).get('failed_checks', [])) or 'None'}",
                f"Strategy             : {trade['strategy']} ({trade['time_frame']})",
                f"Trend / momentum     : {trade['technical']['trend']} / {trade['technical']['momentum']}",
                f"Stock liquidity      : {trade['stock_liquidity']['status']} ({trade['stock_liquidity']['average_daily_turnover_crore']} cr avg/day)",
                f"F&O trust filter      : {trade['trust']['status']} ({trade['trust']['score']}/100)",
                f"Sector context       : {trade['sector_context'].get('status', 'UNAVAILABLE')} / "
                f"{trade['sector_context']['rating']}",
                f"Relative strength    : {trade['relative_strength'].get('status', 'UNAVAILABLE')} / "
                f"{trade['relative_strength']['rating']} / "
                f"{trade['relative_strength'].get('score', 'N/A')}",
                f"Support / resistance : {cls._money(levels['support'])} / {cls._money(levels['resistance'])}",
                f"Entry / stop         : {cls._money(levels['entry'])} / {cls._money(levels['stop_loss'])}",
                f"Targets              : {cls._money(levels['target_1'])}, {cls._money(levels['target_2'])}, {cls._money(levels['target_3'])}",
                f"Target model         : {levels.get('target_basis', 'NEAREST_RESISTANCE')} ({levels.get('breakout_probability', 0)}% breakout probability)",
                f"Expected reward      : {cls._money(levels.get('expected_reward'))} (nearest target {cls._money(levels.get('nearest_target_reward'))})",
                f"Expected risk/reward : 1:{levels['risk_reward']}",
                f"R:R rejection floor  : 1:{trade.get('risk_reward_policy', {}).get('absolute_rejection_min_rr', 'N/A')}",
                f"R:R execution minimum: 1:{trade.get('risk_reward_policy', {}).get('executable_trade_min_rr', 'N/A')}",
                f"Expected value       : {cls._money(trade.get('expected_value', {}).get('amount'))} ({trade.get('expected_value', {}).get('risk_multiple', 0):+.3f}R)",
            ])
            event = trade.get("event_risk", {})
            rows.extend([
                "EVENT RISK", "-" * 68,
                f"Risk level            : {event.get('event_risk_level', 'UNAVAILABLE')} — {event.get('event_risk_score', 0)}/100",
                f"Event direction       : {event.get('event_direction', 'UNCERTAIN')}",
                f"Company type          : {event.get('company_type', 'UNKNOWN')}",
                f"Classification conf. : {event.get('classification_confidence', event.get('event_confidence', 0))}%",
                f"Primary freshness     : {event.get('primary_event_freshness_state', 'UNAVAILABLE')}",
                f"Source coverage       : {event.get('aggregate_source_coverage_state', 'UNAVAILABLE')}",
                f"Event data state      : {event.get('event_data_availability_state', 'UNAVAILABLE')}",
                f"Supporting freshness  : {event.get('supporting_sources_freshness_state', 'UNAVAILABLE')}",
                f"Effective confidence  : {event.get('effective_confidence', 0)}%",
                f"Stock/sector/market  : {event.get('stock_specific_score', 0)} / {event.get('sector_specific_score', 0)} / {event.get('market_wide_score', 0)}",
                f"Primary category      : {event.get('primary_category', 'NONE')}",
                f"Impact / decay        : {event.get('impact_duration', 'N/A')} / {event.get('decay_model', 'N/A')} ({event.get('current_decay_factor', 1):.2f})",
                f"Overnight gap risk    : {event.get('gap_risk_score', 0)}/100",
                f"Overnight hold        : {'ALLOWED' if trade.get('overnight_hold_allowed', True) else 'BLOCKED'}",
                f"Base readiness        : {event.get('base_readiness', trade.get('execution_readiness_score', 0))}%",
                f"Directional bonus     : +{event.get('directional_bonus', 0)}",
                f"Volatility penalty    : -{event.get('volatility_penalty', 0)}",
                f"Gap-risk penalty      : -{event.get('gap_risk_penalty', 0)}",
                f"Event-risk penalty    : -{event.get('readiness_penalty', 0)}",
                f"Data uncertainty pen. : -{event.get('event_data_uncertainty_penalty', 0)}",
                f"Adjusted readiness    : {event.get('adjusted_readiness', trade.get('execution_readiness_score', 0))}%",
                f"Event position scale  : {event.get('position_size_multiplier', 1) * 100:.0f}%",
                "EXECUTION READINESS",
                f"Execution readiness  : {trade['execution_readiness_score']}% — {trade['execution_status']} ({trade['execution_label']})",
                f"Execution policy     : {trade['trade_readiness'].get('policy', 'N/A')} — execute at {trade['trade_readiness'].get('execute_threshold', 'N/A')}%",
                "TRADE ELIGIBILITY",
                f"Trade eligibility    : {trade['trade_eligibility']['status']}",
                "OPTION STRUCTURE",
                f"Structure validation : {trade.get('option_structure', {}).get('status', 'UNAVAILABLE')}",
                "OPTION TRADE APPROVAL",
                f"Option approval      : {cls._option_approval_status(trade)}",
                "POSITION",
                f"Position size        : {risk['quantity']} shares (max risk {cls._money(risk['risk_amount'])})",
                f"Planned if confirmed : {trade.get('planned_position_if_confirmed', {}).get('quantity', 0)} shares",
                f"Risk scaling         : {trade['risk_policy']['position_scale'] * 100:.0f}% position; {trade['risk_policy']['effective_risk_percent']}% effective risk",
                "FINAL ACTION",
                f"Final action          : {trade['final_action']}",
                f"Base / final position : {risk.get('base_market_adjusted_quantity', risk['quantity'])} / {risk['quantity']} shares",
                f"Strategy restriction  : {', '.join(trade.get('strategy_restrictions', [])) or 'NONE'}",
            ])
            for matched in event.get("matched_events", [])[:4]:
                rows.append(f"  ✓ {matched.get('title')} ({matched.get('candidate_event_score', 0)}/100)")
            for warning in event.get("warnings", [])[:2]:
                rows.append(f"  ! {warning}")
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
                rows.append(f"Option entry check   : {cls._option_approval_status(trade)}")
                for code in trade.get("option_trade_approval", {}).get("rejection_codes", []):
                    rows.append(f"Option rejection     : {code}")
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
                    marker = "–" if not check.get("counted", True) else "✓" if check["passed"] else "✗"
                    rows.append(f"  {marker} {check['name']}: {check['detail']}")
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
                    f"selected_contract={'NONE' if strike_search.get('selected_strike') is None else strike_search['selected_strike']}"
                )
                best_rejected = strike_search.get("best_rejected_candidate")
                if best_rejected:
                    rows.append(
                        f"Best rejected contract: {best_rejected.get('strike')} PE — "
                        f"{', '.join(best_rejected.get('rejection_codes', []))}"
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
            f"Event-risk reviewed  : {summary.get('event_risk_reviewed', 0)}",
            f"Event risk V/L/M/H/X : {summary.get('event_risk_very_low', 0)}/{summary.get('event_risk_low', 0)}/{summary.get('event_risk_medium', 0)}/{summary.get('event_risk_high', 0)}/{summary.get('event_risk_extreme', 0)}",
            f"Event blocked        : {summary.get('event_blocked_candidates', 0)}",
            f"Event size reduced   : {summary.get('event_reduced_positions', 0)}",
            f"Overnight blocked    : {summary.get('overnight_blocked_candidates', 0)}",
            f"  Event-level blocks : {summary.get('overnight_event_level_blocks', 0)}",
            f"  Gap-risk blocks    : {summary.get('overnight_gap_risk_blocks', 0)}",
            f"  Market-policy blocks: {summary.get('overnight_market_policy_blocks', 0)}",
            f"  Other blocks       : {summary.get('overnight_other_blocks', 0)}",
            f"Event data C/P/U/F/N : {summary.get('event_data_complete', 0)}/{summary.get('event_data_partial', 0)}/{summary.get('event_data_unavailable', 0)}/{summary.get('event_data_failed', 0)}/{summary.get('event_data_not_requested', 0)}",
            f"Event data unavailable: {summary.get('event_data_unavailable', 0)}",
            f"Raw/candidate/background events: {summary.get('raw_events_detected', 0)}/{summary.get('candidate_impacting_events', 0)}/{summary.get('background_market_wide_events', 0)}",
            f"Common event category: {summary.get('common_event_category', 'N/A')}",
            f"Highest event risk   : {summary.get('highest_risk_candidate', 'N/A')}",
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
            f"Highest candidate sector: {summary.get('highest_ranked_candidate_sector', 'N/A')}",
            f"Best option strategy : {summary['best_option_strategy']}",
            f"Average trade probability: {summary['average_probability']}%" if summary.get("average_probability") is not None else "Average trade probability: N/A",
            f"Average watchlist probability: {summary['average_watchlist_probability']}%" if summary.get("average_watchlist_probability") is not None else "Average watchlist probability: N/A",
            f"Market risk          : {summary['market_risk']}",
            f"Recommendation       : {summary['recommendation']}", line,
            "EXECUTION TIMINGS", "-" * 68,
            *[cls._timing_metric(name, value)
              for name, value in report.get("timings", {}).items()],
            line,
            "FILTER FUNNEL", "-" * 68,
            *[f"{item['stage']:<20}: {item['input']} in / {item['passed']} passed / {item['rejected']} rejected"
              + (f" / {item['deferred']} deferred" if item.get('deferred') else "")
              for item in report.get("filter_stages", [])],
            line,
            "CONTEXT REVIEW", "-" * 68,
            *[f"{name:<20}: {values['passed']} passed / {values['failed']} failed / {values['unavailable']} unavailable"
              + (f" / {values['fetched']} fetched / {values['analysis_failed']} analysis failed"
                 f" / {values.get('not_requested_by_policy', 0)} policy-not-requested"
                 f" / {values.get('fetch_failed', 0)} fetch-failed"
                 f" / {values.get('no_relevant_news', 0)} no-relevant-news"
                 if name == "news" else "")
              for name, values in report.get("context_statistics", {}).items()],
            line,
            "SECTOR RANKING", "-" * 68,
            *[f"#{item['rank']} {item['sector']}: sector_market_score="
              f"{item.get('sector_market_score', 'UNAVAILABLE')}; candidate_aggregate_score="
              f"{item.get('candidate_aggregate_score', 'N/A')}/100; "
              f"status={item.get('market_data_status', 'UNAVAILABLE')}; basis={item.get('ranking_basis')}"
              for item in report.get("sector_ranking", [])[:10]],
            line,
            "DEPENDENCY HEALTH", "-" * 68,
            f"spaCy                : {report.get('dependency_health', {}).get('spacy', 'UNKNOWN')}",
            f"Entity model         : {report.get('dependency_health', {}).get('entity_model', 'UNKNOWN')} "
            f"({report.get('dependency_health', {}).get('entity_model_status', 'UNKNOWN')})",
            f"Entity classification: {report.get('dependency_health', {}).get('entity_dependent_classification', 'UNKNOWN')}",
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
