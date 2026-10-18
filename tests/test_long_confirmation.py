"""Synthetic symmetry regressions, not a reconstruction of an actual BEL trade."""
from copy import deepcopy
from dataclasses import replace
import pandas as pd
import pytest
from test_short_confirmation import inputs,NOW
from src.futures.config import FuturesScanConfig
from src.futures.long_confirmation import evaluate
from src.futures.entry_validity import entry_status


def long_inputs():
    data,oi=inputs()
    # Reflect synthetic OHLC around a positive level to make an upward setup.
    for key in ('underlying_intraday','intraday','short_nifty_intraday','short_sector_intraday'):
        frame=data[key].copy()
        old=frame.copy()
        frame['Open']=400-old.Open;frame['Close']=400-old.Close
        frame['High']=400-old.Low;frame['Low']=400-old.High
        data[key]=frame
    for key in ('quote','spot_quote'):data[key]['last_price']=400-data[key]['last_price']
    oi.factors['price_change_percent']=1
    return data,oi


def test_successful_breakout_retest_and_rising_oi_pass():
    data,oi=long_inputs();gate=evaluate(data,oi,NOW,FuturesScanConfig())
    assert gate['status']=='PASS',gate
    assert gate['evidence']['breakout_retest']['trigger']>gate['evidence']['breakout_retest']['invalidation']


@pytest.mark.parametrize('failure',['quote','sector','oi','strength','price'])
def test_bearish_conflicts_and_missing_data_never_pass(failure):
    data,oi=long_inputs()
    if failure=='quote':data['quote']['timestamp']=(NOW-pd.Timedelta(seconds=31)).isoformat()
    if failure=='sector':data['short_sector_intraday']=None
    if failure=='oi':oi.factors['oi_change_percent']=-2
    if failure=='price':data['quote']['last_price']=190
    if failure=='strength':
        f=data['short_sector_intraday'];f.iloc[-1,f.columns.get_loc('Close')]=310;f.iloc[-1,f.columns.get_loc('High')]=311
    assert evaluate(data,oi,NOW,FuturesScanConfig())['status'] in ('BLOCKED','UNKNOWN')


def test_future_bars_cannot_change_long_decision():
    data,oi=long_inputs();expected=evaluate(data,oi,NOW,FuturesScanConfig())
    for key in ('underlying_intraday','intraday','short_nifty_intraday','short_sector_intraday'):
        f=data[key];future=f.iloc[[-1]].copy();future.index=[NOW];future['Close']=-999
        data[key]=pd.concat([f,future])
    assert evaluate(data,oi,NOW,FuturesScanConfig())==expected


def test_cutoffs_are_enforced_independent_of_gate_toggles():
    c=FuturesScanConfig(news_conflict_gate_enabled=0,long_confirmation_gate_enabled=0,
        short_confirmation_gate_enabled=0,entry_cutoff_minute=15,intraday_exit_minute=20)
    assert (c.entry_cutoff_hour,c.entry_cutoff_minute)==(15,0)
    assert (c.intraday_exit_hour,c.intraday_exit_minute)==(15,10)


def test_saved_approval_expiry_never_upgrades_research():
    row={'final_decision':'APPROVED','entry_valid_until':(NOW+pd.Timedelta(seconds=30)).isoformat()}
    assert entry_status(row,NOW).startswith('APPROVED SNAPSHOT')
    assert entry_status(row,NOW+pd.Timedelta(seconds=31))=='EXPIRED — RESCAN'
    assert entry_status({'final_decision':'APPROVED'},NOW)=='UNVERIFIED — RESCAN'
    assert entry_status({**row,'final_decision':'WAIT'},NOW)=='RESEARCH / NOT APPROVED'


def test_replay_rejects_later_record_and_reports_scope():
    from src.futures.confirmation_replay import replay
    data,oi=long_inputs()
    snapshot={'decision_at':NOW,'available_at':NOW+pd.Timedelta(seconds=1),'side':'LONG','data':data,'oi_check':oi}
    result=replay([snapshot],data['intraday'],1,FuturesScanConfig())
    assert result['decisions'][0]['reason_codes']==['SNAPSHOT_NOT_AVAILABLE_AT_DECISION']
    assert result['trades']==[]
    assert 'NOT_FULL_SCANNER' in result['scope']


@pytest.mark.parametrize('status,expected,trigger,invalidation',[(
    'PASS','APPROVED',200,199.8),('WAIT','WAIT',200,199.8),
    ('BLOCKED','REJECT',200,199.8),('UNKNOWN','UNKNOWN',200,199.8),
    ('PASS','REJECT',200,199.5),('PASS','REJECT',199,199.8),('PASS','REJECT',200.1,199.8)])
