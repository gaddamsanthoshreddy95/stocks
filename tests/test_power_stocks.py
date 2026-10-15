from types import SimpleNamespace
from unittest.mock import patch

from src.ui.ai_stock import AiStockHistory,ThemePlatform
from src.ui.power_stocks import POWER_STOCKS,PowerStockHistory,scan_power_stocks
from src.ui.database import ReportDatabase


def snapshot():
    return {'generated_at':'2026-10-15T18:00:00+05:30','scan_mode':'AFTER_MARKET_RESEARCH',
        'universe_size':8,'approved_count':0,'report_a':[],'report_b':[],'report_c':[],
        'reviewed':[{'symbol':symbol,'side':side,'technical_score':80,'final_decision':'UNKNOWN','reason_codes':[]}
                    for symbol in POWER_STOCKS for side in ('LONG','SHORT')]}


def test_exact_power_universe():
    assert list(POWER_STOCKS)==['NTPC','POWERGRID','TATAPOWER','JSWENERGY','BHEL','CGPOWER','PFC','RECLTD']
    scoped=ThemePlatform(SimpleNamespace(provider=object()),POWER_STOCKS)
    assert set(scoped._universe_symbols())==set(scoped.restricted_symbols)==set(POWER_STOCKS)


def test_power_scan_isolated_from_ai_and_general_archives(tmp_path):
    database=ReportDatabase(tmp_path/'ui.db')
    platform=SimpleNamespace(provider=SimpleNamespace(get_nfo_instruments=lambda:[]),_serialize=lambda r:r)
    captured={}
    def factory(scoped,config):
        assert set(scoped._universe_symbols())==set(POWER_STOCKS)
        def scan(limit,mode,runtime):
            scoped.provider.get_nfo_instruments()
            captured.update(config=config,runtime=runtime)
            return snapshot()
        return SimpleNamespace(scan=scan)
    with patch('src.ui.ai_stock.TradingPlatform',return_value=platform),patch('src.ui.ai_stock.FuturesOpportunityScanner',side_effect=factory),patch('src.ui.ai_stock.fetch_missing_fundamentals',side_effect=lambda r,**kw:r):
        report=scan_power_stocks(SimpleNamespace(),database,5,'AFTER_MARKET_RESEARCH','same-id')
    assert report['feature']=='Power Stocks'
    assert len(report['reviewed'])==16
    assert captured['runtime'].cache_directory.endswith('power_stocks_prepared')
    assert captured['runtime'].ledger_path=='data/futures_executions.sqlite3'
    assert captured['config'].maximum_daily_entries==2
    assert not AiStockHistory(database).list() and not database.list_futures_scans()
    assert len(PowerStockHistory(database).list())==1
    AiStockHistory(database).save(snapshot(),'same-id')
    assert len(PowerStockHistory(database).list())==1
    assert PowerStockHistory(database).get(report['_power_saved_scan_id'])['feature']=='Power Stocks'


def test_power_page_open_and_mode_changes_start_no_scan(tmp_path):
    from streamlit.testing.v1 import AppTest
    path=tmp_path/'ui.db'
    code=f'''import streamlit as st
from types import SimpleNamespace
from src.ui.power_stocks import render
from src.ui.database import ReportDatabase
def submit(feature,worker):st.session_state['submitted']=feature
render(SimpleNamespace(settings=SimpleNamespace()),ReportDatabase({str(path)!r}),submit,lambda _:False)
'''
    with patch('src.ui.ai_stock.TradingPlatform',side_effect=AssertionError('No automatic data requests')):
        app=AppTest.from_string(code).run(timeout=15)
        assert not app.exception
        assert len(app.dataframe[0].value)==8
        assert app.header[0].value=='Power Stocks'
        app.selectbox[0].set_value('LIVE_SCAN').run(timeout=15)
        assert not app.exception
        next(b for b in app.button if b.label=='Scan Power Stocks').click().run(timeout=15)
        assert not app.exception and app.session_state['submitted']=='power_stocks'
    assert not PowerStockHistory(ReportDatabase(path)).list()


def test_power_saved_results_have_same_analysis_and_separate_preferences(tmp_path):
    from streamlit.testing.v1 import AppTest
    path=tmp_path/'ui.db';database=ReportDatabase(path)
    report=snapshot()
    report['reviewed'][0].update(daily_ranking_score=82,oi_ranking={'status':'AVAILABLE','score':100},
        company_research={'fundamentals':{'pe_ratio':20,'sector_pe':22,'roe':18,'roce':25,'delivery_percent':45}})
    PowerStockHistory(database).save(report,'saved')
    code=f'''from types import SimpleNamespace
from src.ui.power_stocks import render
from src.ui.database import ReportDatabase
def forbidden(*a):raise AssertionError('No automatic scan')
render(SimpleNamespace(settings=SimpleNamespace()),ReportDatabase({str(path)!r}),forbidden,lambda _:False)
'''
    app=AppTest.from_string(code).run(timeout=15)
    assert not app.exception
    row=app.dataframe[1].value.iloc[0]
    assert row['Symbol']=='NTPC' and row['Power sector connection']==POWER_STOCKS['NTPC']
    assert row['Daily ranking score']==82 and row['OI score']==100
    assert row['P/E']==20 and row['Sector P/E']==22 and row['Delivery %']==45
    assert not AiStockHistory(database).list()
