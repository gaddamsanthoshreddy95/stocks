"""Approved corrections across final eligibility, persisted inputs and A–E surfaces."""
import ast
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from futures_mode_fixture import fixture, seed, NOW
from test_report_e import approved
from src.futures.config import FuturesScanConfig
from src.futures.execution_safety import finalize, REQUIRED_SCAN_GATES
from src.futures.sessions import normalise_candles, CALCULATION_VERSION
from src.futures.report_e import entry_analysis, append_report_e
from src.futures.rejected_analysis import append_report_d
from src.futures.preparation import PreparedScanner
from src.futures.cache import fingerprint
from src.futures.runtime import ScanRuntime
from src.presenter.futures_opportunities import FuturesOpportunitiesPresenter


def budget(now=NOW, count=0, status='PASS'):
    return {'status':status, 'checked_at':now.isoformat(), 'executed_entries':count,
            'remaining_entries':max(0,2-count), 'reasons':['RECONCILED_TEST_BOOK']}


def safety_fixture(side='LONG', now=NOW):
    item, report, context = approved(side)
    _, provider = fixture(['TEST'])
    bars = normalise_candles(provider.intraday, '5minute', now)
    item['futures_quote']['timestamp'] = now.isoformat()
    return item, report, context, {'TEST':bars}


@pytest.mark.parametrize('count,status', [(0,'PASS'),(1,'PASS'),(2,'FAIL'),(3,'FAIL'),(0,'UNKNOWN')])
def test_executed_entries_block_approvals_without_consuming_displayed_candidates(count,status):
    item,report,context,frames=safety_fixture()
    original=deepcopy(item)
    ledger=budget(count=count,status=status)
    finalize(report,frames,ledger,NOW,FuturesScanConfig())
    assert ledger==budget(count=count,status=status)
    assert item['technical_score']==original['technical_score']
    assert item['company_research']==original['company_research'] and item['plan']==original['plan']
    assert bool(report['report_c'])==(status=='PASS')
    assert (entry_analysis(item,report,context)['entry_status']=='READY')==(status=='PASS')
    assert report['execution_safety']['broker_actions']=='READ_ONLY'


@pytest.mark.parametrize('name', REQUIRED_SCAN_GATES)
@pytest.mark.parametrize('state',['UNKNOWN','FAIL'])
def test_every_final_scan_gate_binds_report_e(name,state):
    item,report,context,frames=safety_fixture()
    item['scan_gates'][name]['status']=state
    assert entry_analysis(item,report,context)['entry_status']!='READY'


def test_quote_can_expire_during_account_reconciliation():
    item,report,context,frames=safety_fixture()
    later=NOW+pd.Timedelta(seconds=121)
    finalize(report,frames,budget(later),later,FuturesScanConfig())
    assert not report['report_c'] and item['final_decision']=='UNKNOWN'
    assert 'QUOTE_EXPIRED_OR_UNVERIFIED_AT_FINAL_REPORT' in item['reason_codes']
    assert item['scan_gates']['latest_completed_futures_candle']['status']=='PASS'


def test_crossing_five_minute_boundary_demands_the_newly_completed_bar():
    item,report,context,frames=safety_fixture()
    later=NOW+pd.Timedelta(minutes=5)
    item['futures_quote']['timestamp']=later.isoformat()
    finalize(report,frames,budget(later),later,FuturesScanConfig())
    assert item['scan_gates']['final_futures_quote_freshness']['status']=='PASS'
    assert item['scan_gates']['latest_completed_futures_candle']['status']=='UNKNOWN'
    assert not report['report_c'] and item['final_decision']=='UNKNOWN'
    assert entry_analysis(item,report,context)['entry_status']!='READY'


