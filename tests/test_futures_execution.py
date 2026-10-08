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
    assert kite.method_calls[-1][0] == 'quote'


def test_first_completed_bar_uses_prior_sessions_to_warm_indicators():
    data = fixture()
    early = NOW.normalize() + pd.Timedelta(hours=9, minutes=20)
    data['quote']['timestamp'] = early
    scores = assess_execution(data, now=early)
    for key in ['futures_vwap_quality', 'futures_rvol_quality', 'futures_atr_quality',
                'futures_rsi_quality', 'futures_adx_quality', 'futures_oi_quality',
                'futures_spread_quality', 'futures_depth_quality']:
        assert scores[key].status != 'UNKNOWN', key
    assert scores['futures_rvol_quality'].factors['rvol'] == 2
    assert scores['futures_vwap_quality'].factors['completed_session_bars'] == 1


def test_missing_daily_does_not_suppress_vwap_rvol_or_intraday_indicators():
    data = fixture()
    data['daily'] = pd.DataFrame()
    scores = assess(data)
    assert scores['futures_atr_quality'].status == 'UNKNOWN'
    assert scores['futures_oi_quality'].status == 'UNKNOWN'
    for key in ['futures_vwap_quality', 'futures_rvol_quality', 'futures_rsi_quality',
                'futures_adx_quality', 'futures_spread_quality', 'futures_depth_quality']:
        assert scores[key].status != 'UNKNOWN'


def test_missing_intraday_does_not_suppress_oi_or_order_book():
    data = fixture()
    data['intraday'] = pd.DataFrame()
    scores = assess(data)
    assert scores['futures_vwap_quality'].status == 'UNKNOWN'
    assert scores['futures_oi_quality'].status == 'PASS'
    assert scores['futures_depth_quality'].status == 'PASS'
    assert scores['futures_spread_quality'].status == 'PASS'


@pytest.mark.parametrize('seconds,expected', [(120, 'PASS'), (121, 'UNKNOWN'), (-1, 'UNKNOWN')])
def test_quote_freshness_boundaries(seconds, expected):
    data = fixture()
    data['quote']['timestamp'] = NOW - pd.Timedelta(seconds=seconds)
    assert assess(data)['futures_spread_quality'].status == expected


def test_opening_bar_and_gaps_required_but_forming_candle_is_excluded():
    data = fixture()
    baseline = assess(data)['futures_rsi_quality'].factors['rsi_14']
    data['intraday'].loc[NOW, ['Open', 'High', 'Low', 'Close']] = [999, 1000, 998, 999]
    assert assess(data)['futures_rsi_quality'].factors['rsi_14'] == baseline
    data['intraday'] = data['intraday'].drop(NOW.normalize() + pd.Timedelta(hours=10))
    scores = assess(data)
    assert scores['futures_vwap_quality'].status == 'UNKNOWN'
    assert scores['futures_oi_quality'].status == 'PASS'


def test_old_daily_oi_baseline_is_unavailable():
    data = fixture()
    data['daily'] = data['daily'].iloc[:-1]
    scores = assess(data)
    assert scores['futures_oi_quality'].status == 'UNKNOWN'
    assert scores['futures_atr_quality'].status == 'UNKNOWN'
    assert scores['futures_vwap_quality'].status != 'UNKNOWN'


def test_contract_token_mismatch_cannot_approve():
    data = fixture()
    data['instrument_token'] = 42
    data['quote']['instrument_token'] = 99
    assert assess(data)['futures_spread_quality'].status == 'UNKNOWN'


@pytest.mark.parametrize('price,quantity', [(float('nan'), 1000), (101, -1), (101, float('inf'))])
def test_invalid_depth_is_not_silently_ignored(price, quantity):
    data = fixture()
    data['quote']['depth']['buy'].append({'price': price, 'quantity': quantity})
    scores = assess(data)
    assert scores['futures_depth_quality'].status == 'UNKNOWN'
    assert scores['futures_oi_quality'].status == 'PASS'


def test_lot_size_does_not_control_spread_availability():
    data = fixture()
    data['contract']['lot_size'] = 0
    scores = assess(data)
    assert scores['futures_spread_quality'].status == 'PASS'
    assert scores['futures_depth_quality'].status == 'UNKNOWN'


