import ast
import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch
from src.futures.rejected_analysis import build_report_d, append_report_d, render_report_d, save_report_d, TITLE
from src.futures.scanner import REQUIRED_EXECUTION
from src.futures.execution_safety import REQUIRED_SCAN_GATES
from src.quality.futures_selection import REQUIRED_CHECKS
from src.presenter.futures_opportunities import FuturesOpportunitiesPresenter


def candidate(symbol='AAA', side='LONG'):
    return {'symbol': symbol, 'side': side, 'final_decision': 'REJECT', 'execution_reviewed': True,
            'technical_score': 80, 'BULLISH_SCORE': 80, 'BEARISH_SCORE': 70, 'setup_type': 'BREAKOUT', 'sector': 'Test',
            'reason_codes': ['INSUFFICIENT_FUTURES_TARGET_SPACE'],
            'failed_execution_checks': ['INSUFFICIENT_FUTURES_TARGET_SPACE'], 'missing_execution_checks': [],
            'scan_gates': {k: {'status':'PASS'} for k in REQUIRED_SCAN_GATES},
            'execution_checks': {k: {'status': 'PASS', 'factors': {}, 'reason_codes': []} for k in (*REQUIRED_EXECUTION, 'listed_futures_contract')},
            'company_research': {'status': 'PASS', 'checks': {k: {'status': 'PASS', 'score': 80} for k in REQUIRED_CHECKS}, 'fundamental_assessment': {'score': 80}},
            'news_alignment': {'approved': True}, 'futures_setup': {'confirmed': True, 'timing': 'READY', 'evidence': {'support': 99, 'resistance': 100.2, 'daily_atr': 1}},
            'short_entry': {'state': 'TRIGGER_CROSSED', 'setup_invalidation_price': 100.5},
            'futures_target_space': {'available_points': .2, 'required_points': .3, 'level': 100.2},
            'plan': {'entry': 100, 'target': 100.3 if side == 'LONG' else 99.7, 'stop_loss': 99.8 if side == 'LONG' else 100.2,
                     'number_of_lots': 1, 'gross_profit_at_target': 300, 'gross_loss_at_stop': 200, 'net_profit_at_target': 250, 'net_risk_reward': 1.1}}


def report():
    a, b = candidate(), candidate('BBB', 'SHORT')
    return {'generated_at': '2026-10-08T09:40:00+05:30', 'universe_size': 4, 'long_discovery_count': 2, 'short_discovery_count': 2,
            'approved_count': 0, 'execution_policy': 'Scanner only; no orders.', 'historical_validation': 'Historical evidence only.',
            'config': {'minimum_net_rr': 1, 'minimum_score': 60, 'target_fraction': .003, 'stop_fraction': .002},
            'report_a': [a], 'report_b': [b], 'report_c': [], 'reviewed': [a, b]}


def test_all_retained_candidates_classification_and_no_mutation():
    r = report(); other = candidate('OTHER'); other['failed_execution_checks'].append('SPREAD_FAILED')
    unknown = candidate('UNKNOWN'); unknown['missing_execution_checks'] = ['MARGIN_UNVERIFIED']; unknown['failed_execution_checks'].append('OTHER_FAILURE')
    r['reviewed'] += [other, unknown]; before = copy.deepcopy(r)
    d = build_report_d(r)
    assert r == before
    assert d['summary'] == {'TARGET_SL_ONLY': 2, 'TARGET_SL_PLUS_OTHER_FAILURES': 1, 'TARGET_SL_PLUS_UNKNOWN': 1}
    assert {i['symbol'] for i in d['candidates']} == {'AAA', 'BBB', 'OTHER', 'UNKNOWN'}
    assert all(i['status'] == 'REJECTED / RESEARCH ONLY' for i in d['candidates'])
    assert all(i['technical_score'] == 80 for i in d['candidates'])
    assert d['candidates'][0]['gross_reward_risk'] == 1.5


