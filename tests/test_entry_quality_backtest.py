from copy import deepcopy
from dataclasses import replace
import json
import pandas as pd
from test_entry_quality import sample,NOW
from src.futures.entry_quality import EntryQualityConfig
from src.futures.entry_quality_backtest import compare,audit_saved_reports
from src.futures.costs import FuturesCosts


def recorded(slot='09:30'):
    s=sample();at=NOW.replace(hour=int(slot[:2]),minute=int(slot[-2:]))
    for key in ('decision_at','available_at','stock_quote_at','confirmation_at','news_checked_at','news_gate_checked_at'):s[key]=at.isoformat()
    s['quote']['timestamp']=at.isoformat();s['execution_spread_bps']=1
    s['source_session']=[r for r in s['source_session'] if pd.Timestamp(r['timestamp'])+pd.Timedelta(minutes=5)<=at]
    return s,at


def history(at):
    index=pd.date_range(at,periods=4,freq='5min')
    frame=pd.DataFrame({'Open':[101,101.1,101.2,101.3],'High':[101.1,101.25,101.4,101.4],
        'Low':[100.95,101.05,101.15,101.25],'Close':[101.05,101.2,101.35,101.3],'Volume':[1000]*4,
        'tradingsymbol':['SYNTHETIC26OCTFUT']*4},index=index)
    return {'SYNTHETIC26OCTFUT':frame}


def test_paired_comparison_uses_costs_slippage_and_counts_missed_opportunity():
    s,at=recorded();s['trigger']=100
    r=compare([s],history(at),EntryQualityConfig(require_15m=False))
    row=r['comparisons'][0]
    assert row['status']=='EVALUATED',row
    assert row['baseline']['outcome']=='TARGET_FIRST'
    assert row['baseline']['net_pnl']<row['baseline']['gross_pnl']
    assert row['baseline']['total_costs']>0
    assert row['baseline']['mae_percent']>0
    assert row['framework'] is None and row['missed_target_opportunity']
    assert r['summary']['09:30']['missed_target_opportunities']==1
    assert r['summary']['09:45']['coverage']=={'UNAVAILABLE':1}


def test_earlier_slot_never_uses_later_decision_or_news():
    s,at=recorded('10:00');r=compare([s],history(at),EntryQualityConfig(require_15m=False))
    assert [row['reason'] for row in r['comparisons'][:2]]==['POINT_IN_TIME_INPUT_NOT_RECORDED']*2


def test_later_price_path_changes_outcomes_but_never_entry_assessment():
    s,at=recorded();h=history(at);first=compare([s],h,EntryQualityConfig(require_15m=False))
    h['SYNTHETIC26OCTFUT'].iloc[0,h['SYNTHETIC26OCTFUT'].columns.get_loc('Low')]=100
    later=compare([s],h,EntryQualityConfig(require_15m=False))
    assert first['comparisons'][0]['assessment']==later['comparisons'][0]['assessment']
    assert later['comparisons'][0]['baseline']['outcome']=='STOP_FIRST'


def test_default_15m_policy_misses_early_entries_explicitly():
    for slot in ('09:30','09:45','10:00'):
        s,at=recorded(slot);r=compare([s],history(at))
        row=next(r for r in r['comparisons'] if r['slot_ist']==slot)
        assert row['assessment']['classification']!='ENTER'
        assert 'COMPLETED_15M_RETEST_NOT_YET_POSSIBLE' in row['assessment']['reason_codes']


def test_no_history_or_wrong_expiry_is_not_a_fabricated_loss():
    s,at=recorded();r=compare([s],{})
    assert r['status']=='INSUFFICIENT_POINT_IN_TIME_EVIDENCE'
    h=history(at);h['SYNTHETIC26OCTFUT']['tradingsymbol']='WRONG_EXPIRY'
    assert compare([s],h)['comparisons'][0]['reason']=='OUTCOME_CONTRACT_IDENTITY_UNVERIFIED'


def test_archive_is_read_only_and_unrecorded_input_is_explicit(tmp_path):
    path=tmp_path/'old.json';path.write_text(json.dumps({'generated_at':NOW.isoformat(),'reviewed':[{'symbol':'ORIGINAL','technical_score':88}]}))
    original=path.read_bytes();r=audit_saved_reports([path])
    assert path.read_bytes()==original
    assert r['recorded_input_snapshots']==0 and r['historical_sources'][0]['file_unchanged']
    assert r['status']=='INSUFFICIENT_POINT_IN_TIME_EVIDENCE'


def test_backtest_does_not_mutate_candles_or_snapshot():
    s,at=recorded();h=history(at);before=deepcopy(s);raw=h['SYNTHETIC26OCTFUT'].copy()
    compare([s],h,EntryQualityConfig(require_15m=False))
    assert s==before
    pd.testing.assert_frame_equal(h['SYNTHETIC26OCTFUT'],raw)


def test_portfolio_comparison_never_exceeds_two_entries_per_day():
    samples=[];histories={}
    for index in range(4):
        s,at=recorded();s['symbol']='STOCK'+str(index);s['original_ranking_score']=90-index
        contract='STOCK'+str(index)+'26OCTFUT';s['contract']['tradingsymbol']=contract
        samples.append(s)
        f=next(iter(history(at).values())).copy();f['tradingsymbol']=contract;histories[contract]=f
    report=compare(samples,histories,EntryQualityConfig(require_15m=False))
    assert report['portfolio_by_slot']['09:30']['baseline']['metrics']['historical_signals']==2
    assert report['summary']['09:30']['baseline']['historical_signals']==4  # Individual opportunity study remains explicit.


def test_invalid_outcome_history_cannot_produce_a_win():
    s,at=recorded();h=history(at)
    h['SYNTHETIC26OCTFUT'].iloc[0,h['SYNTHETIC26OCTFUT'].columns.get_loc('Low')]=-100
    report=compare([s],h,EntryQualityConfig(require_15m=False))
    assert report['comparisons'][0]['reason']=='OUTCOME_HISTORY_INVALID'
    assert report['comparisons'][0]['baseline'] is None


def test_offline_tool_refuses_to_overwrite_an_input_report(tmp_path,monkeypatch):
    import pytest
    from scripts.audit_entry_quality import main
    source=tmp_path/'historical.json';source.write_text('{"reviewed":[]}')
    before=source.read_bytes()
    monkeypatch.setattr('sys.argv',['audit_entry_quality.py','--reports',str(source),'--output',str(source)])
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2
    assert source.read_bytes()==before
