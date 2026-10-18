from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.ui.imp_future import discount_evidence, filter_discount_report, scan_imp_future, ImpFutureHistory
from src.ui.database import ReportDatabase


def candidate(symbol='ABC', future=99, decision='UNKNOWN'):
    contract={'tradingsymbol':symbol+'26OCTFUT','instrument_token':123}
    return {'symbol':symbol,'side':'LONG','technical_score':80,'final_decision':decision,
        'contract':contract,'entry_quality_input':{'symbol':symbol,'contract':contract,
        'decision_at':'2026-10-18T11:00:00+05:30','stock_price':100,
        'stock_quote_at':'2026-10-18T10:59:50+05:30',
        'quote':{'last_price':future,'instrument_token':123,'timestamp':'2026-10-18T10:59:50'}}}


@pytest.mark.parametrize('price,status',[(99,'MATCH'),(98,'MATCH'),(99.8,'NO_MATCH'),(99.000001,'NO_MATCH'),(98.999999,'MATCH'),(100,'NO_MATCH'),(101,'NO_MATCH'),(0,'UNAVAILABLE'),(float('nan'),'UNAVAILABLE')])
def test_discount_rule(price,status):
    assert discount_evidence(candidate(future=price))['status']==status


def test_stale_and_wrong_contract_never_match():
    row=candidate();row['entry_quality_input']['quote']['timestamp']='2026-10-18T10:50:00'
    assert discount_evidence(row)['reason']=='PAIRED_QUOTES_STALE_OR_UNVERIFIED'
    row=candidate();row['entry_quality_input']['quote']['instrument_token']=999
    assert discount_evidence(row)['status']=='UNAVAILABLE'
    row=candidate();del row['entry_quality_input']
    assert discount_evidence(row)['status']=='UNAVAILABLE'


def report():
    rows=[candidate('PREMIUM',101),candidate('DISCOUNT',99),candidate('NEXT',98)]
    return {'generated_at':'2026-10-18T11:00:00+05:30','scan_mode':'LIVE_SCAN',
        'reviewed':rows,'report_a':rows,'report_b':[],'report_c':[], 'approved_count':0}


def test_filter_before_limit_preserves_scores_gates_and_original():
    source=report();original=deepcopy(source)
    result=filter_discount_report(source,1)
    assert source==original
    assert result['report_a'][0]['symbol']=='DISCOUNT'
    assert len(result['reviewed'])==2
    assert result['approved_count']==0 and result['report_c']==[]
    assert result['reviewed'][0]['technical_score']==80
    assert result['reviewed'][0]['final_decision']=='UNKNOWN'


def test_one_full_universe_scan_and_separate_history(tmp_path):
    db=ReportDatabase(tmp_path/'ui.db');calls=[]
    platform=SimpleNamespace(provider=SimpleNamespace(get_nfo_instruments=lambda:[]),
        _universe_symbols=lambda:['PREMIUM','DISCOUNT','NEXT']+[f'STOCK{i}' for i in range(210)],_serialize=lambda r:r)
    def factory(scoped,config):
        assert len(scoped._universe_symbols())==213
        assert config.maximum_daily_entries==2 and config.review_per_direction>=213
        def scan(limit,mode,runtime):
            calls.append(limit)
            assert 1 <= limit <= 50 and runtime.cache_directory.endswith('imp_future_prepared')
            assert runtime.ledger_path=='data/futures_executions.sqlite3'
            return report()
        return SimpleNamespace(scan=scan)
    with patch('src.ui.ai_stock.TradingPlatform',return_value=platform),patch('src.ui.ai_stock.FuturesOpportunityScanner',side_effect=factory),patch('src.ui.ai_stock.fetch_missing_fundamentals',side_effect=lambda r,**kw:r):
        result=scan_imp_future(SimpleNamespace(),db,1,'LIVE_SCAN','test')
    assert len(calls)==1
    assert ImpFutureHistory(db).get(result['_imp_future_saved_scan_id'])['report_a'][0]['symbol']=='DISCOUNT'
    assert not db.list_futures_scans()


def test_page_navigation_starts_no_scan(tmp_path):
    from streamlit.testing.v1 import AppTest
    code=f'''from types import SimpleNamespace
from src.ui.imp_future import render
from src.ui.database import ReportDatabase
def forbidden(*args):raise AssertionError('Automatic scan')
render(SimpleNamespace(settings=None),ReportDatabase({str(tmp_path/'ui.db')!r}),forbidden,lambda _:False)
'''
    app=AppTest.from_string(code).run(timeout=15)
    assert not app.exception
    assert app.selectbox[0].value=='LIVE_SCAN'


@pytest.mark.parametrize('spot,future,status',[(123.45,122.2155,'MATCH'),(123.45,122.215501,'NO_MATCH')])
def test_one_percent_boundary_at_non_integer_stock_price(spot,future,status):
    row=candidate(future=future)
    row['entry_quality_input']['stock_price']=spot
    assert discount_evidence(row)['status']==status


def test_subthreshold_discount_is_replaced_before_limit():
    source=report()
    source['reviewed'][0]=candidate('SMALL_DISCOUNT',99.8)
    source['report_a']=source['reviewed']
    result=filter_discount_report(source,1)
    assert result['report_a'][0]['symbol']=='DISCOUNT'
    assert result['discount_summary']['minimum_discount_percent']==1
    assert result['discount_diagnostics'][0]['reason']=='FUTURES_DISCOUNT_BELOW_1_PERCENT'


def test_discount_candidate_beyond_first_fifty_is_not_lost():
    source=report()
    source['reviewed']=[candidate(f'PREMIUM{i}',101) for i in range(60)]+[candidate('LATE_DISCOUNT',98)]
    source['report_a']=source['reviewed'][:5]
    result=filter_discount_report(source,5)
    assert [i['symbol'] for i in result['report_a']]==['LATE_DISCOUNT']


def test_rebuilt_shortlist_preserves_news_exclusions_and_execution_gates():
    source=report()
    excluded=candidate('NEWS_CONFLICT',98,'APPROVED')
    excluded['news_conflict_gate']={'excluded':True}
    blocked=candidate('UNAPPROVED',98,'WAIT')
    approved=candidate('APPROVED',98,'APPROVED')
    source['reviewed']=[excluded,blocked,approved]
    result=filter_discount_report(source,5)
    assert [i['symbol'] for i in result['report_a']]==['APPROVED','UNAPPROVED']
    assert [i['symbol'] for i in result['report_c']]==['APPROVED']


def test_page_limit_matches_shared_scanner_limit(tmp_path):
    from streamlit.testing.v1 import AppTest
    code=f'''from types import SimpleNamespace
from src.ui.imp_future import render
from src.ui.database import ReportDatabase
render(SimpleNamespace(settings=None),ReportDatabase({str(tmp_path/'ui.db')!r}),lambda *args:None,lambda _:False)
'''
    app=AppTest.from_string(code).run(timeout=15)
    assert not app.exception
    assert app.number_input[0].max==50