def test_ambiguous_risk_budget_is_not_target_failure():
    r = report(); r['reviewed'] = [candidate()]; item = r['reviewed'][0]
    item['reason_codes'] = item['failed_execution_checks'] = ['NET_ECONOMICS_OR_RISK_BUDGET_FAILED']
    item['plan']['number_of_lots'] = 0
    assert not build_report_d(r)['candidates']
    item['plan']['net_risk_reward'] = .5
    row = build_report_d(r)['candidates'][0]
    assert row['classification'] == 'TARGET_SL_PLUS_OTHER_FAILURES'
    item.pop('plan')
    assert build_report_d(r)['unresolved_rejections']


def test_missing_evidence_never_claims_only_and_does_not_invent_stop():
    r = report(); r['reviewed'][0].pop('execution_checks'); r['reviewed'][0].pop('short_entry')
    row = build_report_d(r)['candidates'][0]
    assert row['classification'] == 'TARGET_SL_PLUS_UNKNOWN'
    assert row['logical_structure_stop_reference'] is None
    assert 'logical_structure_stop_reference' in row['missing_information']
    r.pop('reviewed')
    assert build_report_d(r)['missing_information']


def test_existing_abc_rendering_unchanged_and_d_last():
    r = report(); before = copy.deepcopy(r)
    append_report_d(r)
    for key in ('report_a', 'report_b', 'report_c'):
        assert r[key] == before[key]
    rendered = FuturesOpportunitiesPresenter.render(r)
    prefix, additional = rendered.split('\n\n## ' + TITLE)
    assert prefix == Path('tests/fixtures/futures_abc_before_report_d.md').read_text()
    assert rendered.index('Report A') < rendered.index('Report B') < rendered.index('Report C') < rendered.index(TITLE)
    assert rendered.count(TITLE) == 1
    assert 'REJECTED / RESEARCH ONLY' in additional
    assert list(r)[-1] == 'report_d'


def test_completed_scan_analysis_has_zero_additional_requests():
    from tests.futures_mode_fixture import fixture, NOW
    scanner, provider = fixture()
    completed = scanner.scan(now=NOW, include_backtest=False)
    before = copy.deepcopy({k: v for k, v in completed.items() if k != 'report_d'})
    kite = provider.provider.kite
    counts = [getattr(kite, name).call_count for name in ('quote', 'historical_data', 'order_margins', 'margins')]
    with patch.object(scanner, '_scan', side_effect=AssertionError('Duplicate scan')):
        append_report_d(completed)
        render_report_d(completed['report_d'])
    assert before == {k: v for k, v in completed.items() if k != 'report_d'}
    assert counts == [getattr(kite, name).call_count for name in ('quote', 'historical_data', 'order_margins', 'margins')]


def test_save_report_d_and_storage_failure(tmp_path):
    r = append_report_d(report()); save_report_d(r, tmp_path)
    assert json.loads(Path(r['report_d']['saved_files']['json']).read_text()) == r['report_d']
    assert TITLE in Path(r['report_d']['saved_files']['md']).read_text()
    blocker = tmp_path / 'file'; blocker.write_text('x')
    save_report_d(r, blocker)
    assert 'storage_error' in r['report_d']
    assert r['report_a'][0]['final_decision'] == 'REJECT'


