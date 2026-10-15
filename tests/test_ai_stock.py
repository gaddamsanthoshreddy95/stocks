from types import SimpleNamespace
from unittest.mock import Mock, patch
import sqlite3
import pytest

from src.ui.ai_stock import AI_STOCKS,AiStockHistory,ThemePlatform,scan_ai_stock
from src.ui.database import ReportDatabase


def snapshot():
    return {'generated_at':'2026-10-15T18:00:00+05:30','scan_mode':'AFTER_MARKET_RESEARCH',
        'universe_size':12,'approved_count':0,'report_a':[],'report_b':[],'report_c':[],
        'reviewed':[{'symbol':symbol,'side':side,'technical_score':80,'final_decision':'UNKNOWN',
                     'reason_codes':['TEST_ONLY']} for symbol in AI_STOCKS for side in ('LONG','SHORT')]}


def test_exact_user_universe_never_calls_general_universe():
    broad=Mock(side_effect=AssertionError('Must not discover general universe'))
    platform=ThemePlatform(SimpleNamespace(provider=object(),_universe_symbols=broad))
    assert set(platform._universe_symbols())=={'CGPOWER','ABB','POWERINDIA','CUMMINSIND','BLUESTARCO','VOLTAS','AMBER','POLYCAB','KEI','SIEMENS','TATACOMM','KAYNES'}
    assert len(platform._universe_symbols())==12
    broad.assert_not_called()


def test_manual_scan_reuses_scanner_but_isolates_cache_history_and_eligibility(tmp_path):
    db=ReportDatabase(tmp_path/'ui.db')
    master=[{'name':'ABB','tradingsymbol':'ABB26OCTFUT','instrument_type':'FUT','segment':'NFO-FUT',
        'expiry':'2026-10-27','lot_size':100,'instrument_token':1}]
    provider=SimpleNamespace(get_nfo_instruments=Mock(return_value=master))
    platform=SimpleNamespace(provider=provider,_serialize=lambda value:value)
    captured={}
    def factory(scoped,config):
        captured.update(scoped=scoped,config=config)
        def scan(limit,mode,runtime):
            assert set(scoped._universe_symbols())==set(AI_STOCKS)
            scoped.provider.get_nfo_instruments()
            captured.update(runtime=runtime,limit=limit,mode=mode)
            return snapshot()
        return SimpleNamespace(scan=scan)
    with patch('src.ui.ai_stock.TradingPlatform',return_value=platform),patch('src.ui.ai_stock.FuturesOpportunityScanner',side_effect=factory),patch('src.ui.ai_stock.fetch_missing_fundamentals',side_effect=lambda r,**kw:r):
        report=scan_ai_stock(SimpleNamespace(market_data_source='cache'),db,5,'AFTER_MARKET_RESEARCH','ai-test')
    assert captured['runtime'].cache_directory.endswith('ai_stock_prepared')
    assert captured['runtime'].ledger_path=='data/futures_executions.sqlite3'
    assert captured['config'].maximum_daily_entries==2
    assert (captured['config'].entry_cutoff_hour,captured['config'].entry_cutoff_minute)==(15,0)
    assert (captured['config'].intraday_exit_hour,captured['config'].intraday_exit_minute)==(15,10)
    assert {r['symbol']:r['futures_status'] for r in report['universe']}['ABB']=='ACTIVE_CONTRACT_VERIFIED'
    assert {r['symbol']:r['futures_status'] for r in report['universe']}['KEI']=='NO_ACTIVE_CONTRACT'
    assert not db.list_futures_scans()
    archive=AiStockHistory(db)
    assert len(archive.list())==1
    assert len(archive.get(report['_ai_saved_scan_id'])['reviewed'])==24


def test_history_is_separate_and_immutable(tmp_path):
    db=ReportDatabase(tmp_path/'ui.db')
    archive=AiStockHistory(db)
    report=snapshot();scan_id=archive.save(report,'one')
    report['reviewed'][0]['technical_score']=1
    assert AiStockHistory(ReportDatabase(db.path)).get(scan_id)['reviewed'][0]['technical_score']==80
    with pytest.raises(sqlite3.IntegrityError):archive.save(report,'one')
    assert not db.list_futures_scans()


