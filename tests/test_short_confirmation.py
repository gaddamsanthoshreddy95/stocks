from dataclasses import replace
from types import SimpleNamespace
import pandas as pd
import pytest

from src.futures.config import FuturesScanConfig
from src.futures.short_confirmation import evaluate,still_valid

NOW=pd.Timestamp('2026-10-16 10:00',tz='Asia/Kolkata')


def inputs():
    # Synthetic session, not a replay of BEL or a claim about its actual prices.
    index=pd.date_range(NOW.normalize()+pd.Timedelta(hours=9,minutes=15),periods=9,freq='5min')
    rows=[(202,203,200,201),(201,202,199,200),(200,201,198,199),
        (199,199.5,197.5,197.8),(197.9,198.2,197,197.2),
        (197.3,197.6,197,197.1),(197.2,197.5,196.9,197.05),
        (197.1,197.5,196.8,197),(197.1,198.1,196.5,196.9)]
    frame=pd.DataFrame(rows,index=index,columns=['Open','High','Low','Close'])
    frame['Volume']=1000
    benchmark=frame.copy()
    for col in ('Open','High','Low','Close'):benchmark[col]=100.0
    benchmark['High']=100.1;benchmark['Low']=99.9
    data={'underlying_intraday':frame.copy(),'intraday':frame.copy(),
        'short_nifty_intraday':benchmark.copy(),'short_sector_intraday':benchmark.copy(),
        'instrument_token':42,'quote':{'instrument_token':42,'last_price':196.49,'timestamp':NOW.isoformat()}}
    # Weak sector supports the short; stock remains weaker than its sector.
    sector=data['short_sector_intraday']
    sector.iloc[-1,sector.columns.get_loc('Close')]=99.8
    sector.iloc[-1,sector.columns.get_loc('Low')]=99.7
    data['spot_quote']={'last_price':196.49,'timestamp':NOW.isoformat()}
    oi=SimpleNamespace(status='PASS',factors={'price_change_percent':-1,'oi_change_percent':2})
    return data,oi


def test_completed_break_retest_falling_vwaps_and_oi_can_pass():
    data,oi=inputs()
    gate=evaluate(data,oi,NOW,FuturesScanConfig())
    assert gate['status']=='PASS',gate
    assert gate['evidence']['breakdown_retest']['breakdown_at'].endswith('09:35:00+05:30')
    assert gate['evidence']['breakdown_retest']['retest_at'].endswith('09:55:00+05:30')


@pytest.mark.parametrize('change,code',[
    ('price','SHORT_PRICE_NOT_BELOW_BOTH_VWAPS'),
    ('oi','SHORT_PRICE_FALL_WITH_RISING_OI_REQUIRED'),
    ('strength','SHORT_STOCK_STRONGER_THAN_SECTOR_INTRADAY')])
def test_bullish_conflicts_block_short(change,code):
    data,oi=inputs()
    if change=='price':data['quote']['last_price']=210
    if change=='oi':oi.factors.update(price_change_percent=1,oi_change_percent=-2)
    if change=='strength':
        frame=data['short_sector_intraday']
        frame.iloc[-1,frame.columns.get_loc('Close')]=95
        frame.iloc[-1,frame.columns.get_loc('Low')]=94
    gate=evaluate(data,oi,NOW,FuturesScanConfig())
    assert gate['status']=='BLOCKED' and code in gate['reason_codes']


def test_rising_stock_vwap_and_buying_volume_conflict_are_recorded():
    data,oi=inputs()
    stock=data['underlying_intraday']
    stock.iloc[-3:,:4]=[(198,199,197,198.5),(199,200,198,199.5),(200,201,199,200.5)]
    stock.loc[stock.index[-3:],'Volume']=10000
    gate=evaluate(data,oi,NOW,FuturesScanConfig())
    assert gate['status']=='BLOCKED'
    assert 'SHORT_VWAP_RISING' in gate['reason_codes']
    assert 'SHORT_BULLISH_PRICE_VOLUME_CONFLICT' in gate['reason_codes']


def test_without_failed_retest_waits_instead_of_approving():
    data,oi=inputs()
    f=data['intraday']
    for i,stamp in enumerate(f.index):
        opening=200-i
        f.loc[stamp,['Open','High','Low','Close']]=[opening,opening+.1,opening-.6,opening-.5]
    data['quote']['last_price']=191.3
    gate=evaluate(data,oi,NOW,FuturesScanConfig())
    assert gate['status']=='WAIT',gate
    assert 'SHORT_COMPLETED_BREAKDOWN_AND_FAILED_RETEST_REQUIRED' in gate['reason_codes']


def test_retest_without_trigger_crossing_waits():
    data,oi=inputs()
    data['quote']['last_price']=196.7
    gate=evaluate(data,oi,NOW,FuturesScanConfig())
    assert gate['status']=='WAIT' and 'SHORT_RETEST_TRIGGER_NOT_CROSSED' in gate['reason_codes']


