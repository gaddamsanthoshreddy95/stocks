"""Synthetic bars test policy only; no fabricated historical HINDPETRO outcome."""
from dataclasses import replace
from pathlib import Path
import json
import numpy as np
import pandas as pd
import pytest
from src.futures.config import FuturesScanConfig
from src.futures.news_conflict import evaluate,shortlist,research_policy
from src.futures.scanner import FuturesOpportunityScanner

CFG=replace(FuturesScanConfig(),news_conflict_gate_enabled=1)
NOW=pd.Timestamp('2026-10-12 10:45',tz='Asia/Kolkata')


def bars(up=True):
    index=pd.date_range('2026-10-12 09:15',periods=18,freq='5min',tz='Asia/Kolkata')
    close=np.array([100.]*16+[101.,102.])
    if not up:close=200-close
    volume=np.array([100.]*16+[200.,200.])
    return pd.DataFrame({'Open':close,'High':close+.1,'Low':close-.1,'Close':close,'Volume':volume},index=index)


def article(**changes):
    # Independently attested event schema; intentionally a synthetic policy fixture.
    return {'title':'Synthetic HINDPETRO company catalyst','url':'https://www.nseindia.com/event/example',
        'published':'2026-10-12T10:00:00+05:30','available_at':'2026-10-12T10:00:00+05:30',
        'assessment_at':'2026-10-12T10:00:00+05:30',
        'source_verified':True,'company_specific':True,'company_symbols':['HINDPETRO'],
        'materiality_verified':True,'materiality':'HIGH','sentiment':'BULLISH',
        'verification_basis':'TEST_ONLY_INDEPENDENT_EVENT_ATTESTATION',**changes}


def news(*articles,**changes):
    return {'news_state':'ANALYZED','checked_at':NOW.isoformat(),'article_assessments':list(articles),**changes}


def test_positive_material_event_plus_bullish_confirmation_excludes_short_and_refills():
    gate=evaluate('HINDPETRO','SHORT',news(article()),bars(),NOW,CFG)
    assert gate['excluded'] and gate['confirmations'][0]['market']['status']=='CONFIRMED'
    ranked=[{'symbol':'HINDPETRO','news_conflict_gate':gate}]+[{'symbol':str(i)} for i in range(6)]
    assert [i['symbol'] for i in shortlist(ranked,5)]==['0','1','2','3','4']


def test_negative_material_event_plus_bearish_confirmation_excludes_long():
    result=evaluate('HINDPETRO','LONG',news(article(sentiment='BEARISH')),bars(False),NOW,CFG)
    assert result['excluded']
    assert 'MATERIAL_NEGATIVE_NEWS_BEARISH_REVERSAL_EXCLUDES_LONG' in result['reason_codes']


@pytest.mark.parametrize('changes',[
    {'source_verified':False},{'url':'https://reuters.com.evil.test/article'},
    {'company_specific':False},{'company_symbols':['OTHER']},{'materiality_verified':False},
    {'materiality':'LOW'},{'published':'2026-10-10T10:00:00+05:30'},
    {'published':'2026-10-12T11:00:00+05:30'},
    {'available_at':'2026-10-12T11:00:00+05:30'},
    {'assessment_at':'2026-10-12T11:00:00+05:30'},
    {'published':'2026-10-12T10:00:00'},
    {'assessment_at':None},
    {'available_at':None},
])
def test_unverified_stale_future_macro_or_immaterial_news_cannot_exclude(changes):
    assert not evaluate('HINDPETRO','SHORT',news(article(**changes)),bars(),NOW,CFG)['excluded']


def test_headline_without_market_confirmation_cannot_exclude():
    assert not evaluate('HINDPETRO','SHORT',news(article()),bars(False),NOW,CFG)['excluded']
    assert evaluate('HINDPETRO','SHORT',news(article()),None,NOW,CFG)['status']=='UNAVAILABLE'


def test_stale_gapped_and_incomplete_price_bars_cannot_confirm():
    assert not evaluate('HINDPETRO','SHORT',news(article()),bars(),NOW+pd.Timedelta(minutes=20),CFG)['excluded']
    incomplete=bars();incomplete.index=incomplete.index+pd.Timedelta(minutes=10)
    assert not evaluate('HINDPETRO','SHORT',news(article()),incomplete,NOW,CFG)['excluded']
    gap=bars().drop(bars().index[-2])
    assert not evaluate('HINDPETRO','SHORT',news(article()),gap,NOW,CFG)['excluded']


def test_duplicates_are_not_counted_as_multiple_events():
    payload=news(article(),article(url='https://www.nseindia.com/event/example?utm_source=duplicate'))
    result=evaluate('HINDPETRO','SHORT',payload,bars(),NOW,CFG)
    assert len(result['news']['events'])==1
    assert result['news']['discarded'][0]['reason']=='DUPLICATE_NEWS'


