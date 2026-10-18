"""Common point-in-time Entry Quality enforcement. Pure evaluation; no market or broker calls."""
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass, asdict, replace
from math import isfinite
import os
import pandas as pd
from src.futures.sessions import ist, entry_window, normalise_candles, completed_session_check
from src.futures.retracement_15m import evaluate as retracement
from src.futures.config import FuturesScanConfig
from src.quality.futures_execution import clean

VERSION='ENTRY_QUALITY_V1'


@dataclass(frozen=True)
class EntryQualityConfig:
    mode: str='ENFORCE'
    quote_age_seconds: int=30
    max_extension_stop_units: float=1.5
    minimum_rvol: float=1.2
    maximum_slippage_bps: float=10
    require_15m: bool=True
    level_bars_15m: int=2
    retest_age_15m: int=2

    def __post_init__(self):
        if self.mode not in ('REPORT_ONLY','ENFORCE'):raise ValueError('Invalid entry quality mode')
        for name in ('quote_age_seconds','max_extension_stop_units','minimum_rvol','maximum_slippage_bps','level_bars_15m','retest_age_15m'):
            value=getattr(self,name)
            if not isfinite(value) or value<=0:raise ValueError('Invalid '+name)
        for name in ('quote_age_seconds','level_bars_15m','retest_age_15m'):
            if int(getattr(self,name))!=getattr(self,name):raise ValueError(name+' must be integral')
        if self.quote_age_seconds>30 or self.level_bars_15m<2:raise ValueError('Entry quality freshness/structure safeguard invalid')
        if type(self.require_15m) is not bool:raise ValueError('require_15m must be boolean')

    @classmethod
    def from_env(cls):
        prefix='FUTURES_ENTRY_QUALITY_'
        enabled=os.getenv(prefix+'REQUIRE_15M','true').lower()
        if enabled not in ('true','false','1','0'):raise ValueError('Invalid REQUIRE_15M')
        return cls(mode=os.getenv(prefix+'MODE','ENFORCE').upper(),
            quote_age_seconds=int(os.getenv(prefix+'QUOTE_AGE_SECONDS','30')),
            max_extension_stop_units=float(os.getenv(prefix+'MAX_EXTENSION_STOP_UNITS','1.5')),
            minimum_rvol=float(os.getenv(prefix+'MINIMUM_RVOL','1.2')),
            maximum_slippage_bps=float(os.getenv(prefix+'MAXIMUM_SLIPPAGE_BPS','10')),
            require_15m=enabled in ('true','1'),level_bars_15m=int(os.getenv(prefix+'LEVEL_BARS_15M','2')),
            retest_age_15m=int(os.getenv(prefix+'RETEST_AGE_15M','2')))


def number(value):
    try:
        value=float(value)
        return value if isfinite(value) else None
    except (TypeError,ValueError):return None


def rows(frame,now):
    try:
        frame=normalise_candles(frame,'5minute',now)
        frame=frame.loc[frame.index.date==ist(now).date()].tail(75)
        return [{'timestamp':i.isoformat(),**{k:number(row[k]) for k in ('Open','High','Low','Close','Volume')}} for i,row in frame.iterrows()]
    except (ValueError,TypeError,KeyError,AttributeError):return []


