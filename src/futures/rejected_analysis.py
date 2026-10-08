"""Read-only Report D: no providers, recalculation of setups, or approval changes."""
import json
import os
from pathlib import Path
from math import isfinite
from src.quality.futures_selection import REQUIRED_CHECKS

TITLE = 'ADDITIONAL ANALYSIS — STOCKS REJECTED DUE TO FIXED TARGET / STOP-LOSS'
FIXED = {
    'INSUFFICIENT_REMAINING_TARGET_SPACE', 'INSUFFICIENT_FUTURES_TARGET_SPACE',
    'INSUFFICIENT_UNDERLYING_TARGET_SPACE', 'SHORT_STOP_DOES_NOT_COVER_SETUP_INVALIDATION',
    'SHORT_TARGET_OR_STOP_DOES_NOT_MATCH_REQUIRED_MOVEMENT',
}
ECONOMICS = 'NET_ECONOMICS_OR_RISK_BUDGET_FAILED'
CLASSES = ('TARGET_SL_ONLY', 'TARGET_SL_PLUS_OTHER_FAILURES', 'TARGET_SL_PLUS_UNKNOWN')


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def build_report_d(report):
    # Imported locally to reuse the existing mandatory gate definitions without a cycle.
    from src.futures.scanner import REQUIRED_EXECUTION
    from src.futures.execution_safety import REQUIRED_SCAN_GATES
    rows, unresolved, missing = [], [], []
    if not isinstance(report.get('reviewed'), list):
        missing.append('Complete reviewed candidate/rejection records are unavailable; displayed top lists are insufficient.')
    for item in report.get('reviewed') or []:
        if item.get('final_decision') == 'APPROVED':
            continue
        reasons = list(dict.fromkeys(item.get('reason_codes', []) + item.get('failed_execution_checks', [])))
        fixed = [r for r in reasons if r in FIXED]
        plan = item.get('plan') or {}
        net, minimum = plan.get('net_risk_reward'), report.get('config', {}).get('minimum_net_rr')
        if ECONOMICS in reasons:
            attributable = ((number(net) and number(minimum) and net < minimum)
                            or (number(plan.get('net_profit_at_target')) and plan['net_profit_at_target'] <= 0))
            if attributable:
                fixed.append(ECONOMICS)
            elif not (number(net) and number(minimum) and number(plan.get('net_profit_at_target'))):
                unresolved.append({'symbol': item.get('symbol'), 'side': item.get('side'),
                                   'reason': 'Combined economics/risk rejection cannot be attributed to Target/SL from retained values.'})
        checks = item.get('execution_checks') or {}
        if checks.get('futures_target_space_quality', {}).get('status') == 'FAIL' and not fixed:
            fixed.append('futures_target_space_quality:FAIL')
        if not fixed:
            continue
        remaining = {'PASS': [], 'FAIL': [], 'UNKNOWN': []}
        def gate(name, status):
            remaining[status if status in remaining else 'UNKNOWN'].append(name)
        research = item.get('company_research') or {}
        for name in REQUIRED_CHECKS:
            gate('research:' + name, research.get('checks', {}).get(name, {}).get('status'))
        gate('company_research', research.get('status'))
        for name in REQUIRED_EXECUTION:
            gate(name, checks.get(name, {}).get('status'))
        for name in REQUIRED_SCAN_GATES:
            gate(name, item.get('scan_gates',{}).get(name,{}).get('status'))
        for code in item.get('failed_execution_checks', []):
            if code not in FIXED and code != ECONOMICS:
                gate(code, 'FAIL')
        for code in item.get('missing_execution_checks', []):
            gate(code, 'UNKNOWN')
        gate('retained_mandatory_rejection_records', 'PASS' if isinstance(item.get('failed_execution_checks'), list) and isinstance(item.get('missing_execution_checks'), list) else 'UNKNOWN')
        gate('execution_review_completed', 'PASS' if item.get('execution_reviewed') else 'UNKNOWN')
        gate('listed_contract', checks.get('listed_futures_contract', {}).get('status'))
        news = item.get('news_alignment') or {}
        gate('news_alignment', 'UNKNOWN' if news.get('status') == 'UNVERIFIED' else 'PASS' if news.get('approved') is True else 'FAIL' if news.get('approved') is False else 'UNKNOWN')
        setup = item.get('futures_setup') or {}
        gate('setup_confirmation', 'PASS' if setup.get('confirmed') is True else 'FAIL' if setup.get('confirmed') is False else 'UNKNOWN')
        timing = setup.get('timing')
        target_only_wait = timing == 'WAIT' and setup.get('confirmed') is True and bool(setup.get('reason_codes')) and all(r in FIXED for r in setup['reason_codes'])
        gate('setup_timing', 'PASS' if timing == 'READY' or target_only_wait else 'FAIL' if timing in ('TOO LATE', 'WAIT', 'WAIT FOR PULLBACK') else 'UNKNOWN')
        score, threshold = item.get('technical_score'), report.get('config', {}).get('minimum_score')
        gate('technical_threshold', 'PASS' if number(score) and number(threshold) and score >= threshold else 'FAIL' if number(score) and number(threshold) else 'UNKNOWN')
        if research.get('policy_review_required'):
            gate('research_policy_review', 'FAIL')
        if number(plan.get('number_of_lots')) and plan['number_of_lots'] < 1:
            gate('position_sizing_risk_budget', 'FAIL')
        if item.get('side') == 'SHORT':
            state = (item.get('short_entry') or {}).get('state')
            gate('short_trigger', 'PASS' if state == 'TRIGGER_CROSSED' else 'FAIL' if state in ('WAIT_FOR_SHORT_TRIGGER', 'TOO_LATE') else 'UNKNOWN')
        category = CLASSES[2] if remaining['UNKNOWN'] else CLASSES[1] if remaining['FAIL'] else CLASSES[0]
        evidence = setup.get('evidence') or {}
        structural = (item.get('short_entry') or {}).get('setup_invalidation_price', evidence.get('setup_invalidation_price'))
        loss, profit = plan.get('gross_loss_at_stop'), plan.get('gross_profit_at_target')
        row = {
            'symbol': item.get('symbol'), 'side': item.get('side'), 'status': 'REJECTED / RESEARCH ONLY',
            'original_decision': item.get('final_decision'), 'classification': category,
            'technical_score': score, 'research_scores': {k: v.get('score') for k, v in research.get('checks', {}).items()},
            'fundamental_assessment': research.get('fundamental_assessment'),
            'entry': plan.get('entry'), 'target': plan.get('target'), 'stop_loss': plan.get('stop_loss'),
            'movement_basis': plan.get('movement_basis'),
            'underlying_entry': plan.get('underlying_entry'), 'underlying_target': plan.get('underlying_target'), 'underlying_stop_loss': plan.get('underlying_stop_loss'),
            'target_clearance': item.get('futures_target_space'), 'underlying_target_clearance': item.get('underlying_target_space'),
            'atr': evidence.get('daily_atr'), 'atr_basis': 'Retained futures setup daily ATR',
            'atr_measurements': checks.get('futures_atr_quality', {}).get('factors'),
            'support': evidence.get('support'), 'resistance': evidence.get('resistance'),
            'logical_structure_stop_reference': structural,
            'structure_boundary': evidence.get('support' if item.get('side') == 'LONG' else 'resistance'),
            'structure_stop_note': 'Retained invalidation reference; alternative execution stop is unvalidated.' if structural is not None else 'Exact structure-based stop unavailable; no buffer or alternative stop invented.',
            'gross_reward_risk': profit / loss if number(profit) and number(loss) and loss > 0 else None,
            'net_reward_risk': net, 'target_costs': plan.get('target_costs'), 'stop_costs': plan.get('stop_costs'),
            'fixed_target_stop_reasons': fixed, 'exact_rejection_reasons': reasons,
            'remaining_checks': {k: list(dict.fromkeys(v)) for k, v in remaining.items()},
            'daily_discovery': item.get('daily_discovery'), 'futures_execution_confirmation': item.get('futures_execution_confirmation'),
            'retained_check_details': {'scan_gates': item.get('scan_gates',{}), 'execution': checks, 'research': research.get('checks', {})},
            'strong_quality_evidence': number(score) and number(threshold) and score >= threshold and research.get('status') == 'PASS' and all(research.get('checks', {}).get(k, {}).get('status') == 'PASS' for k in REQUIRED_CHECKS),
            'missing_information': [],
        }
        for field in ('entry', 'target', 'stop_loss', 'target_clearance', 'atr', 'support', 'resistance', 'logical_structure_stop_reference', 'gross_reward_risk', 'net_reward_risk'):
            if row[field] is None:
                row['missing_information'].append(field)
        rows.append(row)
    def ranking(row):
        research_score = (row.get('fundamental_assessment') or {}).get('score')
        return (row['strong_quality_evidence'], row['technical_score'] if number(row['technical_score']) else -float('inf'), research_score if number(research_score) else -float('inf'))
    rows.sort(key=ranking, reverse=True)
    return {'title': TITLE, 'status': 'REJECTED / RESEARCH ONLY', 'generated_at': report.get('generated_at'),
            'source': 'Completed scan retained records only; no additional market-data requests.',
            'fixed_configuration': {k: report.get('config', {}).get(k) for k in ('target_fraction', 'stop_fraction')},
            'summary': {k: sum(r['classification'] == k for r in rows) for k in CLASSES},
            'candidates': rows, 'unresolved_rejections': unresolved, 'missing_information': missing}