def test_unavailable_and_confirmed_neutral_are_distinct():
    missing=evaluate('HINDPETRO','SHORT',news(news_state='FETCH_FAILED'),bars(),NOW,CFG)
    neutral=evaluate('HINDPETRO','SHORT',news(article(sentiment='NEUTRAL')),bars(),NOW,CFG)
    assert missing['status']=='UNAVAILABLE'
    assert neutral['news']['status']=='VERIFIED_NEUTRAL' and neutral['status']=='CLEAR'
    failed=evaluate('HINDPETRO','SHORT',news(news_state='NO_RELEVANT_NEWS',fetch_failed=True),bars(),NOW,CFG)
    assert failed['status']=='UNAVAILABLE'


def test_hindpetro_real_release_not_known_before_1030_and_not_inferred_positive_after():
    event=json.loads((Path(__file__).parent/'fixtures/hindpetro_news_2026_10_12.json').read_text())
    before=pd.Timestamp('2026-10-12 10:25',tz='Asia/Kolkata')
    result=evaluate('HINDPETRO','SHORT',news(event,checked_at=before.isoformat()),bars(),before,CFG)
    assert not result['excluded']
    assert result['news']['discarded'][0]['reason']=='NOT_AVAILABLE_AT_DECISION'
    after=evaluate('HINDPETRO','SHORT',news(event),bars(),NOW,CFG)
    assert not after['excluded'] and after['status']=='UNAVAILABLE'


def test_shadow_default_preserves_behavior_and_weights():
    old=FuturesScanConfig()
    assert old.news_conflict_gate_enabled==0
    result=evaluate('HINDPETRO','SHORT',news(article()),bars(),NOW,old)
    assert not result['excluded']
    assert old.weights('bearish')==CFG.weights('bearish') and old.weights('bullish')==CFG.weights('bullish')


def test_gate_annotation_changes_decision_but_not_scores_and_records_exclusion():
    scanner=FuturesOpportunityScanner.__new__(FuturesOpportunityScanner);scanner.config=CFG
    item={'symbol':'HINDPETRO','side':'SHORT','technical_score':91,'final_decision':'APPROVED','reason_codes':[]}
    scanner._news_conflict(item,news(article()),bars(),NOW)
    assert item['final_decision']=='REJECT' and item['technical_score']==91
    assert item['news_conflict_gate']['excluded'] and item['reason_codes']


def test_only_headline_gate_replaced_other_research_failures_preserved():
    research={'checks':{'recent_news_quality':{'status':'FAIL'}},'failed_checks':['recent_news_quality','valuation_quality'],
              'unavailable_checks':[],'review_flags':[{'check':'recent_news_quality'},{'check':'valuation_quality'}],
              'eligible':False,'policy_review_required':True}
    changed=research_policy(research,{'enabled':True})
    assert changed['failed_checks']==['valuation_quality'] and not changed['eligible']
    assert changed['legacy_news_check']=={'status':'FAIL'}
    assert research['failed_checks']==['recent_news_quality','valuation_quality']


def test_enabled_scanner_filters_conflict_before_report_selection_without_orders():
    from test_bidirectional_futures import scanner_fixture
    scanner,provider=scanner_fixture();scanner.config=CFG
    provider.get_session_intraday.return_value=bars()
    scanner.news_provider=lambda symbol:news(article(company_symbols=['DOWN'])) if symbol=='DOWN' else news(news_state='NO_RELEVANT_NEWS')
    report=scanner.scan(5,now=NOW,include_backtest=False)
    assert any(i['symbol']=='DOWN' and i['side']=='SHORT' for i in report['news_conflict_exclusions'])
    assert not any(i['symbol']=='DOWN' for i in report['report_b'])
    assert any(i['symbol']=='DOWN' and i['side']=='SHORT' for i in report['reviewed'])
    provider.kite.place_order.assert_not_called()


def test_macro_usdinr_context_is_not_company_specific_material_news():
    result=evaluate('HINDPETRO','SHORT',news(article(title='Synthetic USD/INR macro update',company_specific=False)),bars(),NOW,CFG)
    assert not result['excluded']
    assert result['news']['status']=='UNAVAILABLE'


def test_enabled_constructor_preserves_strict_session_cutoffs_and_weights():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.application.settings import PlatformSettings
    scanner=FuturesOpportunityScanner(SimpleNamespace(provider=Mock(),settings=PlatformSettings()),config=CFG,
        fundamental_provider=SimpleNamespace(get_fundamentals=Mock(return_value=None)))
    assert (scanner.config.entry_cutoff_hour,scanner.config.entry_cutoff_minute)==(15,0)
    assert (scanner.config.intraday_exit_hour,scanner.config.intraday_exit_minute)==(15,10)
    assert scanner.config.weights('bearish')==CFG.weights('bearish')
    assert scanner.config.weights('bullish')==CFG.weights('bullish')