def snapshot(item,data,now,config=None,scan_config=None):
    """Capture only existing source evidence; no fallback invented prices or additional scans."""
    config=config or EntryQualityConfig.from_env()
    data=data if isinstance(data,Mapping) else {}
    now=ist(now);side=item['side']
    setup=item.get('futures_setup') or {};evidence=setup.get('evidence') or {}
    confirmation=item.get(side.lower()+'_confirmation') or {}
    ce=confirmation.get('evidence') or {}
    checks=item.get('execution_checks') or {}
    factors=lambda name:(checks.get(name) or {}).get('factors') or {}
    stock=data.get('underlying_intraday');future=data.get('intraday')
    frows=rows(future,now)
    future_quote=data.get('quote') or {};spot=data.get('spot_quote') or {}
    future_quote=future_quote if isinstance(future_quote,Mapping) else {}
    spot=spot if isinstance(spot,Mapping) else {}
    plan=deepcopy(item.get('plan') or {})
    frame=pd.DataFrame(frows)
    metrics={}
    if len(frame)>=3:
        opening=frame.iloc[:3]
        previous=frame.Close.shift(1)
        tr=pd.concat([frame.High-frame.Low,(frame.High-previous).abs(),(frame.Low-previous).abs()],axis=1).max(axis=1)
        metrics={'opening_range_high':number(opening.High.max()),'opening_range_low':number(opening.Low.min()),
            'intraday_true_range':number(tr.tail(14).mean()),'session_range':number(frame.High.max()-frame.Low.min()),
            'session_open':number(frame.Open.iloc[0])}
    retest=ce.get('breakout_retest' if side=='LONG' else 'breakdown_retest') or {}
    trigger=(item.get(side.lower()+'_entry') or {}).get('trigger_price',retest.get('trigger'))
    invalidation=(item.get(side.lower()+'_entry') or {}).get('setup_invalidation_price',retest.get('invalidation'))
    news_alignment=item.get('news_alignment') or {}
    news=item.get('news') or {}
    from src.futures.news_conflict import evaluate as evaluate_news_conflict
    # Same proposed/active evaluation in both modes, using already collected evidence only.
    news_policy=replace(scan_config or FuturesScanConfig.from_env(),news_conflict_gate_enabled=1)
    news_gate=evaluate_news_conflict(item['symbol'],side,news,stock,now,news_policy)
    news_state=('CONFLICT' if news_gate.get('excluded') or news_alignment.get('status')=='CONFLICT'
        else 'CLEAR' if news_gate.get('status')=='CLEAR' else 'UNAVAILABLE')
    if config.require_15m:
        fifteen=retracement(stock,future,now,side,config.level_bars_15m,config.retest_age_15m)
    else:fifteen={'status':'DISABLED','reason_codes':['15M_PROPOSAL_DISABLED']}
    return {'version':VERSION,'symbol':item['symbol'],'side':side,'decision_at':now.isoformat(),'available_at':now.isoformat(),
        'contract':deepcopy(item.get('contract') or {}),'plan':plan,'metrics':metrics,
        'quote':{k:str(future_quote[k]) if k=='timestamp' and future_quote.get(k) is not None else deepcopy(future_quote.get(k)) for k in ('timestamp','last_price','instrument_token','ohlc')},
        'stock_quote_at':str(spot['timestamp']) if spot.get('timestamp') is not None else None,
        'vwap':number(factors('futures_vwap_quality').get('vwap',ce.get('futures_vwap'))),
        'stock_vwap':number(ce.get('stock_vwap')),'stock_price':number(spot.get('last_price')),
        'rvol':number(factors('futures_rvol_quality').get('rvol',evidence.get('relative_volume'))),
        'stock_return':number(ce.get('stock_session_return_percent')),'nifty_return':number(ce.get('nifty_session_return_percent')),
        'sector_return':number(ce.get('sector_session_return_percent')),
        'support':number(evidence.get('support')),'resistance':number(evidence.get('resistance')),
        'trigger':number(trigger),'invalidation':number(invalidation),
        'stock_previous_close':number((spot.get('ohlc') or {}).get('close')),
        'stock_session_open':number((spot.get('ohlc') or {}).get('open')),
        'execution_spread_bps':number(factors('futures_spread_quality').get('spread_percent'))*100 if number(factors('futures_spread_quality').get('spread_percent')) is not None else None,'slippage_bps':number(item.get('expected_slippage_bps')),
        'confirmation_status':confirmation.get('status','UNKNOWN'),'confirmation_at':confirmation.get('checked_at'),
        'news_status':news_state,'news_checked_at':news.get('checked_at'),
        'news_gate_checked_at':news_gate.get('decision_at'),
        'news_gate':deepcopy(news_gate),'retracement_15m':fifteen,'source_session':frows,
        'original_decision':item.get('final_decision','UNKNOWN'),
        'original_ranking_score':number(item.get('daily_ranking_score',item.get('technical_score'))),
        'missing_execution_checks':list(item.get('missing_execution_checks',[])),
        'failed_execution_checks':list(item.get('failed_execution_checks',[])),
        'research_only':bool(item.get('research_only'))}


