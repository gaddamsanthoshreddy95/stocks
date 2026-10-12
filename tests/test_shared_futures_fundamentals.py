from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from src.ui.futures_history import fetch_missing_fundamentals
from src.ui.stock_tables import context_columns


def test_real_provider_values_fill_both_directions_and_reports_once_without_changing_decisions():
    rows=[{'symbol':'HINDPETRO','side':side,'technical_score':80,'final_decision':'UNKNOWN',
           'company_research':{'fundamentals':None}} for side in ('LONG','SHORT')]
    original={'reviewed':rows,'report_b':[deepcopy(rows[1])],'generated_at':'2026-10-12T10:00:00+05:30'}
    before=deepcopy(original)
    provider=Mock();provider.get_fundamentals.return_value={'symbol':'HINDPETRO','pe_ratio':12,'sector_pe':15,
        'roe':20,'roce':18,'source':'Verified test source','as_of':'2026-10-12'}
    result=fetch_missing_fundamentals(original,provider)
    provider.get_fundamentals.assert_called_once_with('HINDPETRO')
    assert original==before
    for item in result['reviewed']+result['report_b']:
        context=context_columns(item)
        assert context['P/E']==12 and context['Sector P/E']==15
        assert context['ROE %']==20 and context['ROCE %']==18
        assert context['Fundamental snapshot']=='Supplemental company data · display only'
        assert context['Supplemental fundamentals fetched']
        assert item['technical_score']==80 and item['final_decision']=='UNKNOWN'
        assert item['company_research']['fundamentals'] is None


def test_saved_own_values_are_not_replaced_and_wrong_company_rejected():
    provider=Mock();provider.get_fundamentals.return_value={'symbol':'OTHER','pe_ratio':99,'sector_pe':99}
    row={'symbol':'HINDPETRO','company_research':{'fundamentals':{'pe_ratio':0}}}
    result=fetch_missing_fundamentals({'reviewed':[row]},provider)
    assert result['reviewed'][0]['display_fundamentals']['status']=='UNAVAILABLE'
    assert context_columns(result['reviewed'][0])['P/E']==0
    assert context_columns(result['reviewed'][0])['Sector P/E'] is None


def test_provider_failures_are_recorded_without_zero_or_fake_values():
    provider=Mock();provider.get_fundamentals.side_effect=RuntimeError('offline')
    row=fetch_missing_fundamentals({'reviewed':[{'symbol':'HINDPETRO'}]},provider)['reviewed'][0]
    assert row['display_fundamentals']['error_type']=='RuntimeError'
    assert context_columns(row)['P/E'] is None


def test_futures_trading_saved_actual_facts_are_shared_without_another_fetch(tmp_path):
    from src.futures_workspace.store import WorkspaceStore
    store=WorkspaceStore(tmp_path/'shared.db')
    raw={'symbol':'HINDPETRO','pe_ratio':12,'sector_pe':15,'roe':20,'roce':18,'source':'Recorded public provider','as_of':'2026-10-12'}
    with store.job('WEEKLY_ROTATION') as (job_id,output):
        store.record(job_id,'HINDPETRO','FUNDAMENTAL',{'snapshot':raw})
    provider=Mock(side_effect=AssertionError('No duplicate fetch'))
    provider.get_fundamentals.side_effect=AssertionError('No duplicate fetch')
    result=fetch_missing_fundamentals({'reviewed':[{'symbol':'HINDPETRO'}]},provider,database=store)
    context=context_columns(result['reviewed'][0])
    assert context['P/E']==12 and context['Sector P/E']==15
    assert context['Supplemental data origin']=='SAVED_WEEKLY_FUNDAMENTALS'
    provider.get_fundamentals.assert_not_called()


def test_partial_refresh_keeps_existing_weekly_pe_with_separate_provenance(tmp_path):
    from src.futures_workspace.store import WorkspaceStore
    store=WorkspaceStore(tmp_path/'shared.db')
    with store.job('WEEKLY_ROTATION') as (job_id,output):
        store.record(job_id,'HINDPETRO','FUNDAMENTAL',{'snapshot':{'symbol':'HINDPETRO','pe_ratio':12,
            'sector_pe':15,'source':'Earlier public source','as_of':'2026-10-11'}})
    provider=Mock();provider.get_fundamentals.return_value={'symbol':'HINDPETRO','roe':20,'source':'Current source','as_of':'2026-10-12'}
    result=fetch_missing_fundamentals({'reviewed':[{'symbol':'HINDPETRO'}]},provider,database=store)
    row=result['reviewed'][0]
    assert context_columns(row)['P/E']==12 and context_columns(row)['ROE %']==20
    assert row['display_fundamentals']['field_provenance']['pe_ratio']['as_of']=='2026-10-11'
