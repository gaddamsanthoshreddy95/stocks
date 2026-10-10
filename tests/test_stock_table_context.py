from unittest.mock import patch
from src.ui.stock_tables import context_columns, render_stock_table
from src.futures_workspace.store import WorkspaceStore
from streamlit.testing.v1 import AppTest


def test_daily_context_numeric_values_and_news_sources():
    row={'sector':'IT','company_research':{'fundamentals':{'pe_ratio':0,'sector_pe':25,'roe':20,'roce':22,'source':'verified-source'}},
         'execution_checks':{'futures_vwap_quality':{'factors':{'vwap':100}},'futures_rsi_quality':{'factors':{'rsi_14':55}}},
         'evidence':{'support':95,'resistance':105,'ema_values':{'EMA21':99}},
         'news':{'requested':True,'headlines':[{'title':'Company earnings','url':'https://example.org/news','source':'Publisher','published':'2026-10-10T09:00:00+05:30'}]}}
    result=context_columns(row)
    assert result['P/E']==0 and result['Sector P/E']==25
    assert result['Futures VWAP']==100 and result['RSI']==55 and result['EMA21']==99
    assert result['Relevant news']=='Company earnings' and result['News link']=='https://example.org/news'
    assert result['News snapshot']=='Daily scan'


def test_weekly_fallback_and_failed_daily_news_are_not_silently_substituted():
    weekly={'sector':'IT','fundamentals':{'snapshot':{'pe_ratio':15}},'metrics':{'rsi':42,'sma20':90},
            'news_events':{'news':{'requested':True,'headlines':[{'title':'Saved news','url':'javascript:alert(1)'}]}}}
    result=context_columns({},weekly)
    assert result['P/E']==15 and result['Weekly rsi']==42 and result['News link'] is None
    assert result['Relevant news']=='Saved news' and result['News snapshot']=='Weekly rotation'
    failed=context_columns({'news':{'fetch_failed':True}},weekly)
    assert failed['News status']=='Fetch failed' and 'Saved news' not in failed['Relevant news']


def test_collected_headlines_visible_even_if_analysis_unavailable():
    result=context_columns({'news':{'fetch_failed':True,'collection_state':'FETCHED','headlines':[{'title':'Relevant headline'}]}})
    assert result['News status']=='Headlines available; analysis unavailable'
    assert result['Relevant news']=='Relevant headline'
    assert context_columns({'news':{'requested':True}})['News status']=='No relevant headlines found'
    assert context_columns({})['News status']=='Not checked / unknown'


def test_column_preferences_persist_independently_and_survive_missing_fields(tmp_path):
    store=WorkspaceStore(tmp_path/'ui.db')
    store.save_view_columns('daily',['Symbol','P/E','obsolete'])
    assert WorkspaceStore(store.path).view_columns('daily')==['Symbol','P/E','obsolete']
    assert store.view_columns('shorting') is None
    code=f'''from src.ui.stock_tables import render_stock_table
from src.futures_workspace.store import WorkspaceStore
render_stock_table([{{'Symbol':'STOCK','P/E':20,'Relevant news':'headline'}}],'daily',WorkspaceStore({str(store.path)!r}))
'''
    app=AppTest.from_string(code).run(timeout=15)
    assert not app.exception
    assert list(app.dataframe[0].value.columns)==['Symbol','P/E']
    app.multiselect[0].set_value(['Symbol','Relevant news']).run(timeout=15)
    next(b for b in app.button if b.label=='Save column selection').click().run(timeout=15)
    assert store.view_columns('daily')==['Symbol','Relevant news']


def test_legacy_score_visible_without_overriding_explicit_missing_daily_score():
    from src.futures_workspace.ui import result_rows
    assert result_rows([{'symbol':'STOCK','technical_score':82}])[0]['Daily score']==82
    assert result_rows([{'symbol':'STOCK','technical_score':82,'original_daily_score':None}])[0]['Daily score'] is None