def test_streamlit_report_d_last_visible_output():
    tree = ast.parse(Path('ui_app.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'futures_opportunities_page')
    context = Mock(); context.__enter__ = Mock(return_value=context); context.__exit__ = Mock(return_value=False)
    st = Mock(); st.session_state = {'futures_scan_report': append_report_d(report())}
    st.number_input.return_value = 5; st.selectbox.return_value = 'LIVE_SCAN'; st.button.return_value = False
    st.tabs.return_value = [context] * 3; st.expander.return_value = context
    jobs = Mock(); jobs.future.return_value = None
    env = {'st': st, 'TradingPlatform': Mock, 'daily_report_jobs': lambda: jobs, 'feature_is_running': lambda _: False,
           'data_age': lambda _: 'fresh', 'FuturesOpportunitiesPresenter': FuturesOpportunitiesPresenter, 'json': json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'ui_app.py', 'exec'), env)
    env['futures_opportunities_page'](Mock())
    assert st.method_calls[-1][0] == 'markdown'
    assert st.markdown.call_args_list[-2].args[0].startswith('## ' + TITLE)
    assert st.method_calls[-1].args[0].startswith('## REPORT E')
    assert 'AAA' in st.markdown.call_args_list[0].args[0]


def test_scanner_before_after_identical_abc_and_provider_calls():
    from tests.futures_mode_fixture import fixture, NOW
    scanner, provider = fixture()
    with patch('src.futures.rejected_analysis.append_report_d', side_effect=lambda r: r):
        original = scanner.scan(now=NOW, include_backtest=False)
    enhanced_scanner, enhanced_provider = fixture()
    enhanced = enhanced_scanner.scan(now=NOW, include_backtest=False)
    for key in ('report_a', 'report_b', 'report_c', 'reviewed', 'approved_count', 'config'):
        assert original[key] == enhanced[key]
    for name in ('quote', 'historical_data', 'order_margins', 'margins'):
        assert getattr(provider.provider.kite, name).call_args_list == getattr(enhanced_provider.provider.kite, name).call_args_list
    assert list(enhanced)[-2:] == ['report_d','report_e']


def test_cli_and_api_automatic_report_d(capsys):
    import main
    from src import api
    r = append_report_d(report())
    fake = Mock(); fake.scan_futures_opportunities.return_value = r
    with patch.object(main, 'TradingPlatform', return_value=fake), patch('sys.argv', ['main.py', 'futures-scan']):
        main.main()
    output = capsys.readouterr().out
    assert output.index('Report C') < output.index(TITLE)
    assert output.count(TITLE) == 1
    with patch.object(api, 'platform', fake):
        response = api.futures_opportunities()
    assert response == r
    assert list(response)[-1] == 'report_d'


def test_platform_automatically_persists_d_without_altering_abc(tmp_path, monkeypatch):
    from src.application.platform import TradingPlatform
    r = append_report_d(report()); before = copy.deepcopy(r)
    fake = Mock(); fake.scan.return_value = r
    monkeypatch.setenv('FUTURES_REPORT_DIRECTORY', str(tmp_path))
    platform = object.__new__(TradingPlatform)
    with patch('src.futures.scanner.FuturesOpportunityScanner', return_value=fake):
        result = platform.scan_futures_opportunities()
    for key in ('report_a', 'report_b', 'report_c'):
        assert result[key] == before[key]
    assert Path(result['report_d']['saved_files']['json']).exists()
    fake.scan.assert_called_once_with(5, include_backtest=True)


def test_scheduled_command_appends_d_after_existing_output(tmp_path, capsys):
    tree = ast.parse(Path('scripts/run_futures_mode.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ROOT' for t in node.targets):
            node.value = ast.Call(func=ast.Name(id='Path', ctx=ast.Load()), args=[ast.Constant(str(tmp_path))], keywords=[])
    ast.fix_missing_locations(tree)
    r = append_report_d(report()); r['timings'] = {'total_seconds': 1}
    fake = Mock(); fake.scan_futures_opportunities.return_value = r
    with patch('src.application.platform.TradingPlatform', return_value=fake), patch('sys.argv', ['run_futures_mode.py', '--mode', 'LIVE_SCAN']):
        exec(compile(tree, 'scripts/run_futures_mode.py', 'exec'), {'__file__': str(Path('scripts/run_futures_mode.py').resolve())})
    output = capsys.readouterr().out
    assert json.loads(output.splitlines()[0])['mode'] == 'LIVE_SCAN'
    assert output.index('Report A') < output.index('Report B') < output.index('Report C') < output.index(TITLE) < output.index('REPORT E')
    assert output.count(TITLE) == 1
    assert len(list((tmp_path / 'reports' / 'prepared_scans').glob('*.md'))) == 1


def test_optional_failure_does_not_change_mandatory_classification():
    r = report()
    r['reviewed'][0]['execution_checks']['optional_supertrend'] = {'status': 'FAIL'}
    for code in ('INSUFFICIENT_FUTURES_TARGET_SPACE', 'INSUFFICIENT_UNDERLYING_TARGET_SPACE', 'SHORT_STOP_DOES_NOT_COVER_SETUP_INVALIDATION'):
        r['reviewed'][0]['reason_codes'] = [code]
        r['reviewed'][0]['failed_execution_checks'] = [code]
        d = build_report_d(r)
        assert d['candidates'][0]['classification'] == 'TARGET_SL_ONLY'
        assert d['candidates'][0]['fixed_target_stop_reasons'] == [code]
