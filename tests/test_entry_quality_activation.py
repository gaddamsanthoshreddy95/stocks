"""Active-default regressions; legacy engine tests separately exercise REPORT_ONLY compatibility."""
from copy import deepcopy
from dataclasses import replace
import pandas as pd
import pytest
from test_entry_quality import sample,NOW
from src.futures.entry_quality import EntryQualityConfig,refresh_report
from src.futures.scanner import rank_key


def report(side='LONG',late=False):
    s=sample(side)
    if late:s['trigger']+=-.5 if side=='LONG' else .5
    item={'symbol':'SYNTHETIC','side':side,'final_decision':'APPROVED','technical_score':80,
        'entry_quality_input':s,'reason_codes':[],'scan_gates':{'executed_entry_budget':{'status':'PASS'}},
        side.lower()+'_entry':{'state':'VALID_'+side+'_ENTRY','execution_approved':True},
        'futures_execution_confirmation':{'entry_ready':True,'final_decision':'APPROVED'}}
    return {'reviewed':[item],'report_a':[item],'report_b':[],'report_c':[item],'approved_count':1,
        'entry_quality_shortlist_limit':1}


def test_enforcement_is_the_default_when_no_override_exists(monkeypatch):
    monkeypatch.delenv('FUTURES_ENTRY_QUALITY_MODE',raising=False)
    assert EntryQualityConfig().mode=='ENFORCE'
    assert EntryQualityConfig.from_env().mode=='ENFORCE'
    assert EntryQualityConfig.from_env().require_15m


def test_explicit_report_only_override_remains_available(monkeypatch):
    monkeypatch.setenv('FUTURES_ENTRY_QUALITY_MODE','REPORT_ONLY')
    r=report(late=True);refresh_report(r,NOW)
    assert r['reviewed'][0]['final_decision']=='APPROVED'
    assert r['entry_quality_summary']['production_behavior']=='UNCHANGED'


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('late',[False,True])
def test_default_policy_preserves_valid_entry_or_withdraws_chasing(side,late,monkeypatch):
    monkeypatch.delenv('FUTURES_ENTRY_QUALITY_MODE',raising=False)
    r=report(side,late);item=r['reviewed'][0];before=rank_key(item)
    refresh_report(r,NOW)
    assert item['final_decision']==('WAIT' if late else 'APPROVED')
    assert item['entry_quality']['classification']==('WAIT FOR PULLBACK' if late else 'ENTER')
    assert item['futures_execution_confirmation']['entry_ready']==(not late)
    assert rank_key(item)==before
    if late:
        assert item[side.lower()+'_entry']['state']=='WAIT_FOR_PULLBACK'
        assert item[side.lower()+'_entry']['execution_approved'] is False
    assert len(r['report_c'])==(0 if late else 1)


def test_repeated_finalization_is_idempotent_and_retains_original_evidence():
    r=report(late=True);item=r['reviewed'][0]
    refresh_report(r,NOW,EntryQualityConfig())
    first=deepcopy(item['entry_quality']);inputs=deepcopy(item['entry_quality_input'])
    refresh_report(r,NOW,EntryQualityConfig())
    assert item['entry_quality']==first
    assert item['entry_quality_input']==inputs
    assert r['entry_quality_summary']['changed_decisions']==1
    assert item['entry_quality_input']['original_decision']=='APPROVED'


def test_later_ledger_failure_is_not_hidden_by_an_earlier_quality_withdrawal():
    r=report(late=True);item=r['reviewed'][0];refresh_report(r,NOW,EntryQualityConfig())
    item['scan_gates']['executed_entry_budget']={'status':'FAIL'}
    item['reason_codes'].append('DAILY_EXECUTED_ENTRY_LIMIT_REACHED')
    refresh_report(r,NOW,EntryQualityConfig())
    assert item['entry_quality']['classification']=='SKIP'
    assert item['final_decision']!='APPROVED'
    assert item['entry_quality_input']['original_decision']=='WAIT'


def test_refreshing_a_withdrawn_report_never_creates_approval():
    r=report(late=True);item=r['reviewed'][0];refresh_report(r,NOW,EntryQualityConfig())
    item['entry_quality_input']['trigger']=100.99
    refresh_report(r,NOW,EntryQualityConfig())
    assert item['final_decision']=='WAIT'
    assert item['entry_quality']['classification']=='WAIT FOR PULLBACK'
    assert 'NEW_MANUAL_SCAN_APPROVAL_REQUIRED' in item['entry_quality']['reason_codes']


@pytest.mark.parametrize('clock',['09:30','09:45','10:00'])
def test_early_entries_cannot_pass_without_completed_fifteen_minute_context(clock):
    r=report();now=NOW.replace(hour=int(clock[:2]),minute=int(clock[-2:]))
    refresh_report(r,now,EntryQualityConfig())
    assert r['approved_count']==0
    assert r['reviewed'][0]['entry_quality']['classification']!='ENTER'


def test_default_scanner_pipeline_withholds_missing_context_without_orders(monkeypatch):
    from futures_mode_fixture import fixture,NOW as when
    from unittest.mock import patch
    monkeypatch.delenv('FUTURES_ENTRY_QUALITY_MODE',raising=False)
    scanner,provider=fixture(['SBIN'])
    with patch('src.futures.gateway.sleep'):
        r=scanner.scan(now=when,include_backtest=False)
    assert r['entry_quality_summary']['mode']=='ENFORCE'
    assert all(item.get('entry_quality',{}).get('mode')=='ENFORCE' for item in r['reviewed'])
    assert all(i['entry_quality']['classification']=='ENTER' for i in r['report_c'])
    provider.provider.kite.place_order.assert_not_called()


def test_documented_settings_enable_the_same_active_policy(monkeypatch):
    from pathlib import Path
    from dotenv import dotenv_values
    example=dotenv_values(Path(__file__).resolve().parents[1]/'.env.example',interpolate=False)
    for key,value in example.items():
        if key.startswith('FUTURES_ENTRY_QUALITY_'):monkeypatch.setenv(key,value)
    policy=EntryQualityConfig.from_env()
    assert policy.mode=='ENFORCE' and policy.require_15m
    assert policy.level_bars_15m==2 and policy.quote_age_seconds==30


def test_shared_gate_rechecks_available_news_independently_of_legacy_switch():
    from src.futures.entry_quality import snapshot
    from src.futures.config import FuturesScanConfig
    from test_entry_quality import bars
    s=sample();item={'symbol':'SYNTHETIC','side':'LONG','final_decision':'APPROVED',
        'news':{'news_state':'NO_RELEVANT_NEWS','checked_at':NOW.isoformat()}}
    data={'underlying_intraday':bars(),'intraday':bars()}
    original=deepcopy(item)
    for mode in ('REPORT_ONLY','ENFORCE'):
        captured=snapshot(item,data,NOW,EntryQualityConfig(mode=mode),FuturesScanConfig(news_conflict_gate_enabled=0))
        assert captured['news_status']=='CLEAR'
        assert captured['news_gate_checked_at']==NOW.isoformat()
        assert item==original
    item['news']['news_state']='FETCH_FAILED'
    captured=snapshot(item,data,NOW)
    assert captured['news_status']=='UNAVAILABLE'
