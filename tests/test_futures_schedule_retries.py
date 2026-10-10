"""Manual mode forbids scheduled scans regardless of previous attempts."""
from unittest.mock import Mock
import pytest
from dataclasses import replace
from test_futures_workspace import workspace, NOW, CFG
from src.futures_workspace.scheduler import WorkspaceScheduler

@pytest.mark.parametrize('state',['EMPTY','COMPLETED','FAILED','INCOMPLETE'])
def test_no_automatic_jobs_even_with_empty_or_failed_previous_state(workspace,state):
    if state!='EMPTY':
        with workspace.store.job('WEEKLY_ROTATION','old-week') as (_,output):
            output['status']='INCOMPLETE' if state=='INCOMPLETE' else 'COMPLETE'
        if state=='FAILED':
            with workspace.store.transaction() as c:
                c.execute("UPDATE ft_jobs SET status='FAILED'")
    before=workspace.store.jobs()
    workspace.rotate=Mock(side_effect=AssertionError('No automatic scans'))
    result=WorkspaceScheduler(workspace).tick(NOW)
    assert result['status']=='MANUAL_MODE'
    assert workspace.store.jobs()==before and workspace.adapter.counts=={}


def test_disabled_workspace_scheduler_still_rejects(workspace):
    workspace.config=replace(CFG,enabled=False)
    with pytest.raises(ValueError,match='disabled'):
        WorkspaceScheduler(workspace).tick(NOW)