@pytest.mark.parametrize('failure',['stock','sector','gap','quote','spot_quote','identity','oi'])
def test_missing_stale_and_invalid_data_never_pass(failure):
    data,oi=inputs()
    if failure=='stock':data['underlying_intraday']=None
    if failure=='sector':data['short_sector_intraday']=None
    if failure=='gap':data['intraday']=data['intraday'].drop(data['intraday'].index[3])
    if failure=='quote':data['quote']['timestamp']=(NOW-pd.Timedelta(seconds=31)).isoformat()
    if failure=='spot_quote':data['spot_quote']['timestamp']=(NOW-pd.Timedelta(seconds=31)).isoformat()
    if failure=='identity':data['quote']['instrument_token']=43
    if failure=='oi':oi.status='UNKNOWN'
    assert evaluate(data,oi,NOW,FuturesScanConfig())['status']=='UNKNOWN'


def test_only_information_available_at_decision_time_is_used():
    data,oi=inputs()
    expected=evaluate(data,oi,NOW,FuturesScanConfig())
    for key in ('underlying_intraday','intraday','short_nifty_intraday','short_sector_intraday'):
        f=data[key]
        future=f.iloc[[-1]].copy();future.index=[NOW]
        future['Close']=-999 # Still-forming/future bar is unavailable at NOW.
        data[key]=pd.concat([f,future])
    assert evaluate(data,oi,NOW,FuturesScanConfig())==expected


def test_no_blanket_0945_delay_but_no_four_bar_short():
    data,oi=inputs()
    now=NOW.normalize()+pd.Timedelta(hours=9,minutes=35)
    data['quote']['timestamp']=now.isoformat()
    data['spot_quote']['timestamp']=now.isoformat()
    gate=evaluate(data,oi,now,FuturesScanConfig())
    assert gate['status']=='UNKNOWN'
    assert gate['reason_codes']==['SHORT_BREAK_RETEST_HISTORY_INSUFFICIENT']


def test_configurable_disable_and_validation():
    assert evaluate({},None,NOW,replace(FuturesScanConfig(),short_confirmation_gate_enabled=0))['status']=='DISABLED'
    for kw in ({'short_confirmation_gate_enabled':2},{'short_confirmation_support_bars':2},
               {'short_confirmation_buying_volume_share':1.1},{'short_confirmation_max_age_seconds':0}):
        with pytest.raises(ValueError):replace(FuturesScanConfig(),**kw)


def test_final_report_rejects_expired_future_or_unverified_confirmation():
    config=FuturesScanConfig()
    gate={'status':'PASS','checked_at':NOW.isoformat()}
    assert still_valid(gate,NOW+pd.Timedelta(seconds=30),config)
    assert not still_valid(gate,NOW+pd.Timedelta(seconds=31),config)
    assert not still_valid(gate,NOW-pd.Timedelta(seconds=1),config)
    assert not still_valid({'status':'PASS','checked_at':'2026-10-16T10:00:00'},NOW,config)
    assert not still_valid({'status':'UNKNOWN','checked_at':NOW.isoformat()},NOW,config)


@pytest.mark.parametrize('status,expected',[('PASS','APPROVED'),('WAIT','WAIT'),('BLOCKED','REJECT'),('UNKNOWN','UNKNOWN')])
def test_gate_controls_approval_without_changing_score_or_waiving_other_checks(status,expected):
    from unittest.mock import patch
    import numpy as np
    from test_bidirectional_futures import scanner_fixture,candles,NOW as when
    from futures_mode_fixture import SimulatedProvider,SyntheticNoCostModel
    from src.quality.futures_execution import CHECKS,check
    scanner,_=scanner_fixture();scanner.costs=SyntheticNoCostModel()
    item={'symbol':'TEST','side':'SHORT','technical_score':95,'setup_type':'BEARISH_TREND_CONTINUATION',
        'reason_codes':[],'company_research':{'unavailable_checks':[],'failed_checks':[],'policy_review_required':False}}
    setup={'technical_score':95,'confirmed':True,'timing':'READY','reason_codes':[],
        'setup_type':'BEARISH_TREND_CONTINUATION','evidence':{'support':190,'resistance':210,'short_trigger_price':200,'setup_invalidation_price':200.1}}
    checks={key:check(True,{},'VALIDATED') for key in CHECKS}
    checks['futures_oi_quality']=check(True,{'price_change_percent':-1,'oi_change_percent':2},'SHORT_BUILDUP')
    data={'daily':candles(np.full(260,200)),'intraday':SimulatedProvider(['TEST']).intraday,
        'quote':{'timestamp':when.isoformat(),'last_price':200,'depth':{'buy':[{'price':200,'quantity':10000}], 'sell':[{'price':200.02,'quantity':10000}]}}}
    gate={'status':status,'checked_at':when.isoformat(),'reason_codes':['TEST_CONFIRMATION_'+status]}
    with patch('src.futures.scanner.assess_execution',return_value=checks),patch('src.futures.scanner.directional_setup',return_value=setup),patch('src.futures.scanner.short_confirmation',return_value=gate):
        scanner._execution(item,data,{'lot_size':500},(None,None),{'news_state':'NO_RELEVANT_NEWS','checked_at':when.isoformat()},{},when,None)
    assert item['final_decision']==expected
    assert item['technical_score']==95
    assert item['short_entry']['execution_approved']==(status=='PASS')
    assert item['historical']['short_confirmation_replayed'] is False


