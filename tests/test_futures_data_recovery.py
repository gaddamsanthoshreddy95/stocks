"""Recovery tests use real serialized caches and deterministic feeds, never networks."""
import copy
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pandas as pd
from futures_mode_fixture import fixture, seed, NOW
from src.data_provider.kite_data_provider import KiteDataProvider
from src.futures.preparation import PreparedScanner, PreparedDataProvider, CachedCompanies
from src.futures.runtime import ScanRuntime
from src.futures.cache import PreparationCache
from src.futures.data_recovery import recover_existing_data, completed_frame, completed_session_cutoff, source_failure
from src.futures.live_news import article_fingerprint, fresh_result
from src.quality.public_fundamentals import PublicFundamentalProvider
from src.presenter.futures_opportunities import FuturesOpportunitiesPresenter
from src.futures.rejected_analysis import TITLE, append_report_d

AFTER = NOW.normalize()+pd.Timedelta(hours=22,minutes=20)


def legacy_fixture(tmp_path):
    scanner, source = fixture(['FEDERALBNK','HDFCLIFE'])
    provider = KiteDataProvider(provider=source.provider,
        history_cache_directory=tmp_path/'history',long_history_cache_directory=tmp_path/'annual',
        intraday_cache_directory=tmp_path/'intraday',instrument_cache_directory=tmp_path/'instruments')
    provider.get_nfo_instruments = Mock(return_value=source.master)
    scanner.provider = provider
    runtime = ScanRuntime(cache_directory=str(tmp_path/'prepared'),legacy_reports_directory=str(tmp_path/'reports'),require_margin=False,movement_basis='FUTURES')
    manager = PreparedScanner(scanner,runtime)
    manager.now=AFTER;manager.clock=lambda:AFTER;manager.mode='AFTER_MARKET_RESEARCH';manager.data_as_of=completed_session_cutoff(AFTER)
    manager.symbols=source.symbols;manager.contracts={s:source.master[n] for n,s in enumerate(source.symbols)};manager.master={c['tradingsymbol']:c for c in source.master}
    manager.gateway=Mock();manager.instruments=source.master
    provider._history_cache_directory.mkdir();provider._long_history_cache_directory.mkdir()
    for symbol in source.symbols:
        source.daily.to_parquet(provider._history_path(symbol))
        source.daily.to_parquet(provider._long_history_path(symbol,'2y'))
    prior={'generated_at':(AFTER-pd.Timedelta(hours=2)).isoformat(),'reviewed':[]}
    for symbol in source.symbols:
        facts=replace(scanner.company_research.engine.fundamental_provider.get_fundamentals(symbol),as_of=AFTER.date().isoformat())
        prior['reviewed'].append({'symbol':symbol,'side':'LONG','technical_score':99,'company_research':{'fundamentals':asdict(facts)},
                                 'news':{'news_state':'ANALYZED','checked_at':(AFTER-pd.Timedelta(hours=2)).isoformat(),'headlines':[]}})
    folder=tmp_path/'reports';folder.mkdir();(folder/'futures_success.json').write_text(json.dumps(prior))
    return manager,source


def test_original_caches_recovered_idempotently_without_requests(tmp_path):
    manager,source=legacy_fixture(tmp_path)
    imported=recover_existing_data(manager)
    assert imported=={'candles_imported':4,'companies_imported':2,'news_imported':2}
    assert recover_existing_data(manager)=={'candles_imported':0,'companies_imported':0,'news_imported':0}
    assert not source.provider.kite.historical_data.called
    facts=CachedCompanies(manager).get_fundamentals('FEDERALBNK')
    assert facts.roe==20 and facts.pe_ratio==25 and facts.delivery_percent==50
    record=manager.cache.get('company','FEDERALBNK',now=AFTER)
    assert record['saved_at']==(AFTER-pd.Timedelta(hours=2)).isoformat()
    assert manager.cache.get('news','FEDERALBNK',now=AFTER) is None  # expired stays expired
    assert manager.cache.get('news','FEDERALBNK',now=AFTER,allow_expired=True)['payload']['news_state']=='ANALYZED'
    # Old report's 99 score is not imported as a signal.
    assert manager.cache.get('scores','FEDERALBNK',now=AFTER) is None


