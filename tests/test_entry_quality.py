"""Synthetic policy checks; none are a reconstruction of actual broker trades."""
from copy import deepcopy
from dataclasses import replace
import pandas as pd
import pytest
from src.futures.entry_quality import EntryQualityConfig,evaluate,refresh_report
from src.futures.retracement_15m import completed_bars,evaluate as fifteen
from src.futures.costs import trade_plan,FuturesCosts
from futures_mode_fixture import SyntheticNoCostModel
from src.futures.config import FuturesScanConfig

NOW=pd.Timestamp('2026-10-16 10:15',tz='Asia/Kolkata')


def bars(side='LONG'):
    values=[(100,100.2,99.8,100),(100,100.3,99.9,100.1),
        (100.1,101,100,100.8),(100.75,101.2,100.2,101)]
    frame=pd.DataFrame([row for row in values for _ in range(3)],
        index=pd.date_range(NOW.normalize()+pd.Timedelta(hours=9,minutes=15),periods=12,freq='5min'),
        columns=['Open','High','Low','Close']);frame['Volume']=1000
    if side=='SHORT':
        old=frame.copy();frame['Open']=200-old.Open;frame['Close']=200-old.Close
        frame['High']=200-old.Low;frame['Low']=200-old.High
    return frame


def sample(side='LONG'):
    sign=1 if side=='LONG' else -1;entry=101 if sign==1 else 99
    plan=trade_plan(entry,100,side,config=FuturesScanConfig(),costs=SyntheticNoCostModel(),risk_budget=1000,trade_date=NOW.date())
    return {'side':side,'symbol':'SYNTHETIC','decision_at':NOW.isoformat(),'available_at':NOW.isoformat(),
        'contract':{'instrument_token':42,'tradingsymbol':'SYNTHETIC26OCTFUT'},'plan':plan,
        'quote':{'instrument_token':42,'timestamp':NOW.isoformat(),'last_price':entry,'ohlc':{'close':99.9 if sign==1 else 100.1,'open':100}},
        'stock_quote_at':NOW.isoformat(),'stock_price':entry,'stock_vwap':entry-sign*.2,'vwap':entry-sign*.2,
        'trigger':entry-sign*.01,'invalidation':entry-sign*.1,'rvol':1.5,'slippage_bps':3,
        'metrics':{'opening_range_high':100.4,'opening_range_low':99.6,'intraday_true_range':.15,'session_range':1.5,'session_open':100},
        'stock_return':sign*1.1,'nifty_return':0,'sector_return':0,'support':98,'resistance':102,
        'confirmation_status':'PASS','confirmation_at':NOW.isoformat(),
        'news_status':'CLEAR','news_checked_at':NOW.isoformat(),'news_gate_checked_at':NOW.isoformat(),
        'original_decision':'APPROVED','original_ranking_score':80,'source_session':[{'timestamp':i.isoformat(),**row.to_dict()} for i,row in bars(side).iterrows()],'retracement_15m':fifteen(bars(side),bars(side),NOW,side)}


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_more_than_one_percent_is_not_an_automatic_rejection(side):
    s=sample(side);r=evaluate(s)
    assert r['classification']=='ENTER',r
    assert abs(r['measurements']['move_from_previous_close_percent'])>1
    assert r['entry_zone']['conditional']


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_chasing_waits_without_changing_score_or_widening_stop(side):
    s=sample(side);sign=1 if side=='LONG' else -1
    s['trigger']-=sign*.5;s['vwap']-=sign*.6
    original=deepcopy(s);r=evaluate(s)
    assert r['classification']=='WAIT FOR PULLBACK',r
    assert 'ENTRY_TOO_FAR_FROM_FRESH_TRIGGER' in r['reason_codes']
    assert r['stop_loss']==original['plan']['stop_loss']
    assert s==original


@pytest.mark.parametrize('change,reason',[
    ('quote','FUTURES_QUOTE_STALE_OR_UNVERIFIED'),('news','VERIFIED_NEWS_CHECK_UNAVAILABLE'),
    ('gate','NEWS_CONFLICT_RECHECK_REQUIRED'),('future','SNAPSHOT_STALE_OR_FROM_FUTURE'),
    ('identity','EXACT_CONTRACT_UNVERIFIED'),('cost','COSTED_EXECUTION_PLAN_UNAVAILABLE')])
def test_missing_stale_or_future_data_cannot_enter(change,reason):
    s=sample()
    if change=='quote':s['quote']['timestamp']=(NOW-pd.Timedelta(seconds=31)).isoformat()
    if change=='news':s['news_status']='UNAVAILABLE'
    if change=='gate':s['news_gate_checked_at']=(NOW-pd.Timedelta(seconds=31)).isoformat()
    if change=='future':s['available_at']=(NOW+pd.Timedelta(seconds=1)).isoformat()
    if change=='identity':s['quote']['instrument_token']=43
    if change=='cost':s['plan'].pop('cost_evidence')
    r=evaluate(s);assert r['classification']=='SKIP' and reason in r['reason_codes']


