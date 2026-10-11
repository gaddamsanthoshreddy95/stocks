"""One rotation retries failed securities and publishes only validated changes."""
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
import pandas as pd
import pytest
from kiteconnect.exceptions import TokenException
from test_futures_workspace import workspace, rotate, NOW, CFG, frame, LISTS
from src.futures_workspace.adapter import RepositoryAdapter

@pytest.mark.parametrize('failure',['invalid','stale'])
def test_failed_stock_recovers_inside_one_job_and_joins_rankings(workspace,failure):
    good=frame(np.linspace(220,100,280))
    bad=good.copy()
    if failure=='invalid': bad.loc[bad.index[-1],'High']=1
    else: bad.index=bad.index-pd.Timedelta(days=30)
    original=workspace.adapter.history;calls=Counter()
    def history(symbol):
        calls[symbol]+=1
        if symbol=='BEAR':return bad if calls[symbol]==1 else good
        return original(symbol)
    workspace.adapter.history=history
    workspace.adapter.recover_history=Mock()
    result=workspace.rotate(NOW)
    assert result['status']=='COMPLETE'
    assert calls['BEAR']==2 and calls['RECOVER']==calls['UNSELECTED']==1
    assert workspace.adapter.counts['instruments']==1
    assert len(workspace.store.jobs('WEEKLY_ROTATION'))==1
    assert result['evaluations']['BEAR']['classification']==LISTS[0]
    assert 'BEAR' in result['context_shortlist']
    assert any(m['symbol']=='BEAR' for m in workspace.store.members())
    assert result['recovery_history']['BEAR'][-1]['action']=='RECOVERED'
    if failure=='invalid':
        saved=workspace.store.latest_result('WEEKLY_ROTATION')['recovery_history']['BEAR'][0]['history_quality']
        assert saved['fetched_rows']==280
        assert saved['violations'][0]['rule']=='HIGH_BELOW_OHLC'
        assert saved['violations'][0]['samples'][0]['observed']['High']==1


def test_unresolved_invalid_stock_cannot_be_marked_complete(workspace):
    rotate(workspace)
    original=workspace.adapter.history
    bad=frame(np.linspace(220,100,280));bad.loc[bad.index[-1],'High']=1
    workspace.adapter.history=lambda s:bad if s=='BEAR' else original(s)
    workspace.adapter.recover_history=Mock()
    result=workspace.rotate(NOW)
    assert result['status']=='INCOMPLETE' and 'BEAR' in result['unresolved_failures']
    assert workspace.store.jobs('WEEKLY_ROTATION')[0]['status']=='INCOMPLETE'
    assert next(m for m in workspace.store.members() if m['symbol']=='BEAR')['status']=='REVIEW_REQUIRED'
    assert workspace.adapter.recover_history.call_count==2
    assert result['unresolved_failures']['BEAR']['history_quality']['violations'][0]['rule']=='HIGH_BELOW_OHLC'
    assert workspace.adapter.counts['instruments']==2 # one initialization + this job


def test_recovery_exception_is_recorded_without_losing_other_stocks(workspace):
    original=workspace.adapter.history
    workspace.adapter.history=lambda s:None if s=='BEAR' else original(s)
    workspace.adapter.recover_history=Mock(side_effect=RuntimeError('recovery unavailable'))
    result=workspace.rotate(NOW)
    assert result['status']=='INCOMPLETE'
    assert result['recovery_history']['BEAR'][-1]['action']=='RECOVERY_FAILED'
    assert 'BEAR' in result['unresolved_failures']
    assert result['evaluations']['RECOVER']['classification']!='UNKNOWN_DATA'


@pytest.mark.parametrize('recovers',[True,False])
def test_sector_token_exception_reloads_credentials_without_rescanning_universe(workspace,recovers):
    calls=Counter()
    def sector(symbol):
        calls[symbol]+=1
        if symbol=='BEAR' and (not recovers or calls[symbol]==1):
            raise TokenException('invalid token')
        return None
    workspace.adapter.sector_history=sector
    workspace.adapter.refresh_credentials=Mock(return_value=True)
    workspace.adapter.recover_history=Mock()
    result=workspace.rotate(NOW)
    assert workspace.adapter.counts['instruments']==1
    workspace.adapter.refresh_credentials.assert_called_once()
    assert result['status']==('COMPLETE' if recovers else 'INCOMPLETE')
    if not recovers:assert result['unresolved_failures']['BEAR']['sector_error']=='TokenException'


def test_forced_recovery_bypasses_stale_adjusted_disk_cache(tmp_path,monkeypatch):
    adapter=RepositoryAdapter(SimpleNamespace(provider=object()),CFG);adapter.now=NOW
    monkeypatch.chdir(tmp_path)
    good=frame(np.linspace(220,100,280))
    with patch('src.providers.yahoo_provider.YahooProvider.get_historical_data',return_value=good) as source:
        adapter.history('BEAR')
        adapter.recover_history('BEAR',NOW)
        adapter.history('BEAR')
        assert source.call_count==2


def test_captured_benchmark_token_error_reloads_once_and_recovers_specific_sector(workspace):
    calls=Counter()
    adapter=workspace.adapter
    adapter.resolve_sector=lambda symbol,now:{'sector':'POWER','status':'VERIFIED'}
    adapter.benchmark_evidence={}
    def sector(symbol):
        calls[symbol]+=1
        failed=symbol=='BEAR' and calls[symbol]==1
        adapter.benchmark_evidence[symbol]={'status':'UNKNOWN' if failed else 'AVAILABLE',
            'errors':[{'source':'KITE','error_type':'TokenException'}] if failed else []}
        return None
    adapter.sector_history=sector
    adapter.refresh_credentials=Mock(return_value=True)
    adapter.recover_history=Mock()
    adapter.recover_sector=Mock()
    result=workspace.rotate(NOW)
    assert result['status']=='COMPLETE'
    adapter.refresh_credentials.assert_called_once()
    adapter.recover_sector.assert_called_once_with('BEAR',NOW)
    adapter.recover_history.assert_not_called()
    assert adapter.counts['instruments']==1


def test_proxy_context_cannot_certify_complete_exact_sector_comparison(workspace):
    adapter=workspace.adapter
    adapter.resolve_sector=lambda symbol,now:{'sector':'ASSET_MANAGEMENT','status':'VERIFIED'}
    adapter.benchmark_evidence={}
    def sector(symbol):
        adapter.benchmark_evidence[symbol]={'status':'PROXY_CONTEXT_ONLY'}
        return None
    adapter.sector_history=sector
    adapter.recover_history=Mock()
    adapter.recover_sector=Mock()
    result=workspace.rotate(NOW)
    assert result['status']=='INCOMPLETE'
    assert result['unresolved_failures']['BEAR']['sector_error']=='SECTOR_COMPARISON_UNAVAILABLE'
    assert adapter.counts['instruments']==1
