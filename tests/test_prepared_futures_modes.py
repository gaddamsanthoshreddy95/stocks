from dataclasses import replace,asdict
from unittest.mock import patch
from time import perf_counter
import pandas as pd
import pytest

from futures_mode_fixture import fixture,seed,NOW
from src.futures.cache import PreparationCache,fingerprint
from src.futures.runtime import ScanRuntime
from src.futures.preparation import PreparedScanner,bounded_map
from src.futures.live_news import fresh_result,article_fingerprint
from src.futures.costs import trade_plan,FuturesCosts
from src.futures.config import FuturesScanConfig


def test_live_uses_prepared_research_no_backtests_and_batched_quotes(tmp_path):
    scanner,provider=fixture()
    runtime,cache=seed(scanner,tmp_path)
    with patch('src.futures.backtest.FuturesIntradayBacktester.run',side_effect=AssertionError('No live backtests')):
        result=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
    assert result['long_discovery_count']==result['short_discovery_count']==4
    assert len(result['reviewed'])==8
    scanner.company_research.engine.fundamental_provider.get_fundamentals.assert_not_called()
    scanner.news_provider.assert_not_called()
    provider.get_futures_execution_data.assert_not_called()
    assert provider.provider.kite.quote.call_count==1
    assert not provider.provider.kite.historical_data.called
    assert not provider.provider.kite.place_order.called
    assert result['scan_mode']=='LIVE_SCAN'
    assert result['timings']['total_seconds']>0
    assert all('company_research' in item and 'data_freshness' in item for item in result['reviewed'])


def test_reference_and_prepared_outputs_match_same_snapshot(tmp_path):
    scanner,provider=fixture()
    reference=scanner.scan(now=NOW,include_backtest=False)
    runtime,cache=seed(scanner,tmp_path)
    prepared=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
    for key in ['report_a','report_b','report_c']:
        assert [(i['symbol'],i['side'],i['setup_type'],i['technical_score'],i['final_decision'])for i in reference[key]] == [(i['symbol'],i['side'],i['setup_type'],i['technical_score'],i['final_decision'])for i in prepared[key]]
    for before,after in zip(reference['reviewed'],prepared['reviewed']):
        assert before['company_research']['checks']==after['company_research']['checks']
        assert before.get('failed_execution_checks')==after.get('failed_execution_checks')


def test_missing_cache_does_not_trigger_full_downloads_or_approve(tmp_path):
    scanner,provider=fixture()
    runtime=ScanRuntime(cache_directory=str(tmp_path),require_margin=False)
    with patch('src.futures.preparation.collect',return_value=[]),patch('src.futures.backtest.FuturesIntradayBacktester.run',side_effect=AssertionError):
        result=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
    assert len(result['reviewed'])==8
    assert result['approved_count']==0
    assert not provider.provider.kite.historical_data.called
    assert scanner.company_research.engine.fundamental_provider.get_fundamentals.call_count==0
    assert all(item['company_research']['status']=='UNKNOWN' for item in result['reviewed'])


def test_cache_expiry_version_and_fingerprint(tmp_path):
    cache=PreparationCache(tmp_path)
    cache.put('company','A',{'value':1},now=NOW,ttl=60,input_fingerprint='old')
    assert cache.get('company','A',now=NOW,expected_fingerprint='new') is None
    assert cache.get('company','A',now=NOW+pd.Timedelta(seconds=60)) is None
    assert cache.get('company','A',now=NOW-pd.Timedelta(seconds=1)) is None
    with cache.connect() as db:db.execute("UPDATE records SET schema_version=99")
    assert cache.get('company','A',now=NOW) is None


def test_incremental_candle_request_overlaps_last_day_not_full_window(tmp_path):
    scanner,provider=fixture(['SBIN'])
    runtime,cache=seed(scanner,tmp_path,stale_sessions=True)
    result=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
    calls=provider.provider.kite.historical_data.call_args_list
    assert calls
    for call in calls:
        assert (NOW-pd.Timestamp(call.args[1])).total_seconds()<3*86400
    assert result['request_counts']['history']>0
    assert result['approved_count']==0 or all(i['execution_reviewed'] for i in result['report_c'])


def test_new_headlines_unknown_until_semantic_analysis_not_waived():
    article={'title':'SBIN earnings','published':NOW.isoformat(),'url':'https://example.com/news'}
    result=fresh_result([article],None,NOW.to_pydatetime())
    assert result['news_state']=='UNANALYSED_NEW_HEADLINES'
    cached={**result,'news_state':'ANALYZED','articles_fingerprint':article_fingerprint([article]),'sentiment':'BEARISH'}
    assert fresh_result([article],cached,NOW.to_pydatetime())['sentiment']=='BEARISH'
    assert fresh_result([{**article,'description':'Changed guidance'}],cached,NOW.to_pydatetime())['news_state']=='UNANALYSED_NEW_HEADLINES'


