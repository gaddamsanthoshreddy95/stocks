from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import sqlite3
import pandas as pd
import pytest

from src.futures.preparation import PreparedDataProvider
from src.futures.scanner import FuturesOpportunityScanner
from src.ui.stock_tables import context_columns
from scripts.diagnose_futures_scans import diagnose
from kiteconnect.exceptions import TokenException

NOW=pd.Timestamp('2026-10-15 09:40',tz='Asia/Kolkata')


@pytest.mark.parametrize('quote,reason',[
    ({},'LIVE_UNDERLYING_QUOTE_MISSING'),
    ({'timestamp':'2026-10-14T15:30:00+05:30'},'LIVE_UNDERLYING_QUOTE_STALE'),
    ({'timestamp':'2026-10-15T09:41:00+05:30'},'LIVE_UNDERLYING_QUOTE_STALE')])
def test_live_cannot_silently_score_old_cached_prices(quote,reason):
    cached=pd.DataFrame({'Close':[100]},index=[NOW.normalize()-pd.Timedelta(days=1)])
    manager=SimpleNamespace(scanner=SimpleNamespace(provider=object()),mode='LIVE_SCAN',
        now=NOW,clock=lambda:NOW,errors={},cache=SimpleNamespace(frame=lambda *a,**k:cached))
    provider=PreparedDataProvider(manager)
    provider.quotes={'NSE:STOCK':quote}
    with pytest.raises(ValueError,match=reason):provider.get_data('STOCK')
    assert manager.errors['NSE:STOCK:live_quote']['type']==reason


def test_new_manual_snapshot_uses_new_quote_and_keeps_saved_history_unchanged():
    cached=pd.DataFrame({'Open':[100.],'High':[102.],'Low':[99.],'Close':[101.],'Volume':[1000.]},index=[NOW.normalize()-pd.Timedelta(days=1)])
    manager=SimpleNamespace(scanner=SimpleNamespace(provider=object()),mode='LIVE_SCAN',
        now=NOW,clock=lambda:NOW,errors={},cache=SimpleNamespace(frame=lambda *a,**k:cached))
    provider=PreparedDataProvider(manager)
    provider.quotes={'NSE:STOCK':{'timestamp':NOW.isoformat(),'last_price':102,'volume':500,'ohlc':{'open':101,'high':104,'low':100}}}
    first=provider.get_data('STOCK')
    provider.quotes['NSE:STOCK']['last_price']=103
    second=provider.get_data('STOCK')
    assert first.Close.iloc[-1]==102 and second.Close.iloc[-1]==103
    assert len(cached)==1 and cached.Close.iloc[-1]==101


def test_original_history_exception_survives_to_display():
    scanner=FuturesOpportunityScanner.__new__(FuturesOpportunityScanner)
    scanner.context_cache={};scanner.history_failures={};scanner._clock=lambda:NOW
    scanner.provider=SimpleNamespace(get_data=Mock(side_effect=ValueError('CANDLE_TIMESTAMPS_INVALID_OR_DUPLICATED')))
    assert scanner._history('STOCK') is None
    row=context_columns({'score_failure':{'detail':'NO_VALID_EXCHANGE_SESSION_CANDLES',
        'history_failure':scanner.history_failures['STOCK']}})
    assert row['Score failure']=='CANDLE_TIMESTAMPS_INVALID_OR_DUPLICATED'
    assert row['Score basis']=='UNAVAILABLE'


def test_history_auth_failure_is_not_converted_to_candles_missing():
    scanner=FuturesOpportunityScanner.__new__(FuturesOpportunityScanner)
    scanner.context_cache={};scanner.history_failures={};scanner._clock=lambda:NOW
    scanner.provider=SimpleNamespace(get_data=Mock(side_effect=TokenException('invalid token')))
    with pytest.raises(TokenException):scanner._history('STOCK')
    assert 'STOCK' not in scanner.context_cache


def test_account_auth_failure_reaches_workspace_recovery():
    scanner=FuturesOpportunityScanner.__new__(FuturesOpportunityScanner)
    scanner._fixed_clock=True;scanner._clock=lambda:NOW
    scanner.account_gateway=SimpleNamespace(account_snapshot=lambda _: {'status':'UNKNOWN','authentication_failed':True})
    with pytest.raises(TokenException):scanner._read_execution_budget([])