def append_report_d(report):
    report.pop('report_d', None)
    report['report_d'] = build_report_d(report)
    return report


def render_report_d(report_d):
    lines = ['## ' + TITLE, '', 'Report D — REJECTED / RESEARCH ONLY. Original trading decisions remain in force.', '',
             report_d['source'], '', 'Original fixed configuration: ' + json.dumps(report_d['fixed_configuration']),
             'Summary: ' + json.dumps(report_d['summary']), '']
    if report_d.get('storage_error'):
        lines.append('Report D could not be saved: ' + report_d['storage_error'])
    for message in report_d.get('missing_information', []):
        lines.append('Missing information: ' + message)
    for row in report_d['candidates']:
        lines.extend([f"### {row['symbol']} — {row['side']} — REJECTED / RESEARCH ONLY", '',
                      f"Classification: {row['classification']}; strong existing quality evidence: {row['strong_quality_evidence']}."])
        if row['strong_quality_evidence']:
            lines.append('**Strong existing technical and company research evidence; still REJECTED / RESEARCH ONLY.**')
        for key, value in row.items():
            if key not in ('symbol', 'side', 'status', 'classification', 'strong_quality_evidence'):
                lines.append(f"- {key}: {json.dumps(value, default=str) if value is not None else 'UNKNOWN'}")
        lines.append('')
    if not report_d['candidates']:
        lines.append('No attributable fixed Target/SL rejections in retained records.')
    for row in report_d['unresolved_rejections']:
        lines.append('Unresolved rejection attribution: ' + json.dumps(row))
    return '\n'.join(lines)


def save_report_d(report, directory=None):
    """Save only the additive artifact; a filesystem failure cannot break existing output."""
    from tempfile import NamedTemporaryFile
    if 'report_d' not in report:
        append_report_d(report)
    analysis = report['report_d']
    try:
        folder = Path(directory or os.getenv('FUTURES_REPORT_DIRECTORY', 'reports/prepared_scans'))
        folder.mkdir(parents=True, exist_ok=True)
        stamp = ''.join(c for c in str(report.get('generated_at', 'unknown')) if c.isalnum())
        files = {ext: str(folder / f'report_D_{stamp}.{ext}') for ext in ('json', 'md')}
        analysis['saved_files'] = files
        for ext, path in files.items():
            content = json.dumps(analysis, indent=2, default=str) if ext == 'json' else render_report_d(analysis)
            with NamedTemporaryFile('w', dir=folder, encoding='utf-8', delete=False) as tmp:
                tmp.write(content)
                temporary = tmp.name
            try:
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    except OSError as exc:
        analysis['storage_error'] = str(exc)
