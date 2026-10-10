from collections import Counter
from threading import Lock
from unittest.mock import Mock, patch
import pytest
from src.futures_workspace.parallel import AdaptiveRunner
from test_futures_workspace import workspace, rotate


def test_scales_beyond_eight_without_repeating_completed_items():
    runner=AdaptiveRunner(maximum=64,healthy_batches=2,backoff=0)
    calls=Counter()
    lock=Lock()
    def work(item):
        with lock: calls[item]+=1
        return item
    items=list(range(1000))
    assert runner.run(items,work,lambda i,e:None)=={i:i for i in items}
    assert all(count==1 for count in calls.values())
    assert [e['to'] for e in runner.events if e['action']=='SCALE_UP']==[4,8,16,32,64]


def test_timeout_steps_back_and_retries_only_failed_stock():
    runner=AdaptiveRunner(maximum=32,healthy_batches=1,backoff=0)
    counts=Counter();lock=Lock()
    def work(item):
        with lock:
            counts[item]+=1
            first=counts[item]==1
        if item==6 and first: raise TimeoutError('source timeout')
        return item
    result=runner.run(list(range(50)),work,lambda i,e:None)
    assert len(result)==50 and counts[6]==2
    assert all(c==1 for i,c in counts.items() if i!=6)
    assert any(e['action']=='BACK_OFF' and e['from']==8 and e['to']==4 for e in runner.events)


def test_exhausted_retries_produce_unknown_without_hanging():
    runner=AdaptiveRunner(maximum=8,retries=2,backoff=0)
    work=Mock(side_effect=TimeoutError('timeout'))
    result=runner.run(['stock'],work,lambda i,e:{'classification':'UNKNOWN_DATA'})
    assert work.call_count==3 and result['stock']['classification']=='UNKNOWN_DATA'


def test_data_errors_do_not_retry_or_promote_concurrency():
    runner=AdaptiveRunner(maximum=16,healthy_batches=1,backoff=0)
    work=Mock(side_effect=ValueError('invalid candles'))
    result=runner.run(list(range(8)),work,lambda i,e:None)
    assert len(result)==8 and work.call_count==8
    assert runner.workers==2


def test_context_retry_reuses_successful_fundamentals(workspace):
    workspace.adapter.fundamentals=Mock(return_value={'status':'UNKNOWN'})
    counts=Counter();lock=Lock()
    def news(symbol,now):
        with lock:
            counts[symbol]+=1
            first=counts[symbol]==1
        if symbol=='BEAR' and first: raise TimeoutError('timeout')
        return {'status':'UNKNOWN'}
    workspace.adapter.news=news
    with patch('src.futures_workspace.parallel.sleep'):
        result=rotate(workspace)
    assert result['status']=='COMPLETE' and counts['BEAR']==2
    assert Counter(c.args[0] for c in workspace.adapter.fundamentals.call_args_list)=={'BEAR':1,'RECOVER':1}
    assert result['parallel_events']


def test_step_back_uses_actual_previous_level_for_non_power_ceiling():
    runner=AdaptiveRunner(maximum=10,healthy_batches=1,backoff=0)
    failed=False
    lock=Lock()
    def work(item):
        nonlocal failed
        with lock:
            if runner.workers==10 and not failed:
                failed=True
                raise TimeoutError('timeout')
        return item
    runner.run(list(range(80)),work,lambda i,e:None)
    assert any(e['action']=='BACK_OFF' and e['from']==10 and e['to']==8 for e in runner.events)


def test_shared_history_only_fetched_once_in_parallel():
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from src.futures_workspace.adapter import RepositoryAdapter
    from test_futures_workspace import CFG, NOW, frame
    import numpy as np
    adapter=RepositoryAdapter(SimpleNamespace(provider=object()),CFG)
    adapter.now=NOW
    adapter.adjusted_history=Mock(return_value=frame(np.linspace(200,100,280)))
    with ThreadPoolExecutor(max_workers=8) as pool:
        values=list(pool.map(adapter.history,['BEAR']*16))
    assert len(values)==16
    adapter.adjusted_history.assert_called_once_with('BEAR')


def test_new_source_phase_starts_at_two_and_skipped_items_do_not_promote():
    runner=AdaptiveRunner(maximum=32,healthy_batches=1,backoff=0)
    runner.run(list(range(100)),lambda i:i,lambda i,e:None)
    assert runner.workers==32
    levels=[]
    def skipped(item):
        levels.append(runner.workers)
        return {'eligible_for_admission':False}
    runner.run(list(range(30)),skipped,lambda i,e:None)
    assert set(levels)=={2}