def test_underlying_movement_keeps_execution_prices_and_costs():
    plan=trade_plan(205,100,'SHORT',config=FuturesScanConfig(),costs=FuturesCosts(),risk_budget=1000,
        trade_date='2026-10-08',movement_reference_price=200,margin_per_lot=10000,available_capital=100000)
    assert plan['target']==pytest.approx(204.385)
    assert plan['stop_loss']==pytest.approx(205.41)
    assert plan['underlying_target'] is None
    assert plan['underlying_stop_loss'] is None
    assert plan['movement_basis']=='FUTURES'
    assert plan['net_profit_at_target']<plan['gross_profit_at_target']


def test_unavailable_source_is_bounded_and_other_results_survive():
    def work(key):
        if key=='bad':raise TimeoutError('Unavailable')
        return key
    result,errors=bounded_map(work,['good','bad'],2,.5)
    assert result=={'good':'good'}
    assert errors['bad']['type']=='TimeoutError'
    assert errors['bad']['detail']=='Unavailable'


def test_backtest_cache_rejects_changed_policy_and_expiry(tmp_path):
    scanner,_=fixture(['SBIN']);runtime,cache=seed(scanner,tmp_path)
    manager=PreparedScanner(scanner,runtime);manager.now=NOW;manager.clock=lambda:NOW
    contract={'tradingsymbol':'SBIN26OCTFUT'}
    assert 'groups' in manager.cached_backtest(contract)
    manager.scanner.config=replace(manager.scanner.config,target_fraction=.004)
    assert manager.cached_backtest(contract)['status']=='UNKNOWN'
    assert manager.cached_backtest({'tradingsymbol':'SBIN26NOVFUT'})['status']=='UNKNOWN'


def test_preparation_recomputes_backtest_only_for_changed_inputs(tmp_path):
    from src.futures.gateway import KiteGateway
    scanner,provider=fixture(['SBIN']);runtime,cache=seed(scanner,tmp_path)
    manager=PreparedScanner(scanner,runtime);manager.now=NOW;manager.clock=lambda:NOW
    manager.symbols=['SBIN'];manager.instruments=provider.master
    manager.master={row['tradingsymbol']:row for row in provider.master}
    manager.contracts={'SBIN':{key:provider.master[0][key] for key in ('tradingsymbol','expiry','lot_size')}}
    manager.gateway=KiteGateway(provider,runtime)
    result={'movement_basis':'FUTURES','groups':{},'validation_status':'INSUFFICIENT'}
    with patch('src.futures.preparation.FuturesIntradayBacktester.run',return_value=result) as run, patch('src.futures.report_e_history.ReportEBacktester.run',return_value=result):
        manager.prepare_history('FULL_RESEARCH')
        assert run.call_count==1
        manager.prepare_history('DAILY_PREP')
        assert run.call_count==1
        changed=cache.frame('NFO:SBIN26OCTFUT:5minute',now=NOW)
        changed.iloc[-1,changed.columns.get_loc('Close')]+=.01
        cache.put_frame('NFO:SBIN26OCTFUT:5minute',changed,now=NOW,ttl=604800)
        manager.prepare_history('DAILY_PREP')
        assert run.call_count==2


def test_live_margin_calculation_is_read_only_and_missing_margin_blocks(tmp_path):
    scanner,provider=fixture(['SBIN']);runtime,cache=seed(scanner,tmp_path)
    runtime=replace(runtime,require_margin=True)
    provider.provider.kite.order_margins.side_effect=TimeoutError('Margin unavailable')
    result=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
    assert provider.provider.kite.order_margins.called
    assert not provider.provider.kite.place_order.called
    assert result['approved_count']==0
    assert any('FUTURES_MARGIN_OR_AVAILABLE_FUNDS_UNVERIFIED' in i.get('reason_codes',[]) for i in result['reviewed'] if i['execution_reviewed'])


def test_underlying_candles_cannot_drive_futures_barriers_or_outcomes():
    from src.futures.backtest import FuturesIntradayBacktester
    scanner,provider=fixture(['SBIN'])
    future=provider.intraday.iloc[-75:].copy()
    spot=future.copy();spot[['Open','High','Low','Close']]=100.
    spot.iloc[1,spot.columns.get_loc('High')]=101
    spot.iloc[1,spot.columns.get_loc('Low')]=99
    engine=FuturesIntradayBacktester()
    result=engine.simulate(future,1,'LONG','TEST',100,underlying=spot)
    assert result==engine.simulate(future,1,'LONG','TEST',100)
    assert result['target']==pytest.approx(result['entry']*1.003)
    assert result['stop_loss']==pytest.approx(result['entry']*.998)
    assert result['movement_basis']=='FUTURES'
    # Ambiguity still uses stop-first, but it must occur in the FUTURES candle.
    future.iloc[1,future.columns.get_loc('High')]=result['target']+1
    future.iloc[1,future.columns.get_loc('Low')]=result['stop_loss']-1
    assert engine.simulate(future,1,'LONG','TEST',100,underlying=spot)['outcome']=='STOP_FIRST'
