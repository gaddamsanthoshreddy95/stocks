from dataclasses import replace
from unittest.mock import Mock
import numpy as np
import pandas as pd
import pytest
from src.quality.futures_execution import CHECKS, FuturesExecutionConfig, assess_execution, wilder, indicators
from src.quality.futures_selection import REQUIRED_CHECKS, assess_futures_selection

NOW = pd.Timestamp('2026-10-08 13:00', tz='Asia/Kolkata')

def candles(index, values, volume=100):
    return pd.DataFrame({'Open':values, 'High':values+1, 'Low':values-1,
                         'Close':values, 'Volume':volume, 'OI':1000}, index=index)

def fixture():
    di = pd.date_range('2026-08-01', '2026-10-07', freq='B', tz='Asia/Kolkata')
    daily = candles(di, np.full(len(di), 100.0))
    frames=[]
    for day in pd.date_range('2026-09-28','2026-10-08',freq='B',tz='Asia/Kolkata'):
        index=pd.date_range(day+pd.Timedelta(hours=9,minutes=15), periods=75,freq='5min')
        values=100+np.arange(75)*.02+np.sin(np.arange(75))*.3
        frames.append(candles(index,values,200 if day.date()==NOW.date() else 100))
    intra=pd.concat(frames)
    return {'daily':daily,'intraday':intra,'contract':{'lot_size':100},'spot_price':100,
            'quote':{'timestamp':NOW,'last_price':101,'oi':1100,
                'depth':{'buy':[{'price':100.99,'quantity':1000}],
                         'sell':[{'price':101.01,'quantity':1000}]}}}

def assess(data,**kw):
    return assess_execution(data, now=NOW,levels={'support':98,'resistance':102}, **kw)

def test_missing_and_stale_data_block_execution():
    assert all(x.status=='UNKNOWN' for x in assess(None).values())
    data=fixture();data['quote']['timestamp']=NOW-pd.Timedelta(minutes=6)
    scores=assess(data)
    assert scores['futures_spread_quality'].status=='UNKNOWN'
    approved={k:{'status':'PASS'} for k in REQUIRED_CHECKS}
    assert not assess_futures_selection(approved,[{}],scores)['eligible']

@pytest.mark.parametrize('gate',CHECKS)
def test_every_new_gate_blocks_even_with_company_checks_passing(gate):
    approved={k:{'status':'PASS'} for k in REQUIRED_CHECKS}
    scores={k:{'status':'PASS'} for k in CHECKS}
    assert assess_futures_selection(approved,[{}],scores)['eligible']
    for status in ['FAIL','UNKNOWN']:
        result=assess_futures_selection(approved,[{}],{**scores,gate:{'status':status}})
        assert not result['eligible']
        assert gate in result['failed_checks']+result['unavailable_checks']

def test_matched_time_rvol_and_real_oi_baseline():
    scores=assess(fixture())
    assert scores['futures_rvol_quality'].factors['rvol']==2
    assert scores['futures_oi_quality'].status=='PASS'
    assert scores['futures_oi_quality'].reason_codes==['LONG_BUILDUP']
    assert scores['futures_spread_quality'].status=='PASS'
    assert scores['futures_depth_quality'].status=='PASS'
    assert scores['futures_target_space_quality'].status=='PASS'
    assert scores['futures_gap_quality'].status=='PASS'
    assert scores['futures_adx_quality'].status!='UNKNOWN'
    assert scores['futures_rsi_quality'].status!='UNKNOWN'

def test_missing_oi_does_not_hide_other_checks():
    data=fixture();data['daily']=data['daily'].drop(columns='OI')
    scores=assess(data)
    assert scores['futures_oi_quality'].status=='UNKNOWN'
    assert scores['futures_ema_quality'].status!='UNKNOWN'
    assert scores['futures_gap_quality'].status=='PASS'

def test_oi_direction_and_bad_depth():
    data=fixture();data['quote']['last_price']=99
    assert assess(data)['futures_oi_quality'].reason_codes==['SHORT_BUILDUP']
    assert assess(data)['futures_oi_quality'].status=='FAIL'
    assert assess(data,direction='BEARISH')['futures_oi_quality'].status=='PASS'
    data['quote']['depth']['sell'][0]['price']=98
    assert assess(data)['futures_spread_quality'].status=='UNKNOWN'


def test_rvol_incomplete_baseline_and_opening_bar():
    data=fixture();data['intraday']=data['intraday'].loc[data['intraday'].index.date==NOW.date()]
    assert assess(data)['futures_rvol_quality'].status=='UNKNOWN'
    data['intraday']=data['intraday'].iloc[1:]
    assert assess(data)['futures_vwap_quality'].status=='UNKNOWN'


def test_sector_comparison_and_event_coverage():
    data=fixture();stock=data['daily'].copy();sector=stock.copy()
    stock['Close']=np.linspace(90,110,len(stock));stock['High']=stock.Close+1;stock['Low']=stock.Close-1;stock['Open']=stock.Close
    kw={'stock_history':stock,'sector_history':sector,'event':{'event_data_availability_state':'COMPLETE','hard_block':False,'event_risk_level':'LOW'}}
    scores=assess(data,**kw)
    assert scores['futures_sector_strength_quality'].status=='PASS'
    assert scores['futures_event_quality'].status=='PASS'
    kw['event']['hard_block']=True
    assert assess(data,**kw)['futures_event_quality'].status=='FAIL'
    kw['event']['event_data_availability_state']='PARTIAL'
    assert assess(data,**kw)['futures_event_quality'].status=='UNKNOWN'


def test_wilder_seed_and_flat_rsi():
    s=pd.Series(range(1,17),dtype=float)
    result=wilder(s)
    assert result.iloc[13]==7.5
    assert result.iloc[14]==pytest.approx((7.5*13+15)/14)
    daily=fixture()['daily']
    _,_,rsi=indicators(daily)
    assert rsi.iloc[-1]==50


def test_config_and_margin_exclusion():
    with pytest.raises(ValueError): replace(FuturesExecutionConfig(),minimum_rvol=-1)
    assert not any('margin' in x for x in CHECKS)


def test_provider_uses_exact_expiry_quotes_and_oi_candles_without_margin():
    from src.data_provider.kite_data_provider import KiteDataProvider
    provider = object.__new__(KiteDataProvider)
    kite = Mock()
    provider.provider = Mock(kite=kite)
    provider.get_nfo_instruments = lambda: [{'tradingsymbol':'TEST26OCTFUT',
                                            'segment':'NFO-FUT','instrument_token':42}]
    kite.quote.return_value = {'NFO:TEST26OCTFUT':{'last_price':101},
                               'NSE:TEST':{'last_price':100}}
    kite.historical_data.return_value = [{'date':NOW,'open':100,'high':102,
                                          'low':99,'close':101,'volume':100,'oi':1000}]
    result = provider.get_futures_execution_data('TEST',{'tradingsymbol':'TEST26OCTFUT','lot_size':100})
    kite.quote.assert_called_once_with(['NFO:TEST26OCTFUT','NSE:TEST'])
    assert result['spot_price']==100
    assert 'OI' in result['daily']
    assert len(kite.historical_data.call_args_list)==2
    for call in kite.historical_data.call_args_list:
        assert call.args[0]==42
        assert call.kwargs=={'continuous':False,'oi':True}
    assert not kite.order_margins.called
    assert not kite.place_order.called
