"""Post-market discovery and bounded technical-first enrichment."""
from dataclasses import replace
from unittest.mock import Mock, patch
from types import SimpleNamespace
import pandas as pd
import numpy as np
import pytest
from test_futures_workspace import workspace, rotate, evaluation, LISTS, NOW, CFG, frame, contract
from src.futures_workspace.liquidity import historical_volume, closing_volume
from src.futures_workspace.adapter import RepositoryAdapter


def test_postmarket_missing_depth_does_not_reject_candidates(workspace):
    workspace.adapter.quotes=Mock(return_value={})
    result=rotate(workspace)
    assert result['status']=='COMPLETE'
    assert {m['symbol'] for m in workspace.store.members()}=={'BEAR','RECOVER'}
    assert all(m['status']=='ACTIVE' for m in workspace.store.members())
    assert result['execution_liquidity_unverified']==['BEAR','RECOVER']
    assert all('WEEKLY_EXECUTION_LIQUIDITY_UNVERIFIED_DAILY_CHECK_REQUIRED' in result['evaluations'][s]['reason_codes'] for s in ('BEAR','RECOVER'))


def test_closing_volume_ignores_spread_and_depth():
    quote={'volume':2000,'timestamp':'2026-10-09 15:29:00+05:30'}
    assert closing_volume(quote,CFG,NOW)['status']=='PASS'
    quote['depth']={'buy':[{'price':1}],'sell':[{'price':1000}]}
    assert closing_volume(quote,CFG,NOW)['status']=='PASS'
    quote['volume']=1
    assert closing_volume(quote,CFG,NOW)['status']=='FAIL'


def test_zero_quote_requires_history_but_verified_zero_history_fails():
    quote={'volume':0,'timestamp':'2026-10-09 16:52:45+05:30'}
    assert closing_volume(quote,CFG,NOW)['status']=='UNKNOWN'
    data=frame(np.linspace(220,100,280));data['Volume']=0
    assert historical_volume(data,CFG,NOW)['status']=='FAIL'


def test_input_failure_and_zero_quote_retain_request_error_without_low_volume_claim(workspace):
    from kiteconnect.exceptions import InputException
    workspace.adapter.weekly_liquidity=Mock(side_effect=InputException('Invalid from_date fixture'))
    original=workspace.adapter.quotes
    def quotes(contracts):
        values=original(contracts)
        for quote in values.values():quote['volume']=0
        return values
    workspace.adapter.quotes=quotes
    result=workspace.rotate(NOW)
    failure=result['unresolved_failures']['BEAR']
    assert failure['error_message']=='Invalid from_date fixture'
    assert failure['request']['interval']=='day'
    assert failure['request']['instrument_token']==result['evaluations']['BEAR']['contract']['instrument_token']
    item=result['evaluations']['BEAR']
    assert item['futures_quality']['status']=='UNKNOWN'
    assert item['futures_quality']['historical_fetch_error']==failure
    assert 'COMPLETED_FUTURES_VOLUME_BELOW_THRESHOLD_REVIEW_REQUIRED' not in item['reason_codes']
    assert item['classification']=='SHORTING_STOCKS'
    assert item['stock_quality']['status']=='PASS'
    assert result['status']=='INCOMPLETE'


@pytest.mark.parametrize('stamp',['2026-10-09 10:00+05:30','2026-09-01 15:29+05:30','2026-10-12 15:29+05:30'])
def test_partial_stale_future_closing_volume_unknown(stamp):
    assert closing_volume({'volume':2000,'timestamp':stamp},CFG,NOW)['status']=='UNKNOWN'


def test_completed_futures_volume_excludes_live_candle():
    bars=frame(np.linspace(200,100,280)).tail(5)
    live=bars.tail(1).copy();live.index=[pd.Timestamp('2026-10-12',tz='Asia/Kolkata')];live.Volume=0
    result=historical_volume(pd.concat([bars,live]),CFG,pd.Timestamp('2026-10-12 12:00',tz='Asia/Kolkata'))
    assert result['status']=='PASS' and result['sessions']==5
    assert result['as_of'].startswith('2026-10-09')
    bars.Volume=1
    assert historical_volume(bars,CFG,NOW)['status']=='FAIL'
    assert historical_volume(bars,CFG,NOW+pd.Timedelta(days=10))['status']=='UNKNOWN'


