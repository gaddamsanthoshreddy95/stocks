from dataclasses import replace
from types import SimpleNamespace
import math
import pytest

from src.futures.config import FuturesScanConfig
from src.futures.oi_ranking import apply_oi_ranking
from src.futures.scanner import rank_key


def sample(side='LONG',pc=1,oc=5):
    item={'side':side,'technical_score':80,'technical_score_timeframe':'COMPLETED_FUTURES_5_MINUTE','final_decision':'WAIT'}
    data={'instrument_token':42,'quote':{'instrument_token':42,'timestamp':'2026-10-15T10:00:00+05:30'}}
    check=SimpleNamespace(status='PASS',factors={'price_change_percent':pc,'oi_change_percent':oc,
        'current_oi':105000,'previous_session_oi':100000,'previous_session_close':100,
        'quote_age_seconds':1,'baseline_timestamp':'2026-10-14T00:00:00+05:30'})
    return item,data,check


@pytest.mark.parametrize('side,pc,oc,regime,score',[
    ('LONG',1,5,'LONG_BUILDUP',100),('SHORT',-1,5,'SHORT_BUILDUP',100),
    ('SHORT',1,5,'LONG_BUILDUP',0),('LONG',-1,5,'SHORT_BUILDUP',0),
    ('LONG',1,-5,'SHORT_COVERING',50),('SHORT',-1,-5,'LONG_UNWINDING',50),
    ('SHORT',1,-5,'SHORT_COVERING',0),('LONG',-1,-5,'LONG_UNWINDING',0),
    ('LONG',.01,5,'UNCHANGED_OR_SMALL_CHANGE',50),('SHORT',-1,0,'UNCHANGED_OR_SMALL_CHANGE',50)])
def test_regime_direction_and_ranking_without_altering_approval(side,pc,oc,regime,score):
    item,data,check=sample(side,pc,oc)
    apply_oi_ranking(item,data,check,FuturesScanConfig())
    assert item['oi_ranking']['regime']==regime
    assert item['oi_ranking']['score']==score
    assert item['daily_ranking_score']==round(.9*80+.1*score,2)
    assert item['technical_score']==80 and item['final_decision']=='WAIT'
    apply_oi_ranking(item,data,check,FuturesScanConfig())
    assert item['daily_ranking_score']==round(.9*80+.1*score,2)


@pytest.mark.parametrize('failure',['missing','stale','nan','zero','identity','daily','baseline'])
def test_unverified_data_does_not_become_zero_or_neutral_score(failure):
    item,data,check=sample()
    if failure=='missing':check.status='UNKNOWN'
    if failure=='stale':check.factors['quote_age_seconds']=999
    if failure=='nan':check.factors['oi_change_percent']=math.nan
    if failure=='zero':check.factors['previous_session_oi']=0
    if failure=='identity':data['quote']['instrument_token']=43
    if failure=='daily':item['technical_score_timeframe']='DAILY_UNDERLYING_RESEARCH'
    if failure=='baseline':check.factors['baseline_timestamp']=None
    apply_oi_ranking(item,data,check,FuturesScanConfig())
    assert item['oi_ranking']['status']=='UNAVAILABLE'
    assert item['oi_ranking']['score'] is None
    assert item['daily_ranking_score']==80


def test_absolute_oi_does_not_boost_score_and_weight_can_be_disabled():
    item,data,check=sample()
    apply_oi_ranking(item,data,check,FuturesScanConfig())
    first=item['daily_ranking_score']
    check.factors.update(current_oi=105000000,previous_session_oi=100000000)
    apply_oi_ranking(item,data,check,FuturesScanConfig())
    assert item['daily_ranking_score']==first
    apply_oi_ranking(item,data,check,replace(FuturesScanConfig(),oi_ranking_weight=0))
    assert item['daily_ranking_score']==80 and item['oi_ranking']['status']=='DISABLED'


def test_ranking_changes_without_overriding_decision_priority():
    weaker,da,ca=sample('SHORT',-1,5)
    stronger,db,cb=sample('SHORT',1,5)
    weaker['technical_score']=79;stronger['technical_score']=81
    for i,d,c in ((weaker,da,ca),(stronger,db,cb)):apply_oi_ranking(i,d,c,FuturesScanConfig())
    assert rank_key(weaker)>rank_key(stronger)
    stronger['final_decision']='APPROVED'
    assert rank_key(stronger)>rank_key(weaker)


def test_shared_scanner_applies_overlay_and_retains_original_price_score():
    from futures_mode_fixture import fixture,NOW
    scanner,_=fixture(['SBIN'])
    report=scanner.scan(now=NOW,include_backtest=False)
    reviewed=[i for i in report['reviewed'] if i.get('execution_reviewed')]
    assert reviewed
    assert any(i.get('oi_ranking',{}).get('status')=='AVAILABLE' for i in reviewed)
    for item in reviewed:
        assert item['technical_score']==item['futures_setup']['technical_score']
        assert item['daily_ranking_score'] is not None


@pytest.mark.parametrize('changes',[{'oi_ranking_weight':1.1},{'oi_minimum_change_percent':0},{'oi_full_strength_change_percent':.01}])
def test_invalid_config(changes):
    with pytest.raises(ValueError):replace(FuturesScanConfig(),**changes)