@pytest.mark.parametrize('change', ['slippage','costs','news_conflict','ledger','after_cutoff','research','structural_stop','target_room','volume','strength','opening','volatility'])
def test_risk_safeguards(change):
    s=sample();now=NOW
    if change=='slippage':s['slippage_bps']=11
    if change=='costs':s['plan']['net_risk_reward']=.8
    if change=='news_conflict':s['news_status']='CONFLICT'
    if change=='ledger':s['original_decision']='REJECT';s['failed_execution_checks']=['executed_entry_budget']
    if change=='after_cutoff':now=NOW.replace(hour=15,minute=0)
    if change=='research':s['research_only']=True
    if change=='structural_stop':s['invalidation']=100
    if change=='target_room':s['resistance']=101.1
    if change=='volume':s['rvol']=.5
    if change=='strength':s['sector_return']=2
    if change=='opening':s['metrics']['opening_range_high']=102
    if change=='volatility':s['metrics']['intraday_true_range']=1;s['trigger']=100.93
    assert evaluate(s,now)['classification']!='ENTER'


def test_wait_recheck_requires_fresh_price_and_news():
    s=sample();s['trigger']=100.5
    assert evaluate(s)['classification']=='WAIT FOR PULLBACK'
    later=NOW+pd.Timedelta(seconds=31)
    assert evaluate(s,later)['classification']=='SKIP'
    fresh=sample();fresh['trigger']=100.99
    for key in ('decision_at','available_at','stock_quote_at','confirmation_at','news_checked_at','news_gate_checked_at'):fresh[key]=later.isoformat()
    fresh['quote']['timestamp']=later.isoformat();fresh['retracement_15m']['checked_at']=later.isoformat()
    assert evaluate(fresh,later)['classification']=='ENTER'


def test_report_only_preserves_reports_decisions_and_ranks():
    item={'symbol':'SYNTHETIC','side':'LONG','technical_score':80,'final_decision':'APPROVED','entry_quality_input':sample(),'reason_codes':['OLD']}
    report={'reviewed':[item],'report_a':[item],'report_b':[],'report_c':[item],'approved_count':1}
    item['entry_quality_input']['trigger']=100
    before=deepcopy(report);refresh_report(report,NOW,EntryQualityConfig(mode="REPORT_ONLY"))
    assert report['approved_count']==1 and report['report_c'][0]['final_decision']=='APPROVED'
    assert item['reason_codes']==before['reviewed'][0]['reason_codes']
    assert item['technical_score']==80
    assert item['entry_quality']['classification']=='WAIT FOR PULLBACK'
    assert report['entry_quality_summary']['changed_decisions']==0


def test_opt_in_enforcement_only_withdraws_and_preserves_input():
    s=sample();s['trigger']=100
    item={'symbol':'SYNTHETIC','side':'LONG','final_decision':'APPROVED','entry_quality_input':s,'reason_codes':[]}
    original=deepcopy(s);report={'reviewed':[item],'report_c':[item],'approved_count':1}
    refresh_report(report,NOW,EntryQualityConfig(mode='ENFORCE'))
    assert item['final_decision']=='WAIT' and not report['report_c']
    assert {k:v for k,v in item['entry_quality_input'].items() if k!='existing_scan_gates'}==original
    item['final_decision']='REJECT';refresh_report(report,NOW,EntryQualityConfig(mode='ENFORCE'))
    assert item['final_decision']=='REJECT'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_completed_fifteen_minute_retest_symmetry(side):
    f=bars(side);r=fifteen(f,f,NOW,side)
    assert r['status']=='PASS',r
    assert len(completed_bars(f,NOW))==4
    assert r['instruments']['futures']['retest']['retest_at'].endswith('10:00:00+05:30')


def test_unfinished_future_or_missing_five_minute_bar_cannot_confirm():
    f=bars();early=NOW-pd.Timedelta(seconds=1)
    assert len(completed_bars(f,early))==3
    assert fifteen(f,f,early,'LONG')['status']=='WAIT'
    future=f.iloc[[-1]].copy();future.index=[NOW];future['Close']=-999
    assert fifteen(pd.concat([f,future]),f,NOW,'LONG')==fifteen(f,f,NOW,'LONG')
    assert fifteen(f.drop(f.index[1]),f,NOW,'LONG')['status']=='UNKNOWN'


def test_invalid_candle_or_duplicate_cannot_confirm():
    f=bars();f.iloc[0,f.columns.get_loc('Low')]=-1
    assert fifteen(f,f,NOW,'LONG')['status']=='UNKNOWN'
    f=bars();assert fifteen(pd.concat([f,f.iloc[[0]]]),f,NOW,'LONG')['status']=='UNKNOWN'