def evaluate(sample,now=None,config=None,scan_config=None):
    config=config or EntryQualityConfig.from_env();scan_config=scan_config or FuturesScanConfig()
    now=ist(now or sample['decision_at']);side=sample.get('side');sign=1 if side=='LONG' else -1
    result={'version':VERSION,'mode':config.mode,'classification':'SKIP','status':'UNAVAILABLE',
        'assessed_at':now.isoformat(),'decision_at':sample.get('decision_at'),'reason_codes':[],
        'entry_zone':None,'target':None,'stop_loss':None,'estimated_net_risk_reward':None,
        'measurements':{},'policy':asdict(config),'production_decision_changed':False,
        'recheck_policy':'NEXT_MANUAL_SCAN_REQUIRES_FRESH_QUOTES_CANDLES_CONFIRMATION_AND_NEWS',
        'scope':'ENTRY_QUALITY_GATE; NOT_BROKER_PERMISSION'}
    missing=[];hard=[];wait=[]
    if side not in ('LONG','SHORT'):missing.append('INVALID_DIRECTION')
    def fresh(value,limit):
        try:return 0<=(now-ist(value)).total_seconds()<=limit
        except (ValueError,TypeError):return False
    if not fresh(sample.get('available_at'),config.quote_age_seconds):missing.append('SNAPSHOT_STALE_OR_FROM_FUTURE')
    quote=sample.get('quote') or {};plan=sample.get('plan') or {};m=sample.get('metrics') or {}
    try:
        frame=pd.DataFrame(sample.get('source_session') or [])
        frame.index=pd.to_datetime(frame.pop('timestamp'),utc=True)
        frame=clean(normalise_candles(frame,'5minute',now))
        if completed_session_check(frame,now)['status']!='PASS':raise ValueError('Current prefix missing')
    except (ValueError,TypeError,KeyError):missing.append('COMPLETED_CURRENT_SESSION_UNVERIFIED')
    entry=number(plan.get('entry'));vwap=number(sample.get('vwap'));stock_vwap=number(sample.get('stock_vwap'))
    price=number(sample.get('stock_price'));trigger=number(sample.get('trigger'));invalidation=number(sample.get('invalidation'))
    for key,value in (('EXECUTABLE_ENTRY',entry),('FUTURES_VWAP',vwap),('STOCK_VWAP',stock_vwap),('STOCK_PRICE',price),
                      ('TRIGGER',trigger),('INVALIDATION',invalidation)):
        if value is None or value<=0:missing.append(key+'_UNAVAILABLE')
    if number(quote.get('last_price')) is None or number(quote.get('last_price'))<=0:missing.append('FUTURES_QUOTE_PRICE_UNAVAILABLE')
    if plan.get('movement_basis')!='FUTURES':missing.append('EXACT_FUTURES_PLAN_UNVERIFIED')
    for cost_field in ('target_costs','stop_costs'):
        components=plan.get(cost_field)
        if not isinstance(components,dict) or not components or any(number(v) is None or number(v)<0 for v in components.values()):
            missing.append('EXECUTION_COST_COMPONENTS_UNAVAILABLE')
    for key in ('timestamp',):
        if not fresh(quote.get(key),config.quote_age_seconds):missing.append('FUTURES_QUOTE_STALE_OR_UNVERIFIED')
    if not fresh(sample.get('stock_quote_at'),config.quote_age_seconds):missing.append('STOCK_QUOTE_STALE_OR_UNVERIFIED')
    token=quote.get('instrument_token');contract_token=(sample.get('contract') or {}).get('instrument_token')
    if token is None or contract_token is None or str(token)!=str(contract_token):missing.append('EXACT_CONTRACT_UNVERIFIED')
    for key in ('opening_range_high','opening_range_low','intraday_true_range','session_range','session_open'):
        if number(m.get(key)) is None or number(m[key])<=0:missing.append(key.upper()+'_UNAVAILABLE')
    previous=number((quote.get('ohlc') or {}).get('close'));opening=number((quote.get('ohlc') or {}).get('open'))
    if previous is None or previous<=0:missing.append('PREVIOUS_FUTURES_CLOSE_UNAVAILABLE')
    if opening is None or opening<=0:missing.append('FUTURES_OPEN_UNAVAILABLE')
    result['measurements']={**result['measurements'],**m,'move_from_previous_close_percent':(entry/previous-1)*100 if entry and previous and previous>0 else None,
        'move_from_open_percent':(entry/opening-1)*100 if entry and opening and opening>0 else None,
        'vwap_distance_percent':(entry/vwap-1)*100 if entry and vwap and vwap>0 else None}
    stock_previous=number(sample.get('stock_previous_close'));stock_open=number(sample.get('stock_session_open'))
    result['measurements'].update(stock_move_from_previous_close_percent=(price/stock_previous-1)*100 if price and stock_previous and stock_previous>0 else None,
        stock_move_from_open_percent=(price/stock_open-1)*100 if price and stock_open and stock_open>0 else None)
    rvol=number(sample.get('rvol'));slippage=number(sample.get('slippage_bps'))
    if rvol is None or rvol<0:missing.append('RELATIVE_VOLUME_UNAVAILABLE')
    if slippage is None or slippage<0:missing.append('SLIPPAGE_UNAVAILABLE')
    for key in ('stock_return','nifty_return','sector_return'):
        if number(sample.get(key)) is None:missing.append(key.upper()+'_UNAVAILABLE')
    if sample.get('news_status')!='CLEAR':
        (hard if sample.get('news_status')=='CONFLICT' else missing).append('NEWS_CONFLICT' if sample.get('news_status')=='CONFLICT' else 'VERIFIED_NEWS_CHECK_UNAVAILABLE')
    if not fresh(sample.get('news_checked_at'),scan_config.news_conflict_snapshot_age_seconds):missing.append('NEWS_SNAPSHOT_STALE_OR_UNVERIFIED')
    if not fresh(sample.get('news_gate_checked_at'),config.quote_age_seconds):missing.append('NEWS_CONFLICT_RECHECK_REQUIRED')
    if sample.get('confirmation_status')=='PASS':
        if not fresh(sample.get('confirmation_at'),config.quote_age_seconds):missing.append('RENEWED_PRICE_CONFIRMATION_REQUIRED')
    elif sample.get('confirmation_status')=='BLOCKED':hard.append('DIRECTIONAL_CONFIRMATION_CONFLICT')
    elif sample.get('confirmation_status')=='WAIT':wait.append('RENEWED_PRICE_CONFIRMATION_REQUIRED')
    else:missing.append('PRICE_CONFIRMATION_UNAVAILABLE')
    fifteen=sample.get('retracement_15m') or {}
    if config.require_15m:
        earliest=now.normalize()+pd.Timedelta(hours=9,minutes=15*(config.level_bars_15m+3))
        if now<earliest:wait.append('COMPLETED_15M_RETEST_NOT_YET_POSSIBLE')
        if fifteen.get('status')=='PASS' and not fresh(fifteen.get('checked_at'),config.quote_age_seconds):missing.append('15M_CONFIRMATION_STALE_OR_FROM_FUTURE')
        if fifteen.get('status')=='BLOCKED':hard.extend(fifteen.get('reason_codes') or ['15M_TREND_CONFLICT'])
        elif fifteen.get('status')=='WAIT':wait.extend(fifteen.get('reason_codes') or ['15M_COMPLETED_RETEST_REQUIRED'])
        elif fifteen.get('status')!='PASS':missing.append('15M_CONFIRMATION_UNAVAILABLE')
    if entry_window(now,scan_config)['status']!='PASS':hard.append('ENTRY_WINDOW_CLOSED_OR_FLAT_DEADLINE')
    if sample.get('research_only'):hard.append('RESEARCH_ONLY_SNAPSHOT')
    rr=number(plan.get('net_risk_reward'));profit=number(plan.get('net_profit_at_target'));loss=number(plan.get('net_loss_at_stop'));lots=number(plan.get('number_of_lots'))
    target=number(plan.get('target'));stop=number(plan.get('stop_loss'));quantity=number(plan.get('economics_quantity'))
    result.update(target=target,stop_loss=stop,estimated_net_risk_reward=rr)
    if any(x is None for x in (rr,profit,loss,target,stop,quantity)) or not plan.get('cost_evidence'):
        missing.append('COSTED_EXECUTION_PLAN_UNAVAILABLE')
    elif profit<=0 or loss<=0 or rr<scan_config.minimum_net_rr or quantity<=0 or quantity!=int(quantity) or lots is None or lots<1 or lots!=int(lots):
        hard.append('NET_REWARD_OR_RISK_BUDGET_INSUFFICIENT')
    if entry and target and stop:
        if abs((target-entry)*sign/entry-.003)>1e-8 or abs((entry-stop)*sign/entry-.002)>1e-8:hard.append('FIXED_TARGET_STOP_POLICY_MISMATCH')
        if invalidation and sign*(stop-invalidation)>=0:wait.append('FIXED_STOP_DOES_NOT_COVER_STRUCTURE')
    if not missing and entry and vwap and trigger:
        risk=entry*.002
        extension=sign*(entry-vwap)/risk;trigger_distance=sign*(entry-trigger)/risk
        obstacle=number(sample.get('resistance' if side=='LONG' else 'support'))
        costs=sum(number(v) or 0 for v in (plan.get('target_costs') or {}).values())
        room=sign*(obstacle-entry) if obstacle else None
        required=entry*.003+costs/quantity
        result['measurements']={**result['measurements'],**m,'move_from_previous_close_percent':(entry/previous-1)*100,
            'move_from_open_percent':(entry/opening-1)*100,'vwap_distance_percent':(entry/vwap-1)*100,
            'vwap_extension_stop_units':extension,'trigger_extension_stop_units':trigger_distance,
            'slippage_bps':slippage,'rvol':rvol,'remaining_room_points':room,'required_room_after_costs_points':required,
            'target_costs':costs,'intraday_range_stop_units':m['intraday_true_range']/risk,
            'stock_vs_nifty_percentage_points':sample['stock_return']-sample['nifty_return'],
            'stock_vs_sector_percentage_points':sample['stock_return']-sample['sector_return']}
        # Prior move is reported, never an independent veto. A fresh nearby trigger can validate continuation.
        continuation=(0<=trigger_distance<=scan_config.short_maximum_trigger_distance_percent/.2
            and rvol>=config.minimum_rvol and sample['confirmation_status']=='PASS')
        if extension>config.max_extension_stop_units and not continuation:wait.append('EXTENDED_FROM_VWAP_WAIT_FOR_PULLBACK')
        if trigger_distance<0:wait.append('TRIGGER_NOT_CROSSED')
        elif trigger_distance>scan_config.short_maximum_trigger_distance_percent/.2:wait.append('ENTRY_TOO_FAR_FROM_FRESH_TRIGGER')
        if rvol<config.minimum_rvol:wait.append('VOLUME_CONFIRMATION_INSUFFICIENT')
        if slippage>config.maximum_slippage_bps:hard.append('EXCESSIVE_SLIPPAGE')
        if sign*(entry-vwap)<=0 or sign*(price-stock_vwap)<=0:wait.append('PRICE_ON_WRONG_SIDE_OF_VWAP')
        if sign*(sample['stock_return']-sample['nifty_return'])<0 or sign*(sample['stock_return']-sample['sector_return'])<0:wait.append('RELATIVE_STRENGTH_CONFLICT')
        opening_level=m['opening_range_high' if side=='LONG' else 'opening_range_low']
        if sign*(entry-opening_level)<=0:wait.append('OPENING_RANGE_NOT_BROKEN')
        if room is None or obstacle<=0:missing.append('NEARBY_TARGET_OBSTACLE_UNVERIFIED')
        elif room<required:wait.append('INSUFFICIENT_REMAINING_REWARD_AFTER_COSTS')
        # A volatility-sized retest must fit inside the fixed stop; never widen it.
        if m['intraday_true_range']>risk*config.max_extension_stop_units and trigger_distance>.25:wait.append('INTRADAY_VOLATILITY_REQUIRES_TIGHTER_RETEST')
        anchor=trigger if continuation else vwap
        width=min(risk*.5,m['intraday_true_range']*.25)
        if anchor>width:
            result['entry_zone']={'low':anchor-width,'high':anchor+width,'basis':'OBSERVED_FUTURES_TRIGGER' if continuation else 'OBSERVED_FUTURES_VWAP',
                'conditional':True,'requires_new_confirmation':True,'levels_are_order_prices':False,
                'plan_applies_to':'CURRENT_EXECUTABLE_ENTRY_ONLY; RECOMPUTE_AT_ZONE'}
        else:missing.append('CONDITIONAL_ENTRY_ZONE_UNVERIFIED')
    for name,gate in (sample.get('existing_scan_gates') or {}).items():
        state=gate.get('status','UNKNOWN')
        if state!='PASS':
            (hard if state=='FAIL' else missing).append('EXISTING_SCAN_GATE_'+name+':'+state)
    # Existing mandatory failures always dominate; report-only never authorizes them.
    if sample.get('original_decision')!='APPROVED':
        if sample.get('original_decision') in ('WAIT','TOO LATE') and not sample.get('missing_execution_checks') and not sample.get('failed_execution_checks'):
            wait.append('EXISTING_APPROVAL_PENDING')
        else:hard.append('EXISTING_EXECUTION_GATES_NOT_APPROVED')
    result['classification']='SKIP' if missing or hard else 'WAIT FOR PULLBACK' if wait else 'ENTER'
    result['status']='UNAVAILABLE' if missing else 'EVALUATED'
    result['reason_codes']=list(dict.fromkeys(missing+hard+wait)) or ['FRESH_CONFIRMED_CONTINUATION_WITH_COSTED_REWARD_SPACE']
    result['retracement_15m']=deepcopy(fifteen)
    return result