def test_saved_pair_replaces_stale_client_without_mixing_partial_pair(tmp_path):
    from src.providers.kite_provider import KiteProvider
    provider=KiteProvider.__new__(KiteProvider)
    provider.kite=SimpleNamespace(api_key='old',set_access_token=Mock())
    env=tmp_path/'.env'
    env.write_text('KITE_API_KEY=new-key\nKITE_ACCESS_TOKEN=new-token\n')
    assert provider.reload_saved_credentials(env)
    assert provider.kite.api_key=='new-key'
    provider.kite.set_access_token.assert_called_once_with('new-token')
    env.write_text('KITE_API_KEY=incomplete\n')
    assert not provider.reload_saved_credentials(env)
    assert provider.kite.api_key=='new-key'
    assert provider.kite.set_access_token.call_count==1


def test_standalone_manual_job_reloads_before_scan():
    from src.data_provider.kite_data_provider import KiteDataProvider
    from src.ui.futures_history import scan_and_save
    provider=KiteDataProvider.__new__(KiteDataProvider)
    calls=[]
    provider.provider=SimpleNamespace(reload_saved_credentials=lambda:calls.append('reload'))
    platform=SimpleNamespace(provider=provider,scan_futures_opportunities=lambda *a,**k:(calls.append('scan') or {'reviewed':[]}))
    database=SimpleNamespace(save_futures_scan=lambda *a:1)
    with patch('src.ui.futures_history.TradingPlatform',return_value=platform):
        scan_and_save(SimpleNamespace(market_data_source='kite'),database,5,'LIVE_SCAN','test')
    assert calls==['reload','scan']


def test_account_gateway_preserves_auth_failure_without_secret_message():
    from src.futures.gateway import KiteGateway
    gateway=KiteGateway.__new__(KiteGateway)
    gateway.kite=SimpleNamespace(profile=Mock())
    gateway.request=Mock(side_effect=TokenException('private text'))
    snapshot=gateway.account_snapshot(NOW)
    assert snapshot['authentication_failed'] is True
    assert snapshot['status']=='UNKNOWN'
    assert 'private text' not in str(snapshot)


def test_empty_history_is_refetched_instead_of_sticking_in_process_cache(tmp_path):
    from src.data_provider.kite_data_provider import KiteDataProvider
    valid=pd.DataFrame({'Open':[100.],'High':[102.],'Low':[99.],'Close':[101.],'Volume':[1000.]},index=[NOW.normalize()-pd.Timedelta(days=1)])
    feed=SimpleNamespace(get_historical_data=Mock(side_effect=[pd.DataFrame(),valid]))
    provider=KiteDataProvider(feed,history_cache_directory=tmp_path)
    assert provider.get_data('STOCK').empty
    assert 'STOCK' not in provider._history_cache
    # Also recover an empty cache entry left by the previous implementation.
    provider._history_cache['STOCK']=pd.DataFrame()
    assert not provider.get_data('STOCK').empty
    assert feed.get_historical_data.call_count==2


def test_insufficient_history_records_required_rows_without_inventing_score():
    from futures_mode_fixture import fixture, NOW as scan_time
    scanner,provider=fixture(['SBIN'])
    provider.get_data=Mock(return_value=provider.daily.tail(20))
    report=scanner.scan(now=scan_time,include_backtest=False)
    assert report['approved_count']==0
    for item in report['reviewed']:
        assert item['technical_score'] is None
        assert item['score_failure']['detail']=='INSUFFICIENT_EMA200_HISTORY'
        assert item['score_failure']['history_rows']==20
        assert item['score_failure']['required_history']==scanner.config.minimum_history


def test_diagnostic_reads_failures_without_mutating_database(tmp_path):
    path=tmp_path/'saved.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE ft_jobs(kind,started_at,status,result,error)')
        report={'other':[{'symbol':'STOCK','side':'SHORT','original_daily_score':None,'reason_codes':['DISCOVERY_DATA_UNAVAILABLE']}]}
        c.execute('INSERT INTO ft_jobs VALUES(?,?,?,?,?)',('DAILY_TRADING','2026-10-15','COMPLETED',json.dumps(report),None))
    before=path.read_bytes()
    result=diagnose(path)['Futures Trading']
    assert result['scored_rows']==0 and result['reasons']=={'DISCOVERY_DATA_UNAVAILABLE':1}
    assert path.read_bytes()==before