def test_expired_company_report_is_not_resurrected(tmp_path):
    manager,source=legacy_fixture(tmp_path)
    path=tmp_path/'reports'/'futures_success.json'; r=json.loads(path.read_text())
    r['generated_at']=(AFTER-pd.Timedelta(days=8)).isoformat();path.write_text(json.dumps(r))
    recover_existing_data(manager)
    assert CachedCompanies(manager).get_fundamentals('FEDERALBNK') is None


def test_cache_location_independent_of_cli_working_directory(tmp_path,monkeypatch):
    name='.cache/test_path_resolution_'+tmp_path.name
    first=PreparationCache(name)
    monkeypatch.chdir(tmp_path)
    second=PreparationCache(name)
    assert first.path==second.path and first.path.is_absolute()
    first.path.unlink()
    first.path.parent.rmdir()


def test_completed_session_ignores_partial_live_candle(tmp_path):
    scanner,source=fixture()
    frame=source.daily.copy()
    frame.loc[NOW.normalize(),:]=frame.iloc[-1]
    frame.loc[NOW.normalize(),'IS_LIVE_CANDLE']=True
    assert completed_frame(frame,NOW,'day').index[-1].date()<NOW.date()
    completed=completed_frame(frame,AFTER,'day')
    assert completed.index[-1].date()==AFTER.date()
    assert 'IS_LIVE_CANDLE' not in completed
    saturday=pd.Timestamp('2026-10-10 22:00',tz='Asia/Kolkata')
    assert completed_session_cutoff(saturday)==pd.Timestamp('2026-10-09 15:30',tz='Asia/Kolkata')


def test_after_market_does_not_inject_quote_as_daily_candle(tmp_path):
    manager,source=legacy_fixture(tmp_path);recover_existing_data(manager)
    provider=PreparedDataProvider(manager)
    provider.quotes={'NSE:FEDERALBNK':{'last_price':999,'ohlc':{'open':999,'high':999,'low':999},'volume':0}}
    data=provider.get_data('FEDERALBNK')
    assert data.Close.iloc[-1] == source.daily.Close.iloc[-1]
    assert 'IS_LIVE_CANDLE' not in data


def test_missing_only_history_fetch_and_incremental_overlap(tmp_path):
    manager,source=legacy_fixture(tmp_path)
    rows=source.provider.kite._history(1,NOW-pd.Timedelta(days=45),AFTER,'5minute')
    manager.gateway.history.return_value=rows
    identity='NSE:FEDERALBNK:5minute'
    first=manager.update_candles(identity,live=True)
    assert first is not None and first.index[-1].hour==15
    manager.gateway.history.reset_mock()
    assert manager.update_candles(identity,live=True).equals(first)
    manager.gateway.history.assert_not_called()
    stale=first.loc[first.index.date<AFTER.date()]
    manager.cache.put_frame(identity,stale,now=AFTER-pd.Timedelta(days=1),ttl=604800)
    manager.update_candles(identity,live=True)
    args=manager.gateway.history.call_args.args
    assert pd.Timestamp(args[1])==stale.index[-1]-pd.Timedelta(days=1)
    assert pd.Timestamp(args[2])==completed_session_cutoff(AFTER)


def test_failed_refresh_preserves_old_timestamp_and_error_detail(tmp_path):
    manager,source=legacy_fixture(tmp_path)
    old=source.intraday.loc[source.intraday.index.date<AFTER.date()]
    identity='NSE:FEDERALBNK:5minute'
    manager.cache.put_frame(identity,old,now=AFTER-pd.Timedelta(days=1),ttl=604800)
    record=manager.cache.get('candles',identity,now=AFTER)
    manager.gateway.history.side_effect=ConnectionError('Failed to resolve api.kite.trade')
    assert manager.update_candles(identity,live=True).equals(old)
    assert manager.cache.get('candles',identity,now=AFTER)['saved_at']==record['saved_at']
    assert manager.errors[identity]['category']=='DNS_RESOLUTION_FAILED'