@pytest.mark.parametrize('minute',[15,19,20,30])
def test_no_new_entries_at_cutoff_and_manual_warning_is_visible(minute):
    now=NOW.replace(hour=15,minute=minute)
    item,report,context,frames=safety_fixture(now=now)
    finalize(report,frames,budget(now),now,FuturesScanConfig())
    assert not report['report_c'] and not item['futures_execution_confirmation']['entry_ready']
    assert report['execution_safety']['entry_window']['manual_exit_warning_active']
    result=entry_analysis(item,report,context)
    assert result['entry_status']!='READY' and result['entry'] is None
    assert '15:20 IST' in report['execution_safety']['entry_window']['manual_exit_warning']


def test_reconciliation_must_still_be_fresh_at_final_report():
    item,report,context,frames=safety_fixture()
    finalize(report,frames,budget(NOW-pd.Timedelta(minutes=3)),NOW,FuturesScanConfig())
    assert not report['report_c']
    assert item['scan_gates']['executed_entry_budget']['status']=='UNKNOWN'


def test_daily_discovery_can_conflict_but_two_confirmed_futures_directions_cannot_be_ready():
    item,report,context,frames=safety_fixture()
    short,_,short_context=approved('SHORT')
    report['reviewed'].append(short);report['report_c'].append(short)
    report['report_b']=[short]
    finalize(report,frames,budget(),NOW,FuturesScanConfig())
    assert not report['report_c']
    assert report['execution_safety']['contradictory_symbols']==['TEST']
    assert all(i['futures_execution_confirmation']['timing']!='READY' for i in report['reviewed'])
    assert all(i['scan_gates']['directional_consistency']['status']=='FAIL' for i in report['reviewed'])
    assert entry_analysis(item,report,context)['entry_status']!='READY'
    assert entry_analysis(short,report,short_context)['entry_status']!='READY'


@pytest.mark.parametrize('changes',[{'minimum_net_rr':.9},{'target_fraction':.004},{'stop_fraction':.003}])
def test_unapproved_execution_policy_never_approves(changes):
    item,report,_,frames=safety_fixture()
    finalize(report,frames,budget(),NOW,replace(FuturesScanConfig(),**changes))
    assert not report['report_c'] and item['scan_gates']['fixed_strategy_policy']['status']=='FAIL'


def test_legacy_underlying_runtime_is_rejected_explicitly():
    with pytest.raises(ValueError,match='must be FUTURES'):
        ScanRuntime(movement_basis='UNDERLYING')


def test_complete_scanner_reads_account_before_and_after_and_never_mutates_broker():
    scanner,provider=fixture(['SBIN'])
    with patch('src.futures.gateway.sleep'):
        report=scanner.scan(now=NOW,include_backtest=False)
    kite=provider.provider.kite
    for name in ('profile','orders','trades','positions'):
        assert getattr(kite,name).call_count==4  # two coherent reads per reconciliation
    for name in ('place_order','modify_order','cancel_order','convert_position','exit_order'):
        getattr(kite,name).assert_not_called()
    assert report['execution_safety']['initial_reconciliation']['status']=='PASS'
    assert report['execution_safety']['reconciliation']['executed_entries']==0
    before=deepcopy({k:v for k,v in report.items() if k not in ('report_d','report_e')})
    calls={name:getattr(kite,name).call_count for name in ('quote','historical_data','profile','orders','trades','positions')}
    append_report_d(report);append_report_e(report,contexts=scanner._report_e_inputs)
    assert before=={k:v for k,v in report.items() if k not in ('report_d','report_e')}
    assert calls=={name:getattr(kite,name).call_count for name in calls}
    assert list(report)[-2:]==['report_d','report_e']
    rendered=FuturesOpportunitiesPresenter.render(report)
    titles=['Report A','Report B','Report C','ADDITIONAL ANALYSIS','REPORT E']
    assert [rendered.index(t) for t in titles]==sorted(rendered.index(t) for t in titles)
    assert 'DAILY DISCOVERY' in rendered and 'FUTURES EXECUTION' in rendered


