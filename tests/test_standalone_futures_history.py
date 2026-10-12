from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest
from src.ui.database import ReportDatabase
from src.ui.futures_history import scan_and_save


def report():
    return {'report_type':'bidirectional_futures','generated_at':'2026-10-12T10:00:00+05:30',
        'scan_mode':'LIVE_SCAN','universe_size':31,'approved_count':0,
        'long_discovery_count':31,'short_discovery_count':31,
        'execution_policy':'Read-only test fixture','historical_validation':'Synthetic test fixture',
        'reviewed':[{'symbol':'HINDPETRO','side':'SHORT','technical_score':80,'final_decision':'UNKNOWN','reason_codes':[]}],
        'report_a':[],'report_b':[],'report_c':[]}


def test_history_persists_across_database_reopen_and_remains_separate(tmp_path):
    path=tmp_path/'ui.db';db=ReportDatabase(path)
    value=report();scan_id=db.save_futures_scan(value,'kite','job-1')
    fresh=ReportDatabase(path)
    assert fresh.get_futures_scan(scan_id)==value
    assert fresh.list_reports()==[]
    header=fresh.list_futures_scans()[0]
    assert header['universe_size']==31 and header['scan_mode']=='LIVE_SCAN'
    assert 'report_json' not in header
    assert db.save_futures_scan({**value,'approved_count':9},'kite','job-1')==scan_id
    assert fresh.get_futures_scan(scan_id)['approved_count']==0


def test_worker_saves_before_result_without_browser_sync(tmp_path):
    db=ReportDatabase(tmp_path/'ui.db');platform=Mock()
    platform.scan_futures_opportunities.return_value=report()
    with patch('src.ui.futures_history.TradingPlatform',return_value=platform),patch('src.ui.futures_history.fetch_missing_fundamentals',side_effect=lambda value,**kwargs:value):
        result=scan_and_save(SimpleNamespace(market_data_source='kite'),db,5,'LIVE_SCAN','job-1')
    assert db.get_futures_scan(result['_ui_saved_scan_id'])['reviewed'][0]['symbol']=='HINDPETRO'
    platform.scan_futures_opportunities.assert_called_once_with(5,mode='LIVE_SCAN')


def test_failed_scan_does_not_archive_a_success(tmp_path):
    db=ReportDatabase(tmp_path/'ui.db');platform=Mock()
    platform.scan_futures_opportunities.side_effect=RuntimeError('source failed')
    with patch('src.ui.futures_history.TradingPlatform',return_value=platform),pytest.raises(RuntimeError):
        scan_and_save(SimpleNamespace(market_data_source='kite'),db,5,'LIVE_SCAN','job-1')
    assert not db.list_futures_scans()


def test_history_page_read_does_not_start_a_scan(tmp_path):
    from streamlit.testing.v1 import AppTest
    path=tmp_path/'ui.db';db=ReportDatabase(path)
    db.save_futures_scan(report(),'cache','job-1')
    code=f'''from types import SimpleNamespace
from ui_app import futures_opportunities_page
from src.ui.database import ReportDatabase
futures_opportunities_page(SimpleNamespace(settings=SimpleNamespace(market_data_source="cache")),ReportDatabase({str(path)!r}))
'''
    with patch('src.ui.futures_history.TradingPlatform',side_effect=AssertionError('Viewing history must not scan')):
        app=AppTest.from_string(code).run(timeout=15)
        assert not app.exception
        assert {'Missing data','History'}.issubset({tab.label for tab in app.tabs})
        next(box for box in app.checkbox if box.label=='View a saved Futures scan').check().run(timeout=15)
        assert not app.exception
        assert any('Historical snapshot' in warning.value for warning in app.warning)
    assert len(db.list_futures_scans())==1


def test_missing_fields_retain_reasons_and_do_not_mark_known_values_missing():
    from src.ui.futures_history import missing_data_rows
    value=report()
    value['reviewed']=[{'symbol':'HINDPETRO','side':'SHORT','reason_codes':['CANDLES_MISSING'],
        'evidence':{'rsi_14':47,'relative_volume':1.2},
        'company_research':{'fundamentals':{'pe_ratio':0,'sector_pe':15}},
        'execution_checks':{'futures_vwap_quality':{'status':'UNKNOWN','reason_codes':['INTRADAY_HISTORY_MISSING']}}}]
    rows=missing_data_rows(value)
    assert not any(row['Missing field'] in ('P/E','Sector P/E','RSI','Relative volume') for row in rows)
    assert next(row for row in rows if row['Missing field']=='Futures VWAP')['Reason']=='INTRADAY_HISTORY_MISSING'


def test_missing_data_and_history_tabs_visible_before_first_scan(tmp_path):
    from streamlit.testing.v1 import AppTest
    code=f'''from types import SimpleNamespace
from ui_app import futures_opportunities_page
from src.ui.database import ReportDatabase
futures_opportunities_page(SimpleNamespace(settings=SimpleNamespace(market_data_source="cache")),ReportDatabase({str(tmp_path/'empty.db')!r}))
'''
    with patch('src.ui.futures_history.TradingPlatform',side_effect=AssertionError('No automatic scan')):
        app=AppTest.from_string(code).run(timeout=15)
        assert not app.exception
        assert {'Missing data','History'}.issubset({tab.label for tab in app.tabs})