def test_failed_worker_does_not_save_fake_success(tmp_path):
    db=ReportDatabase(tmp_path/'ui.db')
    platform=SimpleNamespace(provider=object())
    with patch('src.ui.ai_stock.TradingPlatform',return_value=platform),patch('src.ui.ai_stock.FuturesOpportunityScanner',side_effect=RuntimeError('source failed')):
        with pytest.raises(RuntimeError,match='source failed'):
            scan_ai_stock(SimpleNamespace(),db,5,'LIVE_SCAN','failed')
    assert AiStockHistory(db).list()==[]


def test_real_shared_pipeline_evaluates_only_the_twelve_symbols(tmp_path):
    from futures_mode_fixture import fixture,seed,NOW
    scanner,_=fixture(list(AI_STOCKS))
    scanner.platform=ThemePlatform(scanner.platform)
    scanner.provider=scanner.platform.provider
    runtime,_=seed(scanner,tmp_path)
    report=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
    assert report['universe_size']==12
    assert len(report['reviewed'])==24
    assert {i['symbol'] for i in report['reviewed']}==set(AI_STOCKS)
    assert all('company_research' in i and 'evidence' in i for i in report['reviewed'])


def test_failed_master_cached_universe_cannot_expand_restricted_scope(tmp_path):
    from futures_mode_fixture import fixture,seed,NOW
    scanner,provider=fixture(['ABB','OUTSIDE'])
    runtime,cache=seed(scanner,tmp_path)
    cache.put('universe','current',{'symbols':['ABB','OUTSIDE'],'instruments':provider.master},now=NOW,ttl=3600)
    scanner.platform.restricted_symbols=('ABB',)
    provider.get_nfo_instruments=Mock(side_effect=RuntimeError('master offline'))
    report=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
    assert report['universe_size']==1
    assert {i['symbol'] for i in report['reviewed']}=={'ABB'}
    assert report['approved_count']==0


def test_opening_ai_stock_page_never_starts_scan(tmp_path):
    from streamlit.testing.v1 import AppTest
    path=tmp_path/'ui.db'
    code=f'''from types import SimpleNamespace
from src.ui.ai_stock import render
from src.ui.database import ReportDatabase
def forbidden(*a,**kw):raise AssertionError('No automatic scan allowed')
render(SimpleNamespace(settings=SimpleNamespace()),ReportDatabase({str(path)!r}),forbidden,lambda _:False)
'''
    with patch('src.ui.ai_stock.TradingPlatform',side_effect=AssertionError('No provider on page load')):
        app=AppTest.from_string(code).run(timeout=15)
        assert not app.exception
        assert len(app.dataframe[0].value)==12
        assert any(b.label=='Scan AI-Stock' for b in app.button)
        app.selectbox[0].set_value('LIVE_SCAN').run(timeout=15)
        assert not app.exception
    assert not AiStockHistory(ReportDatabase(path)).list()


def test_saved_page_shows_shared_technical_fundamental_and_oi_columns(tmp_path):
    from streamlit.testing.v1 import AppTest
    path=tmp_path/'ui.db';db=ReportDatabase(path)
    report=snapshot()
    report['reviewed'][0].update(daily_ranking_score=82,oi_ranking={'status':'AVAILABLE','score':100,'oi_change_percent':5},
        company_research={'fundamentals':{'pe_ratio':20,'sector_pe':22,'roe':18,'roce':25}})
    AiStockHistory(db).save(report,'saved')
    code=f'''from types import SimpleNamespace
from src.ui.ai_stock import render
from src.ui.database import ReportDatabase
def forbidden(*a,**kw):raise AssertionError('No automatic scan allowed')
render(SimpleNamespace(settings=SimpleNamespace()),ReportDatabase({str(path)!r}),forbidden,lambda _:False)
'''
    app=AppTest.from_string(code).run(timeout=15)
    assert not app.exception
    row=app.dataframe[1].value.iloc[0]
    assert row['Daily score']==80 and row['Daily ranking score']==82 and row['OI score']==100
    assert row['P/E']==20 and row['Sector P/E']==22
    assert row['AI infrastructure connection']==AI_STOCKS['CGPOWER']