def test_entry_limit_reconciled_again_after_a_manual_execution_during_scan():
    scanner,_=fixture(['SBIN'])
    with patch.object(scanner,'_read_execution_budget',side_effect=[budget(count=1),budget(count=2,status='FAIL')]) as reconcile:
        report=scanner.scan(now=NOW,include_backtest=False)
    assert reconcile.call_count==2 and not report['report_c']
    assert report['execution_safety']['initial_reconciliation']['executed_entries']==1
    assert all(i['scan_gates']['executed_entry_budget']['status']=='FAIL' for i in report['reviewed'])


def test_normalized_cached_candles_do_not_destroy_raw_evidence(tmp_path):
    scanner,provider=fixture(['SBIN']);runtime,cache=seed(scanner,tmp_path)
    key='NFO:SBIN26OCTFUT:5minute'
    bars=provider.get_session_intraday('SBIN')
    dirty=bars.copy();dirty.loc[NOW.normalize()+pd.Timedelta(hours=15,minutes=35)]=dirty.iloc[-1]
    cache.put_frame(key,dirty,now=NOW,ttl=86400)
    manager=PreparedScanner(scanner,runtime);manager.now=NOW;manager.clock=lambda:NOW;manager.mode='LIVE_SCAN'
    result=manager.update_candles(key,live=True)
    pd.testing.assert_frame_equal(result,bars,check_freq=False,check_dtype=False)
    raw=cache.frame(key,now=NOW)
    assert len(raw)==len(dirty) and (raw.index.hour==15).any()


def test_corrected_policy_invalidates_old_cached_backtests(tmp_path):
    scanner,provider=fixture(['SBIN']);runtime,cache=seed(scanner,tmp_path)
    manager=PreparedScanner(scanner,runtime);manager.now=NOW
    contract=provider.master[0]
    old=fingerprint({'config':asdict(scanner.config),'costs':asdict(scanner.costs),'movement_basis':'FUTURES','algorithm_version':2})
    cache.put('backtest',contract['tradingsymbol'],{'policy_fingerprint':old,'movement_basis':'FUTURES','groups':{}},now=NOW,ttl=86400)
    assert manager.cached_backtest(contract)['status']=='UNKNOWN'
    assert old!=manager.backtest_policy()


def test_preserved_baseline_keeps_daily_scores_and_research_thresholds():
    before=json.loads(Path('tests/fixtures/futures_correction_before.json').read_text())
    scanner,_=fixture()
    with patch('src.futures.gateway.sleep'):
        after=scanner.scan(now=NOW,include_backtest=False)
    old={(r['symbol'],r['side']):r for r in before['reviewed']}
    assert len(old)==len(after['reviewed'])
    for row in after['reviewed']:
        previous=old[(row['symbol'],row['side'])]
        for key in ('BULLISH_SCORE','BEARISH_SCORE','technical_score','final_decision'):
            assert row[key]==previous[key]
        assert row['company_research']['thresholds']==previous['company_research']['thresholds']
        assert row['daily_discovery']['execution_confirmation'] is False
        assert row['legacy_field_provenance']['confirmed']=='DAILY_DISCOVERY'
    assert before['approved_count']==after['approved_count']==0


def test_no_broker_mutation_call_sites_in_futures_scanner_modules():
    forbidden={'place_order','modify_order','cancel_order','convert_position','exit_order','place_gtt','modify_gtt','delete_gtt'}
    for path in Path('src/futures').glob('*.py'):
        tree=ast.parse(path.read_text())
        assert not [(path,n.func.attr) for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in forbidden]


def test_later_direction_candles_cannot_refresh_an_earlier_direction_signal():
    item,report,_,frames=safety_fixture()
    later=NOW+pd.Timedelta(minutes=5)
    _,provider=fixture(['TEST'])
    old=frames['TEST']
    fresh=normalise_candles(provider.intraday,'5minute',later)
    item['futures_quote']['timestamp']=later.isoformat()
    frames={'TEST:LONG':old, 'TEST:SHORT':fresh}
    finalize(report,frames,budget(later),later,FuturesScanConfig())
    assert not report['report_c']
    assert item['scan_gates']['latest_completed_futures_candle']['status']=='UNKNOWN'
    assert item['futures_execution_confirmation']['timing']!='READY'
