"""Strict rejection diagnostics and stale-source isolation."""
from unittest.mock import Mock,patch
from types import SimpleNamespace
from dataclasses import replace
import hashlib
import numpy as np
import pandas as pd
import pytest
from src.futures_workspace.validation import ohlcv_diagnostics, HistoryValidationError
from src.futures_workspace.adapter import RepositoryAdapter
from src.futures_workspace.discovery import technical
from test_futures_workspace import frame,CFG,NOW

@pytest.mark.parametrize('field,value,rule',[
 ('High',1,'HIGH_BELOW_OHLC'),('Low',1000,'LOW_ABOVE_OHLC'),
 ('Close',0,'NON_POSITIVE_PRICE'),('Volume',-1,'NEGATIVE_VOLUME'),
 ('Open',float('nan'),'NON_FINITE_OHLCV')])
def test_each_failed_predicate_reports_exact_row_without_repair(field,value,rule):
    history=frame(np.linspace(200,100,497));stamp=history.index[-1]
    history.loc[stamp,field]=value
    original=history.copy(deep=True)
    result=technical(history,CFG,NOW)
    assert result['reason_codes']==['HISTORY_INVALID']
    failure=next(v for v in result['history_quality']['violations'] if v['rule']==rule)
    assert failure['count']==1 and failure['samples'][0]['timestamp']==str(stamp)
    pd.testing.assert_frame_equal(history,original)


def test_invalid_fresh_cache_is_refetched_and_only_valid_source_replaces_it(tmp_path,monkeypatch):
    adapter=RepositoryAdapter(SimpleNamespace(provider=object()),CFG);adapter.now=NOW
    monkeypatch.chdir(tmp_path)
    folder=tmp_path/'.cache/futures_workspace_adjusted';folder.mkdir(parents=True)
    path=folder/(hashlib.sha256(b'BIOCON').hexdigest()+'.parquet')
    bad=frame(np.linspace(200,100,497));bad.loc[bad.index[-1],'High']=1;bad.to_parquet(path)
    good=frame(np.linspace(200,100,497))
    with patch('src.providers.yahoo_provider.YahooProvider.get_historical_data',return_value=good) as fetch:
        result=adapter.adjusted_history('BIOCON')
    fetch.assert_called_once_with('BIOCON',period='2y',interval='1d')
    assert not ohlcv_diagnostics(result)['violations']
    assert pd.read_parquet(path).High.iloc[-1]!=1


def test_invalid_refetch_remains_rejected_and_preserves_original_prices(tmp_path,monkeypatch):
    adapter=RepositoryAdapter(SimpleNamespace(provider=object()),CFG);adapter.now=NOW
    monkeypatch.chdir(tmp_path)
    bad=frame(np.linspace(200,100,497));bad.loc[bad.index[-1],'High']=1
    with patch('src.providers.yahoo_provider.YahooProvider.get_historical_data',return_value=bad):
        with pytest.raises(HistoryValidationError) as caught:
            adapter.adjusted_history('BIOCON')
    assert caught.value.details['violations'][0]['rule']=='HIGH_BELOW_OHLC'
    assert bad.High.iloc[-1]==1
    assert not list((tmp_path/'.cache/futures_workspace_adjusted').glob('*.parquet'))


def test_weekly_pharma_context_uses_existing_yahoo_index_mapping():
    provider=SimpleNamespace(get_data=Mock(side_effect=AssertionError('No broker authentication for Yahoo sector research')))
    adapter=RepositoryAdapter(SimpleNamespace(provider=provider),CFG)
    adapter.history=Mock(return_value=frame(np.linspace(200,100,497)))
    assert adapter.sector('BIOCON')=='PHARMA'
    adapter.sector_history('BIOCON')
    adapter.history.assert_called_once_with('^CNXPHARMA')
    provider.get_data.assert_not_called()


def test_new_broker_clients_use_updated_environment_without_changing_old_clients(monkeypatch):
    from src.providers.kite_provider import KiteProvider
    with patch('src.providers.kite_provider.KiteConnect') as factory:
        monkeypatch.setenv('KITE_API_KEY','test-key-one');monkeypatch.setenv('KITE_ACCESS_TOKEN','test-token-one')
        KiteProvider()
        factory.assert_called_with(api_key='test-key-one')
        factory.return_value.set_access_token.assert_called_with('test-token-one')
        monkeypatch.setenv('KITE_API_KEY','test-key-two');monkeypatch.setenv('KITE_ACCESS_TOKEN','test-token-two')
        KiteProvider()
        factory.assert_called_with(api_key='test-key-two')
        factory.return_value.set_access_token.assert_called_with('test-token-two')


def test_recorded_real_biocon_snapshot_validates_and_scores_bearish_90():
    from pathlib import Path
    import json,hashlib
    folder=Path(__file__).resolve().parents[1]/'reports/investigations/biocon_2026_10_10'
    provenance=json.loads((folder/'provenance.json').read_text())
    for name,digest in provenance['files'].items():
        assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest
    history=pd.read_parquet(folder/'BIOCON.parquet')
    result=technical(history,CFG,pd.Timestamp('2026-10-10 16:00',tz='Asia/Kolkata'),
                     pd.read_parquet(folder/'NSEI.parquet'),pd.read_parquet(folder/'CNXPHARMA.parquet'))
    assert len(history)==497
    assert result['classification']=='SHORTING_STOCKS'
    assert result['bearish_score']==90 and result['technical_score']==90
    assert result['metrics']['bearish_confirmation']
    assert result['metrics']['history_as_of']=='2026-10-09T00:00:00+05:30'
    assert result['bearish_score']>=CFG.minimum_score
