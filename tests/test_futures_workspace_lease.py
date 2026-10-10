"""Real process crashes, live-worker exclusion, and legacy migration safety."""
from pathlib import Path
import subprocess
import sys
import pytest
from src.futures_workspace.store import WorkspaceStore


def test_worker_process_crash_recovers_automatically(tmp_path):
    path=tmp_path/'ui.db'
    store=WorkspaceStore(path)
    script='''import os, sys
from src.futures_workspace.store import WorkspaceStore
s=WorkspaceStore(sys.argv[1])
with s.job('WEEKLY_ROTATION'):
    os._exit(7)
'''
    result=subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True,timeout=20)
    assert result.returncode==7, result.stderr.decode()
    assert store.jobs()[0]['status']=='RUNNING'
    assert store.locked_job() is None
    assert store.jobs()[0]['status']=='FAILED'
    assert 'Automatically recovered' in store.jobs()[0]['error']
    with store.job('NEXT_SCAN'):
        pass


def test_active_worker_cannot_be_recovered_or_duplicated(tmp_path):
    store=WorkspaceStore(tmp_path/'ui.db')
    with store.job('WEEKLY_ROTATION') as (job_id,result):
        assert store.locked_job()['id']==job_id
        with pytest.raises(RuntimeError,match='active'):
            store.recover_job(job_id,worker_stopped=True)
        script='''import sys
from src.futures_workspace.store import WorkspaceStore
s=WorkspaceStore(sys.argv[1])
assert s.locked_job() is not None
try:
    with s.job('DUPLICATE'): pass
except RuntimeError:
    sys.exit(3)
sys.exit(9)
'''
        process=subprocess.run([sys.executable,'-c',script,str(store.path)],capture_output=True,timeout=20)
        assert process.returncode==3,process.stderr.decode()
    assert store.locked_job() is None


def test_legacy_lock_requires_explicit_stopped_worker_recovery(tmp_path):
    store=WorkspaceStore(tmp_path/'ui.db')
    with store.transaction() as c:
        c.execute("INSERT INTO ft_jobs(id,kind,status,started_at) VALUES('legacy','WEEKLY_ROTATION','RUNNING','2026-10-10')")
        c.execute("INSERT INTO ft_lease VALUES('workspace','legacy')")
    assert store.locked_job()['id']=='legacy'
    with pytest.raises(RuntimeError):
        with store.job('BLOCKED'): pass
    store.recover_job('legacy',worker_stopped=True)
    assert store.locked_job() is None


def test_finalization_failure_does_not_permanently_lock_workspace(tmp_path):
    store=WorkspaceStore(tmp_path/'ui.db')
    with pytest.raises(RecursionError):
        with store.job('UNSERIALIZABLE') as (_,output):
            output['invalid']=output
    assert store.locked_job() is None
    assert store.jobs()[0]['status']=='FAILED'


def test_recover_command_finds_legacy_job_without_broker_credentials(tmp_path):
    path=tmp_path/'ui.db'
    store=WorkspaceStore(path)
    with store.transaction() as c:
        c.execute("INSERT INTO ft_jobs(id,kind,status,started_at) VALUES('legacy','WEEKLY_ROTATION','RUNNING','2026-10-10')")
        c.execute("INSERT INTO ft_lease VALUES('workspace','legacy')")
    result=subprocess.run([sys.executable,'scripts/run_futures_workspace.py','recover-job',
                           '--database',str(path),'--worker-stopped'],capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr.decode()
    assert 'RECOVERED' in result.stdout.decode()
    assert store.locked_job() is None


@pytest.mark.parametrize('blocked',[False,True])
def test_windows_lock_branch_and_release(tmp_path,monkeypatch,blocked):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import src.futures_workspace.lease as lease
    def locking(fd,mode,size):
        if mode==1 and blocked:
            raise OSError('locked')
    native=SimpleNamespace(LK_NBLCK=1,LK_UNLCK=2,locking=Mock(side_effect=locking))
    monkeypatch.setattr(lease,'os',SimpleNamespace(name='nt'))
    monkeypatch.setitem(sys.modules,'msvcrt',native)
    with lease.worker_lock(tmp_path/'ui.db') as acquired:
        assert acquired is not blocked
    assert native.locking.call_count==(1 if blocked else 2)
    if not blocked:
        assert native.locking.call_args.args[1:]==(2,1)