def test_15m_confirmation_waits_instead_of_using_previous_day_bars():
    f=bars();prior=f.copy();prior.index-=pd.Timedelta(days=1)
    assert fifteen(pd.concat([prior,f]),f,NOW.replace(hour=9,minute=45),'LONG')['status']=='WAIT'


@pytest.mark.parametrize('kwargs',[{'mode':'ACTIVE'},{'quote_age_seconds':31},{'level_bars_15m':1},{'retest_age_15m':.5}])
def test_invalid_configuration_rejected(kwargs):
    with pytest.raises(ValueError):EntryQualityConfig(**kwargs)


def test_actual_cost_model_is_not_relaxed_to_force_an_entry():
    s=sample()
    s['plan']=trade_plan(101,100,'LONG',config=FuturesScanConfig(),costs=FuturesCosts(),risk_budget=1000,trade_date=NOW.date())
    result=evaluate(s)
    assert result['classification']=='SKIP'
    assert 'NET_REWARD_OR_RISK_BUDGET_INSUFFICIENT' in result['reason_codes']


def test_missing_cost_components_cannot_be_treated_as_zero():
    s=sample();s['plan']['target_costs']={}
    assert 'EXECUTION_COST_COMPONENTS_UNAVAILABLE' in evaluate(s)['reason_codes']


def test_enforcement_replaces_with_next_eligible_without_reordering_scores():
    candidates=[]
    for symbol,score,late in [('FIRST',90,True),('NEXT',80,False)]:
        s=sample();s['symbol']=symbol
        if late:s['trigger']=100
        candidates.append({'symbol':symbol,'side':'LONG','technical_score':score,'final_decision':'APPROVED',
            'entry_quality_input':s,'reason_codes':[]})
    report={'reviewed':candidates,'report_a':candidates.copy(),'report_c':candidates[:1],
        'approved_count':2,'entry_quality_shortlist_limit':1}
    refresh_report(report,NOW,EntryQualityConfig(mode='ENFORCE'))
    assert [r['symbol'] for r in report['report_c']]==['NEXT']
    assert [r['technical_score'] for r in report['report_a']]==[90,80]


def test_scan_pipeline_appends_quality_without_network_reads_or_orders(monkeypatch):
    monkeypatch.setenv("FUTURES_ENTRY_QUALITY_MODE","REPORT_ONLY")
    from futures_mode_fixture import fixture,NOW as when
    from unittest.mock import patch
    scanner,provider=fixture(['SBIN'])
    with patch('src.futures.gateway.sleep'):
        report=scanner.scan(now=when,include_backtest=False)
    assert report['entry_quality_summary']['mode']=='REPORT_ONLY'
    assert report['entry_quality_summary']['changed_decisions']==0
    assert all('entry_quality' in item for item in report['reviewed'])
    assert all('entry_quality_input' in item for item in report['reviewed'] if item.get('execution_reviewed'))
    provider.provider.kite.place_order.assert_not_called()


def test_malformed_provider_response_is_unavailable_not_a_scan_crash():
    from unittest.mock import Mock
    from src.futures.entry_quality import snapshot
    item={'symbol':'TEST','side':'LONG','final_decision':'UNKNOWN'}
    captured=snapshot(item,Mock(),NOW)
    assert captured['quote']['timestamp'] is None
    assert evaluate(captured)['classification']=='SKIP'


def test_snapshot_does_not_share_mutable_market_objects():
    from src.futures.entry_quality import snapshot
    source=sample();frame=bars()
    item={'symbol':'SYNTHETIC','side':'LONG','final_decision':'APPROVED','plan':source['plan'],
        'contract':source['contract'],'execution_checks':{},'reason_codes':[]}
    quote={'timestamp':NOW,'last_price':101,'instrument_token':42,'ohlc':{'close':100,'open':100}}
    data={'quote':quote,'spot_quote':quote.copy(),'intraday':frame,'underlying_intraday':frame}
    captured=snapshot(item,data,NOW)
    quote['ohlc']['close']=999;frame.iloc[0,frame.columns.get_loc('Close')]=999
    assert captured['quote']['ohlc']['close']==100
    assert captured['source_session'][0]['Close']==100


def test_fifteen_minute_directional_trend_conflict_cannot_confirm():
    f=bars()
    f.loc[f.index[-3:],['Low','Close']]=[99.4,99.5]
    result=fifteen(f,f,NOW,'LONG')
    assert result['status']=='BLOCKED'
    assert 'LONG_15M_FUTURES_TREND_CONFLICT' in result['reason_codes']


def test_fifteen_minute_break_without_a_retest_waits():
    f=bars();f.loc[f.index[-3:],'Low']=100.4
    result=fifteen(f,f,NOW,'LONG')
    assert result['status']=='WAIT'
    assert 'LONG_15M_FUTURES_COMPLETED_RETEST_REQUIRED' in result['reason_codes']
