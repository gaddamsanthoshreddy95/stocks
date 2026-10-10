"""Finished rotations never trigger an automatic full-universe retry loop."""
from unittest.mock import Mock, patch
import pandas as pd
from test_futures_workspace import workspace, NOW, evaluation, LISTS, rotate
from src.futures_workspace.scheduler import WorkspaceScheduler
from src.futures_workspace.ui import JobController


def test_incomplete_rotation_attempts_once_per_week_across_restarts(workspace):
    with patch('src.futures_workspace.service.technical',return_value={'classification':'UNKNOWN_DATA','technical_score':None,'confidence':0,'reason_codes':['MISSING']}):
        first=WorkspaceScheduler(workspace).tick(NOW)
    assert first['status']=='INCOMPLETE'
    calls=workspace.adapter.counts['instruments']
    workspace.rotate=Mock(side_effect=AssertionError('No automatic full scan retry'))
    for _ in range(3):
        result=WorkspaceScheduler(workspace).tick(NOW+pd.Timedelta(minutes=10))
        assert result['status']=='AUTO_RETRY_PAUSED' and result['previous_status']=='INCOMPLETE'
    assert workspace.adapter.counts['instruments']==calls
    assert len(workspace.store.jobs('WEEKLY_ROTATION'))==1


def test_failed_scheduled_rotation_not_retried(workspace):
    with workspace.store.job('UNIVERSE_REFRESH','UNIVERSE:2026-10-10'):
        pass
    try:
        with workspace.store.job('WEEKLY_ROTATION','WEEKLY:2026-10-10'):
            raise RuntimeError('Source unavailable')
    except RuntimeError:
        pass
    workspace.rotate=Mock(side_effect=AssertionError('Do not retry'))
    result=WorkspaceScheduler(workspace).tick(NOW)
    assert result['status']=='AUTO_RETRY_PAUSED' and result['reason']=='Source unavailable'
    assert workspace.adapter.counts=={}


def test_failed_universe_refresh_not_retried_repeatedly(workspace):
    try:
        with workspace.store.job('UNIVERSE_REFRESH','UNIVERSE:2026-10-10'):
            raise RuntimeError('Broker unavailable')
    except RuntimeError:
        pass
    assert WorkspaceScheduler(workspace).tick(NOW)['status']=='AUTO_RETRY_PAUSED'
    assert workspace.adapter.counts=={}


def test_manual_full_rotation_satisfies_current_week(workspace):
    rotate(workspace)
    with workspace.store.transaction() as c:
        c.execute("UPDATE ft_jobs SET started_at=? WHERE kind='WEEKLY_ROTATION'",(NOW.isoformat(),))
    workspace.rotate=Mock(side_effect=AssertionError('Manual scan already evaluated universe'))
    result=WorkspaceScheduler(workspace).tick(NOW+pd.Timedelta(minutes=1))
    assert result['status']=='ALREADY_COMPLETED'


def test_next_week_still_runs_and_manual_retry_is_available(workspace):
    with patch('src.futures_workspace.service.technical',return_value={'classification':'UNKNOWN_DATA','technical_score':None,'confidence':0,'reason_codes':['MISSING']}):
        WorkspaceScheduler(workspace).tick(NOW)
    rotate(workspace)  # A manual retry is explicitly allowed.
    with patch('src.futures_workspace.service.technical',return_value=evaluation(LISTS[0])):
        WorkspaceScheduler(workspace).tick(NOW+pd.Timedelta(days=7))
    assert len(workspace.store.jobs('WEEKLY_ROTATION'))==3


def test_schedule_delay_starts_after_long_operation_finishes():
    controller=JobController()
    def long_job():
        assert controller.next_schedule==float('inf')
        return {'status':'INCOMPLETE'}
    try:
        with patch('src.futures_workspace.ui.time.monotonic',side_effect=[100,1000]):
            assert controller.submit('Rotation',long_job)
            controller.future.result(timeout=5)
            assert controller.next_schedule==1300
    finally:
        controller.pool.shutdown(wait=True)