def test_long_gate_controls_execution_and_preserves_score(status,expected,trigger,invalidation):
    from unittest.mock import patch
    import numpy as np
    from test_bidirectional_futures import scanner_fixture,candles,NOW as when
    from futures_mode_fixture import SimulatedProvider,SyntheticNoCostModel
    from src.quality.futures_execution import CHECKS,check
    scanner,_=scanner_fixture();scanner.costs=SyntheticNoCostModel()
    item={'symbol':'TEST','side':'LONG','technical_score':95,'setup_type':'BULLISH_TREND_CONTINUATION',
        'reason_codes':[],'company_research':{'unavailable_checks':[],'failed_checks':[],'policy_review_required':False}}
    setup={'technical_score':95,'confirmed':True,'timing':'READY','reason_codes':[],
        'setup_type':'BULLISH_TREND_CONTINUATION','evidence':{'support':190,'resistance':210}}
    checks={key:check(True,{},'VALIDATED') for key in CHECKS}
    checks['futures_oi_quality']=check(True,{'price_change_percent':1,'oi_change_percent':2},'LONG_BUILDUP')
    data={'daily':candles(np.full(260,200)),'intraday':SimulatedProvider(['TEST']).intraday,
        'quote':{'timestamp':when.isoformat(),'last_price':200,'depth':{'buy':[{'price':200,'quantity':10000}], 'sell':[{'price':200.02,'quantity':10000}]}}}
    gate={'status':status,'checked_at':when.isoformat(),'reason_codes':['TEST_CONFIRMATION_'+status],
        'evidence':{'breakout_retest':{'trigger':trigger,'invalidation':invalidation}}}
    with patch('src.futures.scanner.assess_execution',return_value=checks),patch('src.futures.scanner.directional_setup',return_value=setup),patch('src.futures.scanner.long_confirmation',return_value=gate):
        scanner._execution(item,data,{'lot_size':500},(None,None),{'news_state':'NO_RELEVANT_NEWS','checked_at':when.isoformat()},{},when,None)
    assert item['final_decision']==expected,item
    assert item['technical_score']==95
    assert item['historical']['long_confirmation_replayed'] is False
    if expected=='APPROVED':assert pd.Timestamp(item['entry_valid_until'])==when+pd.Timedelta(seconds=30)


def test_replay_pass_records_target_first_and_excursion_without_using_future_signal_data():
    from src.futures.confirmation_replay import replay
    data,oi=inputs();frame=data['intraday'].copy()
    future=pd.DataFrame([(196.49,196.6,195.5,195.8,1000)],index=[NOW],columns=['Open','High','Low','Close','Volume'])
    frame=pd.concat([frame,future])
    snapshot={'decision_at':NOW,'available_at':NOW,'side':'SHORT','data':data,'oi_check':oi}
    result=replay([snapshot],frame,1,FuturesScanConfig())
    assert result['decisions'][0]['status']=='PASS'
    assert result['trades']==[]  # Fixed stop cannot cover the wide synthetic retest high.
    assert result['metrics']['maximum_adverse_excursion_percent'] is None
    assert result['metrics']['target_first_percent'] is None  # Sample remains insufficient.


def test_replay_measures_target_first_and_mae_for_stop_compatible_setup():
    from src.futures.confirmation_replay import replay
    data,oi=inputs()
    for key in ('underlying_intraday','intraday','short_nifty_intraday','short_sector_intraday'):
        data[key][['Open','High','Low','Close']]=180+data[key][['Open','High','Low','Close']]*.1
    for key in ('quote','spot_quote'):data[key]['last_price']=180+data[key]['last_price']*.1
    future=pd.DataFrame([(199.649,199.7,198.8,199,1000)],index=[NOW],columns=['Open','High','Low','Close','Volume'])
    frame=pd.concat([data['intraday'],future])
    result=replay([{'decision_at':NOW,'available_at':NOW,'side':'SHORT','data':data,'oi_check':oi}],frame,1,FuturesScanConfig())
    assert result['trades'][0]['outcome']=='TARGET_FIRST',result
    assert result['metrics']['maximum_adverse_excursion_percent']>0
    assert result['metrics']['sample_status']=='INSUFFICIENT'
    assert result['metrics']['target_first_percent'] is None


def test_captured_evidence_is_json_safe_and_replays_same_decision():
    import json
    from src.futures.confirmation_replay import capture,replay
    data,oi=long_inputs()
    snapshot=json.loads(json.dumps(capture(data,oi,NOW,'LONG')))
    result=replay([snapshot],data['intraday'],1,FuturesScanConfig())
    assert result['decisions'][0]['status']==evaluate(data,oi,NOW,FuturesScanConfig())['status']
    assert all(len(snapshot['data'][key])<=75 for key in ('intraday','underlying_intraday'))