def refresh_report(report,now,config=None,scan_config=None):
    """In-place only on a newly generated report. No history viewer calls this function."""
    config=config or EntryQualityConfig.from_env()
    counts={'ENTER':0,'WAIT FOR PULLBACK':0,'SKIP':0};changes=0
    for item in report.get('reviewed',[]):
        recorded=item.get('entry_quality_input')
        if recorded is None:
            recorded={'symbol':item.get('symbol'),'side':item.get('side'),'decision_at':ist(now).isoformat(),
                'original_decision':item.get('final_decision'),'research_only':item.get('research_only',report.get('research_only',False))}
        else:recorded=deepcopy(recorded)
        current=item.get('final_decision','UNKNOWN')
        withdrawal=item.get('entry_quality_withdrawal') or {}
        own_withdrawal=(current==withdrawal.get('decision')
            and item.get('reason_codes',[])==withdrawal.get('reason_codes_after')
            and all(g.get('status')=='PASS' for g in item.get('scan_gates',{}).values()))
        recorded['original_decision']=withdrawal.get('original_decision',current) if own_withdrawal else current
        recorded['existing_scan_gates']=deepcopy(item.get('scan_gates',{}))
        # These are fields of this new run only, captured after final safety. Archives never call this function.
        if item.get('entry_quality_input') is not None:
            item['entry_quality_input']['original_decision']=recorded['original_decision']
            item['entry_quality_input']['existing_scan_gates']=deepcopy(recorded['existing_scan_gates'])
        recorded['research_only']=bool(item.get('research_only') or report.get('research_only'))
        assessment=evaluate(recorded,now,config,scan_config)
        if current!='APPROVED' and assessment['classification']=='ENTER':
            assessment['classification']='WAIT FOR PULLBACK'
            assessment['reason_codes'].append('NEW_MANUAL_SCAN_APPROVAL_REQUIRED')
        item['entry_quality']=assessment;counts[assessment['classification']]+=1
        if config.mode=='ENFORCE' and item.get('final_decision')=='APPROVED' and assessment['classification']!='ENTER':
            item['decision_before_entry_quality']='APPROVED'
            item['final_decision']='WAIT' if assessment['classification']=='WAIT FOR PULLBACK' else 'UNKNOWN' if assessment['status']=='UNAVAILABLE' else 'REJECT'
            item['reason_codes']=list(dict.fromkeys(item.get('reason_codes',[])+assessment['reason_codes']))
            item['entry_quality_withdrawal']={'original_decision':'APPROVED','decision':item['final_decision'],
                'reason_codes_after':list(item['reason_codes'])}
            assessment['production_decision_changed']=True
            confirmation=item.get('futures_execution_confirmation')
            if confirmation:confirmation.update(entry_ready=False,final_decision=item['final_decision'],timing='NOT_READY')
            for name in ('long_entry','short_entry'):
                if item.get(name):
                    entry=item[name]
                    entry['state_before_entry_quality']=entry.get('state')
                    entry['execution_approved']=False
                    entry['state']='WAIT_FOR_PULLBACK' if item['final_decision']=='WAIT' else 'UNVERIFIED' if item['final_decision']=='UNKNOWN' else 'REJECTED'
        if config.mode=='ENFORCE' and item.get('entry_quality_withdrawal') and recorded['original_decision']=='APPROVED' and item.get('final_decision')!='APPROVED':
            assessment['production_decision_changed']=True
            changes+=1
    if config.mode=='ENFORCE':
        report['report_c']=[i for i in report.get('report_c',[]) if i.get('final_decision')=='APPROVED']
        report['approved_count']=sum(i.get('final_decision')=='APPROVED' for i in report.get('reviewed',[]))
        if report.get('entry_quality_shortlist_limit') is not None:
            from src.futures.scanner import rank_key
            from src.futures.news_conflict import shortlist
            eligible=sorted((i for i in report.get('reviewed',[]) if i.get('final_decision')=='APPROVED'),key=rank_key,reverse=True)
            report['report_c']=shortlist(eligible,report['entry_quality_shortlist_limit'])
    report['entry_quality_summary']={'version':VERSION,'mode':config.mode,'counts':counts,'changed_decisions':changes,
        'production_behavior':'UNCHANGED' if config.mode=='REPORT_ONLY' else 'WITHDRAW_ONLY',
        'checked_at':ist(now).isoformat(),'automatic_rechecks':False,'require_15m':config.require_15m,
        'earliest_entry_time_ist':(ist(now).normalize()+pd.Timedelta(hours=9,minutes=15*(config.level_bars_15m+3))).strftime('%H:%M') if config.require_15m else '09:20'}
    if 'execution_safety' in report:
        report['execution_safety']['entry_quality_mode']=config.mode
        report['execution_safety']['entry_quality_15m_required']=config.require_15m
    return report
