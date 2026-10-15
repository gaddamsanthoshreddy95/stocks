from types import SimpleNamespace
from unittest.mock import patch

from src.ui.ai_stock import AiStockHistory,ThemePlatform
from src.ui.power_stocks import PowerStockHistory
from src.ui.reality_stocks import REALITY_STOCKS,RealityStockHistory,scan_reality_stocks
from src.ui.database import ReportDatabase


def snapshot():
    return {'generated_at':'2026-10-15T18:00:00+05:30','scan_mode':'AFTER_MARKET_RESEARCH',
        'universe_size':6,'approved_count':0,'report_a':[],'report_b':[],'report_c':[],
        'reviewed':[{'symbol':symbol,'side':side,'technical_score':80,'final_decision':'UNKNOWN','reason_codes':[]}
                    for symbol in REALITY_STOCKS for side in ('LONG','SHORT')]}


def test_exact_six_symbol_universe():
    assert list(REALITY_STOCKS)==['DLF','GODREJPROP','LODHA','OBEROIRLTY','PRESTIGE','PHOENIXLTD']
    scoped=ThemePlatform(SimpleNamespace(provider=object()),REALITY_STOCKS)
    assert set(scoped._universe_symbols())==set(scoped.restricted_symbols)==set(REALITY_STOCKS)


def test_reality_worker_uses_shared_analysis_and_separate_storage(tmp_path):
    db=ReportDatabase(tmp_path/'ui.db')
    platform=SimpleNamespace(provider=SimpleNamespace(get_nfo_instruments=lambda:[]),_serialize=lambda r:r)
    captured={}
    def factory(scoped,config):
        assert set(scoped._universe_symbols())==set(REALITY_STOCKS)
        def scan(limit,mode,runtime):
            scoped.provider.get_nfo_instruments()
            captured.update(config=config,runtime=runtime)
            return snapshot()
        return SimpleNamespace(scan=scan)
    with patch('src.ui.ai_stock.TradingPlatform',return_value=platform),patch('src.ui.ai_stock.FuturesOpportunityScanner',side_effect=factory),patch('src.ui.ai_stock.fetch_missing_fundamentals',side_effect=lambda r,**kw:r):
        report=scan_reality_stocks(SimpleNamespace(),db,5,'AFTER_MARKET_RESEARCH','reality')
    assert report['feature']=='Reality' and len(report['reviewed'])==12
    assert captured['runtime'].cache_directory.endswith('reality_stocks_prepared')
    assert captured['runtime'].ledger_path=='data/futures_executions.sqlite3'
    assert captured['config'].maximum_daily_entries==2
    assert len(RealityStockHistory(db).list())==1
    assert not AiStockHistory(db).list() and not PowerStockHistory(db).list() and not db.list_futures_scans()
    assert all(row['futures_status']=='NO_ACTIVE_CONTRACT' for row in report['universe'])


def test_page_is_manual_and_saved_analysis_remains_visible(tmp_path):
    from streamlit.testing.v1 import AppTest
    path=tmp_path/'ui.db'
    code=f'''import streamlit as st
from types import SimpleNamespace
from src.ui.reality_stocks import render
from src.ui.database import ReportDatabase
def submit(feature,worker):st.session_state['submitted']=feature
render(SimpleNamespace(settings=SimpleNamespace()),ReportDatabase({str(path)!r}),submit,lambda _:False)
'''
    with patch('src.ui.ai_stock.TradingPlatform',side_effect=AssertionError('No automatic source calls')):
        app=AppTest.from_string(code).run(timeout=15)
        assert not app.exception and app.header[0].value=='Reality'
        assert len(app.dataframe[0].value)==6
        assert not RealityStockHistory(ReportDatabase(path)).list()
        app.selectbox[0].set_value('LIVE_SCAN').run(timeout=15)
        assert not app.exception
        next(b for b in app.button if b.label=='Scan Reality').click().run(timeout=15)
        assert app.session_state['submitted']=='reality_stocks'
        assert not app.exception
    report=snapshot()
    report['reviewed'][0].update(daily_ranking_score=82,oi_ranking={'status':'AVAILABLE','score':100},
        company_research={'fundamentals':{'pe_ratio':20,'sector_pe':22,'roe':18,'roce':25,'delivery_percent':45}})
    RealityStockHistory(ReportDatabase(path)).save(report,'saved')
    app=AppTest.from_string(code).run(timeout=15)
    assert not app.exception
    row=app.dataframe[1].value.iloc[0]
    assert row['Symbol']=='DLF' and row['Company']=='DLF Ltd'
    assert row['Daily ranking score']==82 and row['OI score']==100 and row['P/E']==20