def test_after_market_real_scoring_research_and_report_order(tmp_path):
    scanner,source=fixture()
    runtime,cache=seed(scanner,tmp_path)
    with patch('src.futures.preparation.collect',return_value=[]),patch('src.futures.backtest.FuturesIntradayBacktester.run',side_effect=AssertionError('No duplicate backtest')):
        r=scanner.scan(mode='AFTER_MARKET_RESEARCH',runtime=runtime,now=AFTER)
    assert r['research_only'] and r['approved_count']==0 and not r['report_c']
    for key in ('report_a','report_b'):
        assert r[key] and all(i['technical_score'] is not None for i in r[key])
        assert [i['technical_score'] for i in r[key]]==sorted([i['technical_score'] for i in r[key]],reverse=True)
        assert all(i['company_research']['fundamentals']['roe']==20 for i in r[key])
        assert all(i['final_decision']!='APPROVED' and i['research_only'] for i in r[key])
    assert r['completed_session_as_of']==completed_session_cutoff(AFTER).isoformat()
    text=FuturesOpportunitiesPresenter.render(r)
    assert text.index('## Report A')<text.index('## Report B')<text.index('## Report C')<text.index('## '+TITLE)
    calls=source.provider.kite.historical_data.call_count
    append_report_d(r)
    assert source.provider.kite.historical_data.call_count==calls


def test_missing_data_never_populates_ranked_lists(tmp_path):
    scanner,source=fixture()
    runtime=ScanRuntime(cache_directory=str(tmp_path),require_margin=False)
    with patch('src.futures.preparation.collect',side_effect=ConnectionError('Failed to resolve news.google.com')):
        r=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
    assert not r['report_a'] and not r['report_b'] and not r['report_c']
    assert len(r['reviewed'])==8
    assert r['source_failures']['news:SBIN']['category']=='DNS_RESOLUTION_FAILED'
    assert 'missing_fields' in r['source_failures']['NSE:SBIN:day']


def test_legacy_news_analysis_reused_only_after_verified_unchanged_collection():
    article={'title':'Bank earnings','description':'Revenue grew','source':'Company','url':'https://example.test','published':'2026-10-08T09:00:00+00:00'}
    changed_zone={**article,'published':'2026-10-08T14:30:00+05:30'}
    old={'news_state':'ANALYZED','headlines':[article],'checked_at':NOW.isoformat(),'sentiment':'BULLISH'}
    news=fresh_result([changed_zone],old,AFTER.to_pydatetime())
    assert news['analysis_reused'] and news['analysis_original_checked_at']==NOW.isoformat()
    assert article_fingerprint([changed_zone])==article_fingerprint([article])
    assert fresh_result([{**article,'description':'Profit warning'}],old,AFTER.to_pydatetime())['news_state']=='UNANALYSED_NEW_HEADLINES'


def test_transport_diagnostics_redact_credentials():
    data=source_failure(ConnectionError('Failed to resolve api.kite.trade?api_key=abc&access_token=xyz'))
    assert 'abc' not in data['detail'] and 'xyz' not in data['detail']
    assert data['category']=='DNS_RESOLUTION_FAILED'


def test_parsed_company_cache_is_read_without_external_request(tmp_path):
    scanner,source=fixture(['SBIN'])
    facts=asdict(scanner.company_research.engine.fundamental_provider.get_fundamentals('SBIN'))
    provider=PublicFundamentalProvider(cache_directory=tmp_path)
    stamp=pd.Timestamp.now(tz='Asia/Kolkata').isoformat()
    (tmp_path/'snapshot_SBIN.json').write_text(json.dumps({'saved_at':stamp,'payload':facts}))
    with patch.object(provider,'_fetch',side_effect=AssertionError('Network forbidden')):
        assert provider.get_fundamentals('SBIN').roe==20
    (tmp_path/'snapshot_SBIN.json').write_text(json.dumps({'saved_at':(AFTER-pd.Timedelta(days=8)).isoformat(),'payload':facts}))
    assert provider.read_cached_snapshot('SBIN',max_age_seconds=7*86400) is None


