from src.futures_workspace.ui import daily_watchlist_result
from src.futures_workspace.store import LISTS
import pytest

@pytest.mark.parametrize('category,side,bucket,score,decision',[
 (LISTS[0],'SHORT','short',82,'ELIGIBLE'),
 (LISTS[1],'LONG','long',75,'ELIGIBLE'),
 (LISTS[0],'SHORT','other',0,'NO_TRADE'),
 (LISTS[1],'LONG','other',None,'UNKNOWN_DATA'),
 (LISTS[0],'SHORT','other',80,'CONFLICT')])
def test_latest_daily_scores_match_category_and_include_nontrade(category,side,bucket,score,decision):
    member={'symbol':'STOCK','category':category}
    daily={'generated_at':'2026-10-10T10:00:00+05:30',bucket:[{'symbol':'STOCK','side':side,'watchlist':category,'original_daily_score':score,'workspace_decision':decision,'final_decision':'APPROVED'}]}
    row=daily_watchlist_result(member,daily)
    assert row['Latest daily score']==score
    assert row['Latest daily decision']==decision
    assert row['Latest daily scan']==daily['generated_at']
    assert row['Daily score status']==('Unavailable' if score is None else 'Available')


def test_new_or_transferred_member_not_given_opposite_direction_score():
    member={'symbol':'STOCK','category':LISTS[1]}
    daily={'short':[{'symbol':'STOCK','side':'SHORT','watchlist':LISTS[0],'original_daily_score':90}]}
    row=daily_watchlist_result(member,daily)
    assert row['Latest daily score'] is None
    assert row['Latest daily decision']=='Not scanned'
    assert daily_watchlist_result(member,None)['Daily score status']=='Not scanned'


def test_both_watchlist_pages_display_persisted_daily_values(tmp_path,monkeypatch):
    from unittest.mock import patch
    from streamlit.testing.v1 import AppTest
    from src.futures_workspace.store import WorkspaceStore
    from src.futures_workspace.ui import JobController
    monkeypatch.setenv('FUTURES_WORKSPACE_ENABLED','true')
    path=tmp_path/'ui.db';store=WorkspaceStore(path)
    for symbol,category in [('BEAR',LISTS[0]),('RECOVER',LISTS[1])]:
        store.manage(symbol,'add',category,{'discovery':{'ranking_score':70},'reason_codes':[]})
    with store.job('DAILY_TRADING') as (_,output):
        output.update({'generated_at':'2026-10-10T10:00:00+05:30','long':[{'symbol':'RECOVER','side':'LONG','watchlist':LISTS[1],'original_daily_score':75,'workspace_decision':'ELIGIBLE'}],
                       'other':[{'symbol':'BEAR','side':'SHORT','watchlist':LISTS[0],'original_daily_score':60,'workspace_decision':'NO_TRADE'}]})
    controller=JobController()
    code=f'''from types import SimpleNamespace
from src.futures_workspace.ui import render
from src.application.settings import PlatformSettings
render(SimpleNamespace(settings=PlatformSettings(market_data_source="cache")),SimpleNamespace(path={str(path)!r}))
'''
    try:
        with patch('src.futures_workspace.ui.controller',return_value=controller):
            app=AppTest.from_string(code).run(timeout=15)
            for section,score,decision in [('Shorting Stocks',60,'NO_TRADE'),('Recovering Stocks',75,'ELIGIBLE')]:
                app.radio[0].set_value(section).run(timeout=15)
                assert not app.exception
                row=app.dataframe[0].value.iloc[0]
                assert row['Weekly score']==70 and row['Latest daily score']==score
                assert row['Latest daily decision']==decision
                assert row['Latest daily scan']=='2026-10-10T10:00:00+05:30'
                assert controller.future is None
    finally:
        controller.pool.shutdown(wait=True)