def test_directional_stock_vwap_corrects_only_short_rule_and_retains_original():
    from test_futures_research_and_timing import company,research
    from src.quality.models import QualityScore
    _,service,stock,sector,news,_=research(company())
    below=QualityScore(0,'FAIL',100,factors={'current_price':199,'vwap':200},reason_codes=['LEGACY_PRICE_BELOW_VWAP'])
    args=dict(news=news,stock_history=stock,sector_history=sector,session_vwap=below)
    legacy=service.assess('TEST','IT','SHORT',**args)
    updated=service.assess('TEST','IT','SHORT',directional_vwap=True,**args)
    assert legacy['checks']['vwap_quality']['status']=='FAIL'
    assert updated['checks']['vwap_quality']['status']=='PASS'
    assert updated['original_underlying_vwap_check']==below.to_dict()
    for name in legacy['checks']:
        if name!='vwap_quality':assert updated['checks'][name]==legacy['checks'][name]
    assert service.assess('TEST','IT','LONG',directional_vwap=True,**args)['checks']['vwap_quality']['status']=='FAIL'
    above=QualityScore(100,'PASS',100,factors={'current_price':201,'vwap':200})
    assert service.assess('TEST','IT','SHORT',directional_vwap=True,**{**args,'session_vwap':above})['checks']['vwap_quality']['status']=='FAIL'
    absent=QualityScore(None,'UNKNOWN',0,reason_codes=['MISSING'])
    assert service.assess('TEST','IT','SHORT',directional_vwap=True,**{**args,'session_vwap':absent})['checks']['vwap_quality']['status']=='UNKNOWN'


def test_quote_refresh_updates_only_requested_pair_and_keeps_history():
    from unittest.mock import Mock
    from src.futures.preparation import PreparedDataProvider
    fresh={'NFO:BEL26OCTFUT':{'timestamp':NOW.isoformat(),'last_price':377},'NSE:BEL':{'timestamp':NOW.isoformat(),'last_price':376}}
    manager=SimpleNamespace(scanner=SimpleNamespace(provider=object()),gateway=SimpleNamespace(quotes=Mock(return_value=fresh)))
    provider=PreparedDataProvider(manager)
    provider.quotes={'NSE:OTHER':{'last_price':100}}
    history=pd.DataFrame({'Close':[1]})
    data={'intraday':history,'daily':history}
    result=provider.refresh_execution_quotes('BEL',{'tradingsymbol':'BEL26OCTFUT'},data)
    assert result['intraday'] is history and result['daily'] is history
    assert result['spot_quote']==fresh['NSE:BEL'] and result['quote']==fresh['NFO:BEL26OCTFUT']
    assert provider.quotes['NSE:OTHER']=={'last_price':100}
    manager.gateway.quotes.assert_called_once_with(['NFO:BEL26OCTFUT','NSE:BEL'])


def test_stale_quote_refresh_rechecks_company_vwap_with_latest_stock_price():
    from unittest.mock import Mock
    from futures_mode_fixture import fixture,NOW as when
    scanner,provider=fixture(['SBIN'])
    scanner.config=replace(scanner.config,watch_score=0)
    original=provider.get_futures_execution_data.side_effect
    def stale(*args):
        data=original(*args)
        data['quote']={**data['quote'],'timestamp':(when-pd.Timedelta(seconds=31)).isoformat()}
        data['spot_price']=205
        data['spot_quote']={'last_price':205,'timestamp':(when-pd.Timedelta(seconds=31)).isoformat()}
        return data
    def refresh(symbol,contract,data):
        return {**data,'quote':{**data['quote'],'timestamp':when.isoformat()},
            'spot_quote':{'last_price':197,'timestamp':when.isoformat()},'spot_price':197}
    provider.get_futures_execution_data.side_effect=stale
    provider.refresh_execution_quotes=Mock(side_effect=refresh)
    report=scanner.scan(now=when,include_backtest=False)
    short=next(i for i in report['reviewed'] if i['side']=='SHORT')
    assert provider.refresh_execution_quotes.call_count==2  # Both directional evaluations refresh stale input.
    assert short['company_research']['checks']['vwap_quality']['factors']['current_price']==197
    assert short['company_research']['checks']['vwap_quality']['status']=='PASS'
    assert short['short_confirmation']['status']!='PASS'
    assert short['final_decision']!='APPROVED'
    provider.provider.kite.place_order.assert_not_called()
