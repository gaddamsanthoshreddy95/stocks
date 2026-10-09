"""Final read-only eligibility checks shared by CLI, API and dashboard reports."""
from copy import deepcopy
from src.futures.sessions import completed_session_check, entry_window, ist, CALCULATION_VERSION

REQUIRED_SCAN_GATES = ('executed_entry_budget', 'intraday_entry_window',
                       'latest_completed_futures_candle', 'final_futures_quote_freshness',
                       'directional_consistency', 'fixed_strategy_policy')


def finalize(report, frames, reconciliation, now, config, max_snapshot_age=120):
    """May withdraw approval; never manufactures an approval or consumes a trade slot."""
    now = ist(now)
    from src.quality.futures_execution import FuturesExecutionConfig
    quote_limit = FuturesExecutionConfig.from_env().maximum_quote_age_seconds
    window = entry_window(now, config)
    budget = deepcopy(reconciliation) if isinstance(reconciliation, dict) else {'status':'UNKNOWN', 'reasons':['BROKER_RECONCILIATION_UNAVAILABLE']}
    frames = frames if isinstance(frames, dict) else {}
    try:
        if not 0 <= (now-ist(budget['checked_at'])).total_seconds() <= max_snapshot_age:
            raise ValueError('Stale reconciliation')
    except (ValueError, TypeError, KeyError):
        budget.update(status='UNKNOWN', reasons=['BROKER_RECONCILIATION_STALE_OR_UNAVAILABLE'])
    items = report['reviewed']
    by_symbol = {}
    for item in items:
        setup = item.get('futures_setup') or {}
        if setup.get('confirmed') and setup.get('timing') == 'READY':
            by_symbol.setdefault(item['symbol'], set()).add(item['side'])
    conflicts = {symbol for symbol, sides in by_symbol.items() if len(sides) > 1}
    policy_ok = (config.target_fraction == .003 and config.stop_fraction == .002
                 and config.minimum_net_rr >= 1 and config.maximum_daily_entries == 2)
    for item in items:
        candle = completed_session_check(frames.get(item['symbol']+':'+item['side'], frames.get(item['symbol'])), now)
        try:
            quote_ok = 0 <= (now-ist(item.get('futures_quote', {})['timestamp'])).total_seconds() <= quote_limit
        except (ValueError, TypeError, KeyError):
            quote_ok = False
        gates = {
            'executed_entry_budget': {'status': budget['status'], 'reason_codes': budget.get('reasons', []),
                'executed_entries': budget.get('executed_entries'), 'remaining_entries': budget.get('remaining_entries'),
                'checked_at': budget.get('checked_at')},
            'intraday_entry_window': deepcopy(window),
            'latest_completed_futures_candle': candle,
            'final_futures_quote_freshness': {'status': 'PASS' if quote_ok else 'UNKNOWN',
                'reason_codes': ['FINAL_FUTURES_QUOTE_FRESH' if quote_ok else 'QUOTE_EXPIRED_OR_UNVERIFIED_AT_FINAL_REPORT'],
                'checked_at': now.isoformat()},
            'directional_consistency': {'status': 'FAIL' if item['symbol'] in conflicts else 'PASS',
                'reason_codes': ['CONTRADICTORY_CONFIRMED_FUTURES_DIRECTIONS' if item['symbol'] in conflicts else 'NO_CONTRADICTORY_CONFIRMED_FUTURES_DIRECTIONS']},
            'fixed_strategy_policy': {'status': 'PASS' if policy_ok else 'FAIL',
                'reason_codes': ['FIXED_FUTURES_STRATEGY_VERIFIED' if policy_ok else 'UNAPPROVED_STRATEGY_CONFIGURATION']}}
        item['scan_gates'] = gates
        failed = [name for name, gate in gates.items() if gate['status'] == 'FAIL']
        unknown = [name for name, gate in gates.items() if gate['status'] != 'PASS' and name not in failed]
        # Preserve existing research rejections/UNKNOWN; withdraw only execution approval.
        if item.get('final_decision') == 'APPROVED' and (failed or unknown):
            item['decision_before_final_safety'] = 'APPROVED'
            item['final_decision'] = 'UNKNOWN' if unknown else 'REJECT'
        item['reason_codes'] = list(dict.fromkeys(item.get('reason_codes', []) +
            [code for name in failed+unknown for code in gates[name]['reason_codes']]))
        if item.get('short_entry') and item.get('final_decision') != 'APPROVED':
            item['short_entry']['execution_approved'] = False
            if item['short_entry'].get('state') == 'VALID_SHORT_ENTRY':
                item['short_entry']['state'] = 'UNVERIFIED' if unknown else 'REJECTED'
        setup = item.get('futures_setup') or {}
        item['futures_execution_confirmation'] = {
            'basis': 'EXACT_CONTRACT_COMPLETED_5_MINUTE', 'confirmed': setup.get('confirmed'),
            'timing': ('READY' if item.get('final_decision') == 'APPROVED' and not failed and not unknown
                       else 'BLOCKED' if failed else 'UNKNOWN' if unknown else 'NOT_READY'),
            'setup_timing': setup.get('timing', 'UNKNOWN'), 'technical_score': setup.get('technical_score'),
            'signal_timestamp': setup.get('evidence', {}).get('signal_timestamp'),
            'entry_ready': item.get('final_decision') == 'APPROVED' and not failed and not unknown,
            'final_decision': item.get('final_decision'), 'checked_at': now.isoformat(),
            'price_basis': 'FUTURES', 'entry_reference_type': 'DEPTH_WEIGHTED_QUOTE_ESTIMATE_NOT_AN_EXECUTED_FILL'}
    report['report_c'] = [item for item in report['report_c'] if item['final_decision'] == 'APPROVED']
    report['approved_count'] = sum(item.get('final_decision') == 'APPROVED' for item in items)
    report['generated_at'] = now.isoformat()
    report['execution_safety'] = {'reconciliation': budget, 'entry_window': window,
        'contradictory_symbols': sorted(conflicts), 'broker_actions': 'READ_ONLY',
        'calculation_version': CALCULATION_VERSION, 'final_checked_at': now.isoformat(),
        'discovery_basis': 'DAILY_UNDERLYING_RESEARCH', 'execution_basis': 'COMPLETED_FUTURES_5_MINUTE_AND_FRESH_DEPTH'}
    return report
