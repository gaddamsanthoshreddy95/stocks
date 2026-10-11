"""Verified identities, exact benchmarks and uncertainty preserve original points."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import pandas as pd
import pytest
from src.futures_workspace.sectors import SectorResolver,benchmark_quality
from src.futures_workspace.ranking import evidence_score,research_selection,shadow_comparison
from src.futures_workspace.adapter import RepositoryAdapter
from src.futures_workspace.discovery import technical
from test_futures_workspace import CFG,NOW,frame,evaluation,LISTS


def test_nse_symbol_isin_matching_and_verified_cache(tmp_path):
    def fetch(url):
        if 'EQUITY_L.csv' in url:return 'SYMBOL,ISIN NUMBER\nNTPC,INE733E01010\n'
        return json.dumps({'info':{'symbol':'NTPC','isin':'INE733E01010'},'industryInfo':{'basicIndustry':'Power Generation'}})
    source=Mock(side_effect=fetch)
    resolver=SectorResolver({'NTPC':'POWER'},source,tmp_path)
    result=resolver.resolve('NTPC',NOW)
    assert result['status']=='VERIFIED' and result['sector']=='POWER' and result['isin']=='INE733E01010'
    resolver.resolve('NTPC',NOW)
    assert source.call_count==2


def test_moneycontrol_fallback_requires_matching_symbol_and_isin(tmp_path):
    def fetch(url):
        if 'EQUITY_L.csv' in url:return 'SYMBOL,ISIN NUMBER\nNTPC,INE733E01010\n'
        if 'quote-equity' in url:raise RuntimeError('NSE unavailable')
        if 'autosuggestion' in url:return json.dumps([{'pdt_dis_nm':'<span>NTPC</span>','link_src':'https://www.moneycontrol.com/ntpc'}])
        return '{"nseId":"NTPC","isinid":"INE733E01010","sector":"Power Generation/Distribution"}'
    result=SectorResolver({},fetch,tmp_path).resolve('NTPC',NOW)
    assert result['status']=='VERIFIED' and result['source']=='MONEYCONTROL'
    def wrong(url):return fetch(url).replace('INE733E01010','INE000A00000') if 'moneycontrol.com/ntpc' in url else fetch(url)
    assert SectorResolver({},wrong,tmp_path/'wrong').resolve('NTPC',NOW)['status']=='UNKNOWN'


def test_missing_sector_never_defaults_to_diversified_and_stale_is_dated(tmp_path):
    resolver=SectorResolver({},Mock(side_effect=RuntimeError('offline')),tmp_path)
    assert resolver.resolve('UNMAPPED',NOW)['sector']=='UNKNOWN'
    # An old verified identity may be displayed, but not treated as fresh.
    import hashlib
    (tmp_path/(hashlib.sha256(b'NTPC').hexdigest()+'.json')).write_text(json.dumps({'sector':'POWER','status':'VERIFIED','verified_at':'2025-01-01T00:00:00+05:30'}))
    result=resolver.resolve('NTPC',NOW)
    assert result['status']=='STALE_CONTEXT_ONLY'


def test_benchmark_rejects_one_row_stale_gaps_and_invalid_prices():
    good=frame(np.linspace(200,100,60))
    assert len(benchmark_quality(good,NOW))==60
    with pytest.raises(ValueError,match='INSUFFICIENT'):benchmark_quality(good.tail(1),NOW)
    with pytest.raises(ValueError,match='STALE'):benchmark_quality(good,NOW+pd.Timedelta(days=10))
    with pytest.raises(ValueError,match='MISSING_REQUIRED'):benchmark_quality(good.drop(good.index[-10]),NOW,calendar=good)
    bad=good.copy();bad.loc[bad.index[-1],'High']=1
    with pytest.raises(ValueError,match='HIGH_BELOW'):benchmark_quality(bad,NOW)


def test_exact_index_fallback_uses_real_broker_data_and_labels_proxy():
    good=frame(np.linspace(200,100,280))
    provider=SimpleNamespace(provider=SimpleNamespace(get_historical_data=Mock(return_value=good)))
    adapter=RepositoryAdapter(SimpleNamespace(provider=provider),CFG);adapter.now=NOW
    adapter.sector_evidence['NTPC']={'sector':'POWER','status':'VERIFIED'}
    assert len(adapter.sector_history('NTPC'))==280
    assert adapter.benchmark_evidence['NTPC']['index']=='NIFTY POWER'
    provider.provider.get_historical_data.assert_called_with('NIFTY POWER',period='2y')
    adapter.sector_evidence['POWER2']={'sector':'POWER','status':'VERIFIED'}
    assert len(adapter.sector_history('POWER2'))==280
    assert provider.provider.get_historical_data.call_count==1
    adapter.sector_evidence['TEST']={'sector':'ASSET_MANAGEMENT','status':'VERIFIED'}
    assert adapter.sector_history('TEST') is None
    assert adapter.benchmark_evidence['TEST']['status']=='PROXY_CONTEXT_ONLY'


def test_failed_sector_recovery_preserves_healthy_stock_history():
    adapter=RepositoryAdapter(SimpleNamespace(provider=SimpleNamespace()),CFG)
    good=frame(np.linspace(200,100,280))
    adapter.history_cache={'NTPC':good,'OTHER':good,'^CNXENERGY':good}
    adapter.sector_evidence['NTPC']={'sector':'POWER','status':'VERIFIED'}
    adapter._sector_frames={'NIFTY POWER':(good,'KITE'),'NIFTY ENERGY':(good,'KITE')}
    adapter._index_archive_frames={'healthy':good,'failed':None}
    adapter.recover_sector('NTPC',NOW)
    assert 'NIFTY POWER' not in adapter._sector_frames
    assert 'NIFTY ENERGY' in adapter._sector_frames
    assert set(adapter.history_cache)=={'NTPC','OTHER','^CNXENERGY'}
    assert set(adapter._index_archive_frames)=={'healthy'}


def test_new_manual_job_revalidates_benchmark_and_reopens_source_circuit():
    adapter=RepositoryAdapter(SimpleNamespace(provider=SimpleNamespace()),CFG)
    adapter._sector_frames={'NIFTY POWER':('previous frame','KITE')}
    adapter.sector_resolver.blocked={'www.nseindia.com'}
    adapter.sector_resolver.isins={'OLD':'ISIN'}
    adapter.begin(NOW)
    assert not adapter._sector_frames and not adapter.sector_resolver.blocked
    assert adapter.sector_resolver.isins is None


def test_conflicting_verified_identity_requires_review(tmp_path):
    import hashlib
    (tmp_path/(hashlib.sha256(b'NTPC').hexdigest()+'.json')).write_text(json.dumps({
        'sector':'ENERGY','status':'VERIFIED','verified_at':'2025-01-01T00:00:00+05:30'}))
    def fetch(url):
        if 'EQUITY_L.csv' in url:return 'SYMBOL,ISIN NUMBER\nNTPC,INE733E01010\n'
        return json.dumps({'info':{'symbol':'NTPC','isin':'INE733E01010'},'industryInfo':{'basicIndustry':'Power Generation'}})
    result=SectorResolver({},fetch,tmp_path).resolve('NTPC',NOW)
    assert result['status']=='REVIEW_REQUIRED' and result['conflicting_verified_sector']=='ENERGY'


def test_three_state_bounds_do_not_fabricate_missing_points():
    result=evidence_score([True]*8+[False,None],[str(i) for i in range(10)])
    assert result['confirmed_score']==80 and result['possible_score']==90
    assert result['coverage_percent']==90 and result['observed_condition_score']==pytest.approx(88.888888)
    assert result['conditions']['9']=='UNKNOWN'
    data=frame(np.linspace(200,100,280))
    result=technical(data,CFG,NOW)
    assert result['bearish_score']==result['score_evidence']['bearish']['confirmed_score']


def test_boundary_ties_and_uncertain_candidates_get_review_without_forced_admission():
    values={s:{**evaluation(LISTS[0],80),'symbol':s,'metrics':{'return_20d_percent':-i},
               'score_evidence':{'bearish':{'confirmed_score':80,'possible_score':90,'coverage_percent':90}}}
            for i,s in enumerate(['AAA','NTPC','ZZZ'],1)}
    selected,review=research_selection(values,2,3)
    assert selected==set(values) # all boundary ties fit resource budget
    selected,review=research_selection(values,2,2)
    assert 'ZZZ' in selected and 'NTPC' in selected # measured weakness, not alphabet
    assert any(r['reason']=='BOUNDARY_TIE_EXCEEDS_CONTEXT_BUDGET' for r in review)
    shadow=shadow_comparison(values,2)
    assert not shadow['out_of_sample_validated'] and shadow['mode']=='REPORT_ONLY'
