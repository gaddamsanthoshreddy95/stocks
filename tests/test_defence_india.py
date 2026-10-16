from types import SimpleNamespace
from unittest.mock import patch

from src.ui.ai_stock import AiStockHistory,ThemePlatform
from src.ui.power_stocks import PowerStockHistory
from src.ui.reality_stocks import RealityStockHistory
from src.ui.defence_india import DEFENCE_STOCKS,DEFENCE_COMPANIES,DefenceIndiaHistory,scan_defence_india
from src.ui.database import ReportDatabase


def snapshot():
    return {'generated_at':'2026-10-15T18:00:00+05:30','scan_mode':'AFTER_MARKET_RESEARCH',
        'universe_size':10,'approved_count':0,'report_a':[],'report_b':[],'report_c':[],
        'reviewed':[{'symbol':symbol,'side':side,'technical_score':80,'final_decision':'UNKNOWN','reason_codes':[]}
                    for symbol in DEFENCE_STOCKS for side in ('LONG','SHORT')]}


def test_exact_ten_symbols_including_ampersand():
    assert list(DEFENCE_STOCKS)==['HAL','BEL','BDL','MAZDOCK','COCHINSHIP','BHARATFORG','LT','BHEL','ASHOKLEY','M&M']
    assert set(DEFENCE_COMPANIES)==set(DEFENCE_STOCKS)
    scoped=ThemePlatform(SimpleNamespace(provider=object()),DEFENCE_STOCKS)
    assert set(scoped.restricted_symbols)==set(scoped._universe_symbols())==set(DEFENCE_STOCKS)


def test_shared_worker_and_separate_archive(tmp_path):
    db=ReportDatabase(tmp_path/'ui.db')
    platform=SimpleNamespace(provider=SimpleNamespace(get_nfo_instruments=lambda:[]),_serialize=lambda r:r)
    captured={}
    def factory(scoped,config):
        assert set(scoped._universe_symbols())==set(DEFENCE_STOCKS)
        def scan(limit,mode,runtime):
            scoped.provider.get_nfo_instruments()
            captured.update(config=config,runtime=runtime)
            return snapshot()
        return SimpleNamespace(scan=scan)
    with patch('src.ui.ai_stock.TradingPlatform',return_value=platform),patch('src.ui.ai_stock.FuturesOpportunityScanner',side_effect=factory),patch('src.ui.ai_stock.fetch_missing_fundamentals',side_effect=lambda r,**kw:r):
        report=scan_defence_india(SimpleNamespace(),db,5,'AFTER_MARKET_RESEARCH','defence')
    assert report['feature']=='Defence India' and len(report['reviewed'])==20
    assert captured['runtime'].cache_directory.endswith('defence_india_prepared')
    assert captured['runtime'].ledger_path=='data/futures_executions.sqlite3'
    assert captured['config'].maximum_daily_entries==2
    assert len(DefenceIndiaHistory(db).list())==1
    assert not any(archive(db).list() for archive in (AiStockHistory,PowerStockHistory,RealityStockHistory))
    assert not db.list_futures_scans()
    assert all(r['futures_status']=='NO_ACTIVE_CONTRACT' for r in report['universe'])
    saved=DefenceIndiaHistory(db).get(report['_defence_saved_scan_id'])
    assert next(r for r in saved['universe'] if r['symbol']=='M&M')['company']=='Mahindra & Mahindra'


def test_manual_ui_and_saved_analysis(tmp_path):
    from streamlit.testing.v1 import AppTest
    path=tmp_path/'ui.db'
    code=f'''import streamlit as st
from types import SimpleNamespace
from src.ui.defence_india import render
from src.ui.database import ReportDatabase
def submit(feature,worker):st.session_state['submitted']=feature
render(SimpleNamespace(settings=SimpleNamespace()),ReportDatabase({str(path)!r}),submit,lambda _:False)
'''
    with patch('src.ui.ai_stock.TradingPlatform',side_effect=AssertionError('No automatic requests')):
        app=AppTest.from_string(code).run(timeout=15)
        assert not app.exception and app.header[0].value=='Defence India'
        assert len(app.dataframe[0].value)==10
        assert app.dataframe[0].value.iloc[0]['Company']=='Hindustan Aeronautics'
        app.selectbox[0].set_value('LIVE_SCAN').run(timeout=15)
        assert not app.exception
        next(b for b in app.button if b.label=='Scan Defence India').click().run(timeout=15)
        assert not app.exception and app.session_state['submitted']=='defence_india'
    assert not DefenceIndiaHistory(ReportDatabase(path)).list()
    report=snapshot()
    report['reviewed'][0].update(daily_ranking_score=82,oi_ranking={'status':'AVAILABLE','score':100},
        company_research={'fundamentals':{'pe_ratio':20,'sector_pe':22,'delivery_percent':45}})
    DefenceIndiaHistory(ReportDatabase(path)).save(report,'saved')
    app=AppTest.from_string(code).run(timeout=15)
    assert not app.exception
    row=app.dataframe[1].value.iloc[0]
    assert row['Symbol']=='HAL' and row['Company']=='Hindustan Aeronautics'
    assert row['Defence business']=='Fighter jets, helicopters'
    assert row['OI score']==100 and row['P/E']==20 and row['Delivery %']==45
