"""Data-source outages and incomplete weekly classifications stay diagnosable."""
from unittest.mock import Mock, patch
from types import SimpleNamespace
import numpy as np
import pandas as pd
from test_futures_workspace import workspace, rotate, NOW, CFG, LISTS, frame
from src.futures_workspace.adapter import RepositoryAdapter
from src.futures_workspace.discovery import technical


def test_weekly_benchmark_uses_same_two_year_adjusted_window():
    provider=SimpleNamespace(get_data=Mock(side_effect=AssertionError('No short broker benchmark')))
    adapter=RepositoryAdapter(SimpleNamespace(provider=provider),CFG)
    adapter.now=NOW
    adapter.adjusted_history=Mock(return_value=frame(np.linspace(200,100,500)))
    assert len(adapter.history('NIFTY 50'))==500
    adapter.adjusted_history.assert_called_once_with('^NSEI')
    provider.get_data.assert_not_called()


def test_benchmark_outage_falls_back_to_explicit_two_year_broker_history():
    provider=SimpleNamespace(get_data=Mock(),get_long_history=Mock(return_value=frame(np.linspace(200,100,500))))
    adapter=RepositoryAdapter(SimpleNamespace(provider=provider),CFG)
    adapter.now=NOW;adapter.adjusted_history=Mock(side_effect=ValueError('Source unavailable'))
    assert len(adapter.history('NIFTY 50'))==500
    provider.get_long_history.assert_called_once_with('NIFTY 50',period='2y')


def test_optional_sector_outage_does_not_discard_valid_equity_history(workspace):
    workspace.adapter.sector_history=Mock(side_effect=TimeoutError('sector unavailable'))
    result=workspace.rotate(NOW)
    assert result['technical_classification_counts'][LISTS[0]]==3
    assert all(e['sector_context_error']=='TimeoutError' for e in result['evaluations'].values())


def test_exhausted_optional_news_retries_keep_technical_candidates(workspace):
    workspace.adapter.news=Mock(side_effect=TimeoutError('news unavailable'))
    with patch('src.futures_workspace.parallel.sleep'):
        result=rotate(workspace)
    assert result['status']=='COMPLETE'
    assert len(workspace.store.members())==2
    assert all(e['news_events']['status']=='UNKNOWN' for s,e in result['evaluations'].items() if s in ('BEAR','RECOVER'))
    assert any('OPTIONAL_CONTEXT_UNAVAILABLE' in r for r in result['evaluations']['BEAR']['reason_codes'])


def test_missing_history_is_incomplete_with_counted_reason(workspace):
    workspace.adapter.history=lambda symbol:None
    result=workspace.rotate(NOW)
    assert result['status']=='INCOMPLETE'
    assert result['data_failure_reasons']=={'HISTORY_MISSING':3}
    assert result['technical_classification_counts']=={'UNKNOWN_DATA':3}
    assert result['benchmark_quality']['status']=='UNKNOWN'


def test_gap_diagnostics_distinguish_missing_sessions_from_fetch_failure():
    data=frame(np.linspace(200,100,300))
    gap=data.index[-15]
    result=technical(data.drop(gap),CFG,NOW,data)
    assert result['reason_codes']==['HISTORY_MISSING_SESSIONS']
    assert result['history_quality']['sample_missing_sessions']==[gap.isoformat()]
    result=technical(data.tail(100),CFG,NOW)
    assert result['history_quality']['completed_rows']==100


def test_benchmark_covers_required_window_even_with_different_old_cache_start():
    data=frame(np.linspace(200,100,500))
    holiday=data.index[-100]
    stock=data.drop(holiday)
    benchmark=data.tail(300).drop(holiday)
    result=technical(stock,CFG,NOW,benchmark)
    assert result['classification']!='UNKNOWN_DATA'
    gap=stock.index[-20]
    assert technical(stock.drop(gap),CFG,NOW,benchmark)['reason_codes']==['HISTORY_MISSING_SESSIONS']