def test_flat_market_has_zero_adx_and_neutral_rsi():
    data = fixture()
    data['intraday'][['Open', 'High', 'Low', 'Close']] = 100
    scores = assess(data)
    assert scores['futures_adx_quality'].factors['adx_14'] == 0
    assert scores['futures_adx_quality'].status == 'FAIL'
    assert scores['futures_rsi_quality'].factors['rsi_14'] == 50


def test_wilder_indicators_on_constant_range_rising_prices():
    frame = candles(pd.date_range('2026-09-01', periods=40, freq='5min'),
                    np.arange(100, 140, dtype=float))
    atr, adx, rsi = indicators(frame)
    assert atr.iloc[-1] == pytest.approx(2)
    assert adx.iloc[-1] == pytest.approx(100)
    assert rsi.iloc[-1] == pytest.approx(100)


def test_after_hours_keeps_report_results_but_no_live_entry_approval():
    data = fixture()
    closed = NOW.normalize() + pd.Timedelta(hours=18)
    data['quote']['timestamp'] = closed
    scores = assess_execution(data, now=closed)
    assert scores['futures_spread_quality'].status == 'UNKNOWN'
    assert scores['futures_spread_quality'].reason_codes == ['FUTURES_MARKET_CLOSED']


def test_configured_holiday_keeps_previous_session_oi_valid(monkeypatch):
    monkeypatch.setenv('MARKET_HOLIDAYS_IST', '2026-10-07')
    data = fixture()
    data['daily'] = data['daily'].iloc[:-1]
    assert assess(data)['futures_oi_quality'].status == 'PASS'


def test_provider_keeps_quote_when_history_request_fails():
    from src.data_provider.kite_data_provider import KiteDataProvider
    from requests.exceptions import ConnectionError
    provider = object.__new__(KiteDataProvider)
    kite = Mock()
    provider.provider = Mock(kite=kite)
    provider.get_nfo_instruments = lambda: [{'tradingsymbol': 'TEST26OCTFUT',
        'segment': 'NFO-FUT', 'instrument_token': 42}]
    kite.historical_data.side_effect = [ConnectionError('unavailable'), []]
    kite.quote.return_value = {'NFO:TEST26OCTFUT': {'last_price': 101, 'oi': 100}}
    result = provider.get_futures_execution_data('TEST', {'tradingsymbol': 'TEST26OCTFUT'})
    assert result['quote']['oi'] == 100
    assert result['fetch_errors'] == {'daily': 'ConnectionError'}
    assert kite.method_calls[-1][0] == 'quote'


def test_exact_contract_history_shares_provider_rate_limit():
    from threading import Lock
    from unittest.mock import patch
    from src.providers.kite_provider import KiteProvider
    from src.data_provider.kite_data_provider import KiteDataProvider
    raw = object.__new__(KiteProvider)
    raw.kite = Mock()
    raw._historical_lock = Lock()
    raw._last_historical_request = 10
    raw._historical_min_interval = .34
    raw.kite.historical_data.return_value = []
    raw.kite.quote.return_value = {}
    provider = object.__new__(KiteDataProvider)
    provider.provider = raw
    provider.get_nfo_instruments = lambda: [{'tradingsymbol': 'TEST26OCTFUT',
        'segment': 'NFO-FUT', 'instrument_token': 42}]
    with patch('src.providers.kite_provider.monotonic', return_value=10.1), \
            patch('src.providers.kite_provider.sleep') as pause:
        provider.get_futures_execution_data('TEST', {'tradingsymbol': 'TEST26OCTFUT'})
    assert pause.call_count == 2
    assert pause.call_args_list[0].args[0] == pytest.approx(.24)
    assert len(raw.kite.historical_data.call_args_list) == 2
    assert raw.kite.method_calls[-1][0] == 'quote'


def test_report_renders_freshness_and_contract_evidence():
    from src.presenter.futures_report import FuturesReportPresenter
    scores = assess(fixture())
    output = FuturesReportPresenter.render({'reviewed': [{'symbol': 'TEST',
        'futures_selection': {'checks': {key: value.to_dict() for key, value in scores.items()}}}]})
    assert 'quote_timestamp: 2026-10-08' in output
    assert 'checked_at: 2026-10-08' in output
    assert 'candle_timestamp:' in output
