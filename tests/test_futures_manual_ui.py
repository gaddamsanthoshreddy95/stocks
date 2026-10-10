"""Large archived scans never render their raw JSON during navigation."""
from unittest.mock import patch
from types import SimpleNamespace
import json
from streamlit.testing.v1 import AppTest
from src.futures_workspace.store import WorkspaceStore
from src.futures_workspace.ui import JobController


def test_navigation_manual_and_large_report_is_not_rendered(tmp_path,monkeypatch):
    monkeypatch.setenv('FUTURES_WORKSPACE_ENABLED','true')
    path=tmp_path/'ui.db'; store=WorkspaceStore(path)
    huge={'status':'COMPLETE','eligible_universe_size':213,'evaluated_count':213,
          'evaluations':{f'STOCK{i}':{'symbol':f'STOCK{i}','news':'payload'*5000} for i in range(213)}}
    with store.job('WEEKLY_ROTATION') as (_,output): output.update(huge)
    jobs=JobController()
    code=f'''from types import SimpleNamespace
from src.futures_workspace.ui import render
from src.application.settings import PlatformSettings
render(SimpleNamespace(settings=PlatformSettings(market_data_source="cache")),SimpleNamespace(path={str(path)!r}))
'''
    try:
        with patch('src.futures_workspace.ui.controller',return_value=jobs),patch('src.futures_workspace.ui.TradingPlatform',side_effect=AssertionError('No automatic services')):
            app=AppTest.from_string(code).run(timeout=15)
            assert not app.exception and jobs.future is None
            app.radio[0].set_value('Weekly Rotation').run(timeout=15)
            assert not app.exception
            assert all('STOCK0' not in element.value and 'evaluations' not in element.value for element in app.json)
            assert not next(b for b in app.button if b.label=='Run Full Universe Scan').disabled
            app.checkbox[0].check().run(timeout=15)
            assert sum('STOCK0' in element.value for element in app.json)==1
            assert 'STOCK0' in app.json[-1].value and 'STOCK100' not in app.json[-1].value
            # Settings must not read any scan/history payload, even after visiting it.
            with patch.object(WorkspaceStore,'latest_result',side_effect=AssertionError('No report reads')):
                app.radio[0].set_value('Settings').run(timeout=15)
                assert not app.exception
            assert jobs.future is None
    finally: jobs.pool.shutdown(wait=True)


def test_latest_result_does_not_decode_older_corrupt_archive(tmp_path):
    store=WorkspaceStore(tmp_path/'ui.db')
    with store.transaction() as c:
        c.execute("INSERT INTO ft_jobs(id,kind,status,started_at,result) VALUES('old','WEEKLY_ROTATION','COMPLETED','2020','invalid-json')")
        c.execute("INSERT INTO ft_jobs(id,kind,status,started_at,result) VALUES('new','WEEKLY_ROTATION','COMPLETED','2026',?)",(json.dumps({'status':'COMPLETE'}),))
    assert store.latest_result('WEEKLY_ROTATION')=={'status':'COMPLETE'}
    assert len(store.jobs('WEEKLY_ROTATION',limit=1,include_results=False))==1
