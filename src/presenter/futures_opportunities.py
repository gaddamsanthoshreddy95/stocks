"""Detailed A/B/C reports without changing the existing futures presenter."""
from src.news.catalyst_watchlist import ist_time


class FuturesOpportunitiesPresenter:
    @staticmethod
    def value(value):
        return 'UNKNOWN' if value is None else f'{value:,.4f}' if isinstance(value, (int, float)) else str(value)

    @classmethod
    def candidate(cls, item):
        n = cls.value
        evidence = (item.get('futures_setup') or {}).get('evidence') or item.get('evidence', {})
        contract, plan = item.get('contract', {}), item.get('plan', {})
        lines = [f"### {item['symbol']} — {item['side']} — {item['final_decision']}", '',
                 f"Setup: {item['setup_type']}; technical score {n(item['technical_score'])}/100; "
                 f"BULLISH_SCORE {n(item['BULLISH_SCORE'])}; BEARISH_SCORE {n(item['BEARISH_SCORE'])}.",
                 f"Score basis: {item.get('technical_score_timeframe', 'DAILY_DISCOVERY')}; "
                 "BULLISH_SCORE and BEARISH_SCORE are independent daily discovery scores.",
                 f"Contract: {contract.get('tradingsymbol', 'UNKNOWN')}; expiry {contract.get('expiry', 'UNKNOWN')}; "
                 f"lot size {n(contract.get('lot_size'))}. Checked: {ist_time(item.get('checked_at'))}.",
                 f"RSI(14): {n(evidence.get('rsi_14'))}; MACD: {n(evidence.get('macd'))}; "
                 f"signal: {n(evidence.get('macd_signal'))}; histogram: {n(evidence.get('macd_histogram'))}; "
                 f"ADX(14): {n(evidence.get('adx_14'))}; +DI/-DI: {n(evidence.get('plus_di'))}/{n(evidence.get('minus_di'))}.",
                 f"EMA alignment: {evidence.get('ema_alignment', 'UNKNOWN')}; values: {evidence.get('ema_values', {})}.",
                 f"Pattern: {evidence.get('pattern', 'UNKNOWN')}; VWAP: {n(evidence.get('vwap'))}; "
                 f"relationship: {evidence.get('vwap_relationship', 'UNKNOWN')}; volume ratio: {n(evidence.get('relative_volume'))}.",
                 f"Sector: {item['sector']}; stock/sector relative performance: {item.get('evidence', {}).get('sector_relative')}; "
                 f"sector versus Nifty: {item.get('evidence', {}).get('sector_vs_nifty')}.",
                 f"Entry ₹{n(plan.get('entry'))}; target ₹{n(plan.get('target'))}; stop ₹{n(plan.get('stop_loss'))}; "
                 f"lots {n(plan.get('number_of_lots'))}; quantity {n(plan.get('quantity'))}.",
                 f"Gross profit/loss ₹{n(plan.get('gross_profit_at_target'))}/₹{n(plan.get('gross_loss_at_stop'))}; "
                 f"net profit/loss ₹{n(plan.get('net_profit_at_target'))}/₹{n(plan.get('net_loss_at_stop'))}; "
                 f"net reward/risk {n(plan.get('net_risk_reward'))}.",
                 f"Estimated target costs: {plan.get('target_costs', {})}; stop costs: {plan.get('stop_costs', {})}.",
                 f"Expected slippage: {n(item.get('expected_slippage_bps'))} bps; margin: "
                 f"{plan.get('margin_status', 'UNKNOWN')} ₹{n(plan.get('margin_requirement'))}.",
                 f"Sizing: {plan.get('sizing_basis', 'No validated execution sizing')}.",
                 f"Movement basis: {plan.get('movement_basis','FUTURES')}; underlying entry/target/stop: "
                 f"{plan.get('underlying_entry')}/{plan.get('underlying_target')}/{plan.get('underlying_stop_loss')}; {plan.get('basis_assumption') or ''}.",
                 f"Data freshness: {item.get('data_freshness',{})}; statistical estimate: {item.get('probability_estimate',{})}.",
                 f"Target-first historical rate: {n(item.get('historical', {}).get('target_first_percent'))}%; "
                 f"historical evidence: {item.get('historical', {})}.",
                 f"Move: {n(evidence.get('move_percent'))}%; ATR consumed: {n(evidence.get('atr_consumed_fraction'))}; "
                 f"EMA21 extension: {n(evidence.get('ema21_extension_atr'))} ATR; target space: {item.get('futures_target_space', 'UNKNOWN')}.",
                 f"Futures quote: {item.get('futures_quote', {})}.",
                 f"Intraday price/OI change: {item.get('intraday_oi_change', 'UNKNOWN')}.",
                 f"Event risk: {item.get('event_risk', {}).get('event_risk_level', 'UNKNOWN')}; "
                 f"coverage: {item.get('event_risk', {}).get('event_data_availability_state', 'UNKNOWN')}.",
                 f"Exact reasons: {', '.join(item.get('reason_codes', [])) or 'None recorded'}.", '',
                 '| Execution check | Status | Measurements and thresholds | Reasons |',
                 '|---|---|---|---|']
        for key, check in item.get('execution_checks', {}).items():
            lines.append(f"| {key} | {check['status']} | {check.get('factors', {})} | {', '.join(check.get('reason_codes', []))} |")
        lines.append('')
        short_entry = item.get('short_entry', {})
        if item['side'] == 'SHORT':
            lines.extend([f"SHORT entry state: {short_entry.get('state', 'NOT_EXECUTION_VALIDATED')}; "
                f"trigger ₹{n(short_entry.get('trigger_price', evidence.get('short_trigger_price')))}; "
                f"remaining downside {n(short_entry.get('remaining_downside_percent'))}% "
                f"(₹{n(short_entry.get('remaining_downside_points'))}); "
                f"setup invalidation ₹{n(short_entry.get('setup_invalidation_price'))}.", ''])
        if item.get('daily_discovery') or item.get('futures_execution_confirmation'):
            lines.extend([f"DAILY DISCOVERY confirmation/timing: {item.get('daily_discovery',{}).get('confirmed', 'UNKNOWN')} / {item.get('daily_discovery',{}).get('timing','UNKNOWN')}; research only.",
                f"FUTURES EXECUTION confirmation/timing: {item.get('futures_execution_confirmation',{})}.",
                f"Final read-only safety checks: {item.get('scan_gates',{})}.",
                f"Entry reference: {plan.get('entry_reference_type','UNVERIFIED')}. Targets/stops are fixed strategy barriers; actual manual fills can differ.", ''])
        research = item.get('company_research', {})
        lines.extend([f"Company research: {research.get('status', 'UNKNOWN')}; "
                      f"separate fundamental assessment: {research.get('fundamental_assessment', {})}.",
                      research.get('sector_interpretation', 'Company research unavailable.'),
                      f"Research thresholds: {research.get('thresholds', {})}.", '',
                      '| Company research check | Original status | Actual values and thresholds | Exact reasons |',
                      '|---|---|---|---|'])
        for key, value in research.get('checks', {}).items():
            lines.append(f"| {key} | {value['status']} | {value.get('factors', {})} | {', '.join(value.get('reason_codes', []))} |")
        lines.extend(['', '| SHORT research interpretation | Supports/contradicts | Actual values | Reason |', '|---|---|---|---|'])
        for row in research.get('short_interpretation', []):
            lines.append(f"| {row['condition']} | {row['short_thesis']} | {row['values']} | {row['reason']} |")
        lines.append('')
        for flag in research.get('review_flags', []):
            lines.append(f"- Policy review required: {flag['check']} ({flag['status']}). {flag['reason']}")
        fundamentals = research.get('fundamentals') or {}
        for field, source in fundamentals.get('evidence', {}).items():
            if source.get('url'):
                lines.append(f"- Research source {field}: [{source.get('period', 'Report period')}]({source['url']}); {source.get('basis', '')}.")
        lines.append('')
        news = item.get('news', {})
        lines.append(f"News: {news.get('news_state', 'NOT_REQUESTED')}; sentiment {news.get('sentiment', 'UNKNOWN')}; "
                     f"checked {ist_time(news.get('checked_at'))}.")
        seen = set()
        for article in news.get('article_assessments', []) + news.get('headlines', []):
            key = (article.get('title'), article.get('url'))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- [{article.get('title', 'Headline')}]({article.get('url', '')}); "
                         f"{article.get('source', 'UNKNOWN')}; {ist_time(article.get('published'))}; "
                         f"sentiment {article.get('sentiment', 'UNKNOWN')}.")
        for event in item.get('event_risk', {}).get('matched_events', []):
            lines.append(f"- Event: {event.get('title')}; {ist_time(event.get('event_start'))}; "
                         f"{event.get('status')}; sources {event.get('source_urls_or_ids', [])}.")
        if item.get('research_only'):
            lines.extend(['', '**AFTER_MARKET_RESEARCH — RESEARCH ONLY; no live entry approval.**'])
            lines.append(f"Completed-candle entry reference: {item.get('research_entry_reference',{})}.")
        if item.get('missing_fields'):
            lines.append(f"Missing research measurements: {item['missing_fields']}.")
        if item.get('source_failures'):
            lines.append(f"Exact data-source failures: {item['source_failures']}.")
        cached_news = news.get('last_successful_analysis')
        if cached_news:
            lines.append(f"Last cached news analysis: {cached_news.get('news_state')}; sentiment {cached_news.get('sentiment')}; "
                         f"checked {ist_time(cached_news.get('checked_at'))}. Current refresh/analysis state remains {news.get('news_state')}.")
        return '\n'.join(lines)

    @classmethod
    def render(cls, report):
        lines = [f"# Intraday futures opportunities — {ist_time(report['generated_at'])}", '',
                 f"Mode: {report.get('scan_mode','REFERENCE')}; stage durations: {report.get('timings',{})}; "
                 f"Kite requests: {report.get('request_counts',{})}.", '',
                 f"Universe {report['universe_size']}; LONG discovery {report['long_discovery_count']}; "
                 f"SHORT discovery {report['short_discovery_count']}; approved {report['approved_count']}.", '',
                 report['execution_policy'], report['historical_validation'], '']
        safety = report.get('execution_safety', {})
        if safety:
            window = safety.get('entry_window', {})
            budget = safety.get('reconciliation', {})
            lines.extend([f"**READ-ONLY — manual execution in Zerodha Kite.** Executed entries today: {budget.get('executed_entries', 'UNKNOWN')}; remaining: {budget.get('remaining_entries', 'UNKNOWN')}; reconciliation: {budget.get('status','UNKNOWN')}.",
                f"New-entry cutoff: {window.get('entry_cutoff')}. {window.get('manual_exit_warning','')}",
                'DAILY DISCOVERY evaluates both directions independently. FUTURES EXECUTION uses completed five-minute candles, fresh contract depth and final safety checks.', ''])
            if window.get('manual_exit_warning_active'):
                lines.extend(['**MANUAL EXIT WARNING: '+window['manual_exit_warning']+'**', ''])
        if report.get('research_only'):
            lines.extend([f"**AFTER_MARKET_RESEARCH — RESEARCH ONLY. Completed session: {report.get('completed_session_as_of')}. No live trades approved.**", ''])
        if report.get('source_failures'):
            lines.extend([f"Data-source diagnostics: {report['source_failures']}", ''])
        for key, title in [('report_a', 'Report A — Top Bullish Futures Candidates'),
                           ('report_b', 'Report B — Top Bearish Futures Candidates'),
                           ('report_c', 'Report C — Combined Eligible Trading Opportunities')]:
            lines.extend([f'## {title}', ''])
            if not report[key]:
                lines.extend(['No eligible opportunities.' if key == 'report_c' else 'No candidates available.', ''])
            for item in report[key]:
                lines.extend([cls.candidate(item), ''])
        from src.futures.rejected_analysis import build_report_d, render_report_d
        original = '\n'.join(lines) + '\n\n' + render_report_d(report.get('report_d') or build_report_d(report))
        from src.futures.report_e import build_report_e, render_report_e
        return original + '\n\n' + render_report_e(report.get('report_e') or build_report_e(report))