def test_screening_precedes_context_and_skips_non_candidates(workspace):
    visited=[]
    original=workspace.adapter.history
    workspace.adapter.history=lambda symbol:(visited.append(symbol) or original(symbol))
    def fundamentals(symbol,now):
        assert {'BEAR','RECOVER','UNSELECTED'}.issubset(visited)
        return {'status':'UNKNOWN'}
    workspace.adapter.fundamentals=Mock(side_effect=fundamentals)
    workspace.adapter.news=Mock(return_value={'status':'UNKNOWN'})
    workspace.adapter.quotes=Mock(wraps=workspace.adapter.quotes)
    result=rotate(workspace)
    assert {c.args[0] for c in workspace.adapter.fundamentals.call_args_list}=={'BEAR','RECOVER'}
    assert workspace.adapter.news.call_count==2
    assert set(workspace.adapter.quotes.call_args.args[0])=={'BEAR','RECOVER'}
    assert result['technical_evaluated_count']==3 and result['context_evaluated_count']==2


def test_existing_nonqualifier_still_receives_event_context(workspace):
    rotate(workspace)
    workspace.adapter.news=Mock(return_value={'status':'UNKNOWN'})
    rotate(workspace,{s:evaluation('NONE',20) for s in ('BEAR','RECOVER','UNSELECTED')})
    assert {c.args[0] for c in workspace.adapter.news.call_args_list}=={'BEAR','RECOVER'}


def test_confirmed_low_futures_volume_preserves_weekly_stock_candidates(workspace):
    rotate(workspace)
    workspace.adapter.weekly_liquidity=Mock(return_value={'status':'FAIL','average_volume':1})
    result=rotate(workspace)
    assert all(m['status']=='ACTIVE' for m in workspace.store.members())
    assert len(workspace.store.members())==2
    assert result['evaluations']['BEAR']['classification']=='SHORTING_STOCKS'
    assert result['evaluations']['BEAR']['futures_quality']['status']=='FAIL'
    assert 'FUTURES_VOLUME_BELOW_THRESHOLD_CONTEXT_ONLY' in result['evaluations']['BEAR']['reason_codes']


def test_confirmed_low_stock_volume_still_blocks_weekly_admission(workspace):
    for symbol in ('BEAR','RECOVER'):
        workspace.adapter.frames[symbol]['Volume']=1
    result=rotate(workspace)
    assert result['evaluations']['BEAR']['stock_quality']['status']=='FAIL'
    assert result['evaluations']['BEAR']['classification']=='UNKNOWN_DATA'
    assert not workspace.store.members(active=True)


def test_context_shortlist_capacity_bounds_requests(workspace):
    workspace.config=replace(CFG,maximum_per_list=1,maximum_context_per_direction=2)
    workspace.adapter.fundamentals=Mock(return_value={'status':'UNKNOWN'})
    result=rotate(workspace,{s:evaluation(LISTS[0],80) for s in ('BEAR','RECOVER','UNSELECTED')})
    assert result['technical_evaluated_count']==3 and result['context_evaluated_count']==2
    assert workspace.adapter.fundamentals.call_count==2
    assert len(workspace.store.members())==1


def test_exact_contract_liquidity_cache_reuses_completed_session_and_rechecks_threshold(tmp_path,monkeypatch):
    adapter=RepositoryAdapter(SimpleNamespace(provider=object()),CFG)
    monkeypatch.chdir(tmp_path)
    gateway=Mock();adapter.gateway=gateway
    gateway.history.return_value=[{'date':d.isoformat(),'volume':2000} for d in pd.date_range('2026-10-05',periods=5,tz='Asia/Kolkata')]
    first=adapter.weekly_liquidity(contract(),NOW)
    assert first['status']=='PASS'
    adapter.config=replace(CFG,minimum_futures_volume=3000)
    second=adapter.weekly_liquidity(contract(),NOW)
    assert second['status']=='FAIL' and second['cache_reused']
    assert gateway.history.call_count==1
    other=contract(tradingsymbol='BEAR26NOVFUT',instrument_token=44)
    adapter.weekly_liquidity(other,NOW)
    assert gateway.history.call_count==2


def test_adjusted_history_cache_reused_across_job_and_weekend(tmp_path,monkeypatch):
    adapter=RepositoryAdapter(SimpleNamespace(provider=object()),CFG)
    monkeypatch.chdir(tmp_path)
    adapter.now=NOW
    with patch('src.providers.yahoo_provider.YahooProvider.get_historical_data',return_value=frame(np.linspace(200,100,280))) as fetch:
        adapter.adjusted_history('BEAR')
        adapter.now=NOW+pd.Timedelta(days=1)
        adapter.adjusted_history('BEAR')
        assert fetch.call_count==1
        adapter.now=pd.Timestamp('2026-10-12 18:00',tz='Asia/Kolkata')
        adapter.adjusted_history('BEAR')
        assert fetch.call_count==2