def test_news_semantic_analysis_reuses_collected_articles_without_second_fetch():
    from datetime import datetime, timezone
    from src.news.analysis_service import NewsAnalysisService
    article={'title':'SBIN reports quarterly results','description':'Revenue increased','source':'Company','url':'https://example.test',
             'published':datetime.now(timezone.utc).isoformat()}
    model=Mock(model='test-model')
    model.analyze.return_value={'score':50,'sentiment':'BULLISH','confidence':80,'events':[], 'materiality':'LOW',
                               'trade_impact':'SUPPORTIVE','reasoning':[],'article_assessments':[]}
    with patch('src.news.analysis_service.requests.get',side_effect=AssertionError('Duplicate news request')):
        result=NewsAnalysisService.analyze('SBIN',analyzer=model,collected_articles=[article],company_aliases={'SBIN':{'SBIN'}})
    assert result['news_state']=='ANALYZED' and result['article_count']==1
    assert model.analyze.call_args.args[1][0]['title']==article['title']


def test_truncated_legacy_news_is_not_assumed_fully_analysed():
    article={'title':'Bank earnings','description':'Revenue','source':'Company','url':'https://example.test','published':NOW.isoformat()}
    cached={'news_state':'ANALYZED','headlines':[article],'article_count':3,'checked_at':NOW.isoformat()}
    assert fresh_result([article],cached,AFTER.to_pydatetime())['news_state']=='UNANALYSED_NEW_HEADLINES'


def test_research_mode_cannot_publish_even_a_forced_live_approval(tmp_path):
    scanner,source=fixture(['SBIN'])
    runtime,cache=seed(scanner,tmp_path)
    candidate_report=scanner.scan(now=NOW,include_backtest=False)
    item=candidate_report['reviewed'][0]
    item['final_decision']='APPROVED'
    item['short_entry']={'execution_approved':True}
    candidate_report['report_c']=[item];candidate_report['approved_count']=1
    runner=Mock();runner.scan.return_value=candidate_report
    manager=PreparedScanner(scanner,runtime)
    with patch('src.futures.scanner.FuturesOpportunityScanner',return_value=runner),patch('src.futures.preparation.collect',return_value=[]):
        result=manager.run('AFTER_MARKET_RESEARCH',5,now=NOW)
    assert result['report_c']==[] and result['approved_count']==0
    assert item['final_decision']=='WAIT' and item['short_entry']['execution_approved'] is False
    assert all(i['research_only'] for i in result['reviewed'])


def test_preparation_finishes_cached_pending_news_without_recollecting(tmp_path):
    scanner,source=fixture(['SBIN'])
    runtime,cache=seed(scanner,tmp_path)
    manager=PreparedScanner(scanner,runtime)
    manager.now=NOW;manager.clock=lambda:NOW;manager.symbols=['SBIN'];manager.aliases={'SBIN':{'SBIN'}}
    article={'title':'SBIN results','published':NOW.isoformat()}
    pending={'news_state':'UNANALYSED_NEW_HEADLINES','headlines':[article],'articles_fingerprint':'changed'}
    cache.put('news','SBIN',pending,now=NOW,ttl=900)
    cache.put('pending_news','SBIN',pending,now=NOW,ttl=86400)
    analysed={'news_state':'ANALYZED','headlines':[article],'events':[], 'articles_fingerprint':'changed','checked_at':NOW.isoformat()}
    with patch('src.futures.preparation.NewsAnalysisService.analyze',return_value=analysed) as analyse:
        manager.prepare_research('DAILY_PREP')
    assert analyse.call_args.kwargs['collected_articles']==[article]
    assert cache.get('news','SBIN',now=NOW)['payload']['news_state']=='ANALYZED'
    assert cache.get('pending_news','SBIN',now=NOW) is None
