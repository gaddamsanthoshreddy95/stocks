"""Additive snapshot-only quality/timing analysis. Never approves or changes trades."""
from copy import deepcopy
from dataclasses import asdict
from math import isfinite
from time import perf_counter
import json
import os
from pathlib import Path
import pandas as pd
from src.futures.report_e_config import ReportEConfig
from src.futures.report_e_history import candidate_history
from src.quality.futures_selection import REQUIRED_CHECKS

TITLE='REPORT E — QUALITY + ENTRY TIMING + COST-ADJUSTED PROBABILITY'
SECTIONS=(('section_e1','SECTION E1 — BULLISH / LONG CANDIDATES'),
          ('section_e2','SECTION E2 — BEARISH / SHORT CANDIDATES'),
          ('section_e3','SECTION E3 — HIGH-QUALITY REJECTED CANDIDATES FROM REPORT D'))


def numeric(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value)


def stamp(value):
    if value is None:return None
    try:
        value=pd.Timestamp(value)
        if pd.isna(value):return None
        return value.tz_localize('Asia/Kolkata') if value.tz is None else value.tz_convert('Asia/Kolkata')
    except (ValueError,TypeError):return None


def current_market(now):
    holidays={v.strip() for v in os.getenv('MARKET_HOLIDAYS_IST','').split(',')}
    return now is not None and now.weekday()<5 and now.date().isoformat() not in holidays and (9,15)<=(now.hour,now.minute)<(15,30)


def fresh(value,now,maximum):
    value=stamp(value)
    return value is not None and now is not None and value.date()==now.date() and 0<=(now-value).total_seconds()<=maximum


def decision_score(status):
    return 100 if status=='PASS' else 0 if status=='FAIL' else None


def binary(condition,available=True):
    return (100 if condition else 0) if available else None


def quality(item,config,policy):
    """Group contributions use full denominators: unavailable evidence adds no points."""
    side=item['side'];sign=1 if side=='LONG' else -1
    setup=item.get('futures_setup') or {};e=setup.get('evidence') or item.get('evidence') or {}
    c=item.get('company_research') or {};facts=c.get('fundamentals') or {};checks=item.get('execution_checks') or {}
    r=c.get('checks') or {};groups={};measurements={}
    def feature(group,name,value,score):
        groups.setdefault(group,{})[name]={'value':deepcopy(value),'score':score,'status':'UNKNOWN' if score is None else 'SUPPORTS' if score>=70 else 'MIXED' if score>0 else 'CONTRADICTS'}
    ema=e.get('ema_values') or {};pairs=[('EMA9','EMA21'),('EMA21','EMA50'),('EMA50','EMA200')]
    align=[(ema[a]-ema[b])*sign>0 for a,b in pairs if numeric(ema.get(a)) and numeric(ema.get(b))]
    feature('technical','ema_alignment',ema,sum(align)/len(pairs)*100 if len(align)==len(pairs) else None)
    rsi=e.get('rsi_14');low=policy.get('bullish_rsi_low',55) if side=='LONG' else policy.get('bearish_rsi_low',30);high=policy.get('bullish_rsi_high',70) if side=='LONG' else policy.get('bearish_rsi_high',45)
    feature('technical','rsi_momentum',rsi,binary(low<=rsi<=high,numeric(rsi)) if numeric(rsi) else None)
    hist=e.get('macd_histogram');feature('technical','macd_direction',{'macd':e.get('macd'),'signal':e.get('macd_signal'),'histogram':hist},binary(hist*sign>0) if numeric(hist) else None)
    adx=e.get('adx_14');feature('technical','adx_strength',adx,binary(adx>=policy.get('minimum_adx',25)) if numeric(adx) else None)
    price,vwap=e.get('price'),e.get('vwap');feature('technical','vwap_position',vwap,binary((price-vwap)*sign>0) if numeric(price) and numeric(vwap) else None)
    structure=e.get('higher_highs_higher_lows') if side=='LONG' else e.get('lower_highs_lower_lows')
    feature('technical','price_structure',{'support':e.get('support'),'resistance':e.get('resistance'),'pattern':e.get('pattern'),'setup':item.get('setup_type')},binary(structure) if isinstance(structure,bool) else binary(setup.get('confirmed')) if isinstance(setup.get('confirmed'),bool) else None)
    exhaustion=e.get('selling_or_buying_exhaustion');feature('technical','extension_exhaustion',{'atr_consumed':e.get('atr_consumed_fraction'),'ema21_extension_atr':e.get('ema21_extension_atr'),'exhaustion':exhaustion},binary(not exhaustion and (setup.get('timing') or item.get('timing'))!='TOO LATE') if isinstance(exhaustion,bool) else None)
    feature('technical','supertrend',checks.get('futures_supertrend_quality',{}).get('factors'),decision_score(checks.get('futures_supertrend_quality',{}).get('status')))
    rv=e.get('relative_volume');feature('volume','relative_volume',rv,min(100,max(0,rv/policy.get('minimum_rvol',1.2)*100)) if numeric(rv) else None)
    delivery,baseline=facts.get('delivery_percent'),facts.get('monthly_delivery_percent')
    feature('volume','delivery_vs_average',{'delivery_percent':delivery,'historical_average_percent':baseline},binary(delivery>=baseline) if numeric(delivery) and numeric(baseline) else None)
    share=e.get('directional_candle_volume_share');feature('volume','directional_volume',share,max(0,min(100,share*100)) if numeric(share) else None)
    quote=item.get('futures_quote') or {}
    feature('volume','futures_volume',quote.get('volume'),decision_score(checks.get('futures_rvol_quality',{}).get('status')))
    sector=str(item.get('sector','')).upper()
    insurance=item.get('symbol') in {'ICICIGI','ICICIPRULI','HDFCLIFE','SBILIFE'}
    lender=sector in {'BANKING','PSU_BANK','NBFC','HOUSING_FINANCE'} or c.get('sector_interpretation','').startswith('Lending-company')
    financial=lender or insurance or sector=='FINANCIAL_SERVICES'
    if side=='LONG':
        for name,field in [('valuation_quality','pe_ratio'),('roe_quality','roe'),('roce_quality','roce'),('debt_free_quality','total_debt'),('quarterly_results_quality','quarterly_profit_growth_pct'),('institutional_holding_quality','fii_holding_percent'),('promoter_holding_quality','promoter_holding_percent')]:
            score=decision_score(r.get(name,{}).get('status'))
            if financial and name in ('debt_free_quality','roce_quality'):score=None
            feature('fundamentals',name,r.get(name,{}).get('factors',facts.get(field)),score)
    else:
        interpretation={row['condition']:row for row in c.get('short_interpretation',[]) if row.get('condition')}
        for name in ('valuation','debt_trend','roe','roce','fii','dii','promoter','revenue_growth','profit_growth'):
            row=interpretation.get(name,{})
            value={'SUPPORTS_SHORT':100,'CONTRADICTS_SHORT':0,'MIXED':50,'NEUTRAL':50}.get(row.get('short_thesis'))
            if financial and name in ('debt_trend','roce'):value=None
            feature('fundamentals',name,row.get('values'),value)
    for name in ('futures_oi_quality','futures_spread_quality','futures_depth_quality','futures_atr_quality'):
        score=decision_score(checks.get(name,{}).get('status'))
        if name=='futures_oi_quality' and checks.get(name,{}).get('factors',{}).get('directional_alignment') is False:
            score=0
        feature('futures',name,checks.get(name,{}).get('factors'),score)
    relative=e.get('sector_relative')
    excess=relative.get('excess_percentage_points') if isinstance(relative,dict) else None
    feature('context','sector_relative_strength',relative,binary(excess*sign>0) if numeric(excess) else None)
    news=item.get('news') or {};state=news.get('news_state')
    feature('context','news_verification',{'state':state,'sentiment':news.get('sentiment'),'checked_at':news.get('checked_at')},decision_score(r.get('recent_news_quality',{}).get('status')) if state in ('ANALYZED','NO_RELEVANT_NEWS') else None)
    feature('context','event_risk',item.get('event_risk'),decision_score(checks.get('futures_event_quality',{}).get('status')))
    weights=config.long_weights if side=='LONG' else config.short_weights
    total=coverage=0;strengths=[];weaknesses=[];unknown=[];components={}
    for group,values in groups.items():
        known=[v['score'] for v in values.values() if v['score'] is not None]
        subtotal=sum(known)/len(values)
        total+=weights[group]*subtotal
        coverage+=weights[group]*len(known)/len(values)*100
        components[group]={'score':subtotal if known else None,'coverage_percent':len(known)/len(values)*100,'weight':weights[group],'features':values}
        for name,value in values.items():
            label=group+':'+name
            (unknown if value['score'] is None else strengths if value['score']>=70 else weaknesses).append(label)
    measurements={'technical_basis':item.get('technical_score_timeframe','DAILY_DISCOVERY'),
        'technical_indicators':deepcopy(e),'company_facts':deepcopy(facts),'original_research_checks':deepcopy(r),
        'futures_checks':deepcopy(checks),'futures_quote':deepcopy(quote),'intraday_oi_change':deepcopy(item.get('intraday_oi_change')),
        'news':deepcopy(news),'events':deepcopy(item.get('event_risk')),'sector':item.get('sector'),
        'sector_interpretation':('Insurance: debt/ROCE do not establish operating weakness; solvency, claims and embedded-value metrics unavailable. ' if insurance else '')+c.get('sector_interpretation','UNKNOWN'),
        'market_risk':e.get('market_relative')}
    return {'score':round(total,2) if coverage>0 else None,'coverage_percent':round(coverage,2),'components':components,
            'strong_quality_evidence':coverage>=config.strong_coverage_percent and total>=config.strong_quality_threshold,
            'strengths':strengths,'weaknesses':weaknesses,'missing_features':unknown,'measurements':measurements}


def entry_analysis(item,report,context,*,rejected=False):
    from src.futures.scanner import REQUIRED_EXECUTION
    from src.futures.execution_safety import REQUIRED_SCAN_GATES
    from src.futures.sessions import expected_completed_bar
    from src.quality.futures_execution import FuturesExecutionConfig
    limits=FuturesExecutionConfig.from_env()
    e=(item.get('futures_setup') or {}).get('evidence') or {}
    setup=item.get('futures_setup') or {};checks=item.get('execution_checks') or {};research=item.get('company_research') or {}
    policy=report.get('config') or {};now=stamp(report.get('generated_at'));after=bool(report.get('research_only') or item.get('research_only') or not current_market(now))
    context=context or {};rows=context.get('completed_candles') or []
    candle=rows[-1] if rows else {};previous=rows[-2] if len(rows)>1 else {}
    missing=list(item.get('missing_execution_checks') or []);failed=list(item.get('failed_execution_checks') or [])
    if not isinstance(item.get('missing_execution_checks'),list) or not isinstance(item.get('failed_execution_checks'),list):
        missing.append('MANDATORY_REJECTION_AUDIT_NOT_RETAINED')
    for name in REQUIRED_EXECUTION:
        state=checks.get(name,{}).get('status')
        if state=='FAIL':failed.append(name)
        elif state!='PASS':missing.append(name)
    for name in REQUIRED_SCAN_GATES:
        state=item.get('scan_gates',{}).get(name,{}).get('status')
        if state=='FAIL':failed.append(name)
        elif state!='PASS':missing.append(name)
    for name in REQUIRED_CHECKS:
        state=research.get('checks',{}).get(name,{}).get('status')
        if state=='FAIL':failed.append('research:'+name)
        elif state!='PASS':missing.append('research:'+name)
    if research.get('policy_review_required'):failed.append('RESEARCH_POLICY_REVIEW_REQUIRED')
    if research.get('status')!='PASS':missing.append('OVERALL_COMPANY_RESEARCH_NOT_PASS')
    if not item.get('execution_reviewed'):missing.append('FUTURES_EXECUTION_NOT_REVIEWED')
    if checks.get('listed_futures_contract',{}).get('status')!='PASS':missing.append('LISTED_FUTURES_CONTRACT_UNVERIFIED')
    quote=item.get('futures_quote') or {};quote_ok=fresh(quote.get('timestamp'),now,limits.maximum_quote_age_seconds) and numeric(quote.get('last_price')) and quote['last_price']>0
    bar_at=candle.get('timestamp') or e.get('signal_timestamp')
    bar_stamp=stamp(bar_at);bar_ok=bar_stamp is not None and now is not None and bar_stamp == expected_completed_bar(now) and fresh(bar_stamp+pd.Timedelta(minutes=5),now,limits.maximum_candle_age_seconds)
    missing_bars=context.get('missing_candles') or []
    equity_missing=item.get('data_freshness',{}).get('missing_completed_session_candles') or []
    if missing_bars or equity_missing:missing.append('COMPLETED_CANDLE_SEQUENCE_INCOMPLETE')
    if not quote_ok:missing.append('FUTURES_QUOTE_STALE_OR_UNVERIFIED')
    if not bar_ok:missing.append('FUTURES_COMPLETED_CANDLE_STALE_OR_UNVERIFIED')
    if not rows:missing.append('COMPLETED_CANDLE_CONFIRMATION_NOT_RETAINED')
    news=item.get('news') or {};news_at=stamp(news.get('checked_at'))
    news_ok=news.get('news_state') in ('ANALYZED','NO_RELEVANT_NEWS') and news_at is not None and now is not None and 0<=(now-news_at).total_seconds()<=900
    if not news_ok:missing.append('NEWS_PENDING_UNAVAILABLE_OR_STALE')
    if item.get('news_alignment',{}).get('approved') is not True:missing.append('DIRECTIONAL_NEWS_CONFIRMATION_UNVERIFIED')
    sign=1 if item['side']=='LONG' else -1
    ema=e.get('ema_values') or {};rsi_delta=(candle.get('RSI')-previous.get('RSI')) if numeric(candle.get('RSI')) and numeric(previous.get('RSI')) else None
    histogram=e.get('macd_histogram');adx=e.get('adx_14');price=e.get('price');vwap=e.get('vwap');rv=e.get('relative_volume')
    conditions={
        'closed_candle_direction':binary((candle['Close']-candle['Open'])*sign>0) if numeric(candle.get('Close')) and numeric(candle.get('Open')) else None,
        'ema9_ema21':binary((ema['EMA9']-ema['EMA21'])*sign>0) if numeric(ema.get('EMA9')) and numeric(ema.get('EMA21')) else None,
        'rsi_direction':binary(rsi_delta*sign>0) if numeric(rsi_delta) else None,
        'macd_direction':binary(histogram*sign>0) if numeric(histogram) else None,
        'adx':binary(adx>=policy.get('minimum_adx',25)) if numeric(adx) else None,
        'rvol_confirmation':binary(rv>=policy.get('minimum_rvol',1.2)) if numeric(rv) else None,
        'vwap_position':binary((price-vwap)*sign>0) if numeric(price) and numeric(vwap) else None,
        'setup_confirmation':binary(setup.get('confirmed')) if isinstance(setup.get('confirmed'),bool) else None,
        'target_clearance':decision_score(checks.get('futures_target_space_quality',{}).get('status')),
        'oi_confirmation':decision_score(checks.get('futures_oi_quality',{}).get('status')),
        'spread':decision_score(checks.get('futures_spread_quality',{}).get('status')),
        'depth':decision_score(checks.get('futures_depth_quality',{}).get('status')),
        'atr':decision_score(checks.get('futures_atr_quality',{}).get('status')),
        'sector_confirmation':decision_score(checks.get('futures_sector_strength_quality',{}).get('status')),
        'no_extension_or_exhaustion':binary(setup.get('timing') not in ('TOO LATE','WAIT FOR PULLBACK') and not e.get('selling_or_buying_exhaustion')) if setup else None,
    }
    trigger=e.get('short_trigger_price') if item['side']=='SHORT' else e.get('trigger')
    conditions['trigger_crossing_after_completed_confirmation']=binary((quote['last_price']-trigger)*sign>=0) if quote_ok and numeric(trigger) else None
    for name,value in conditions.items():
        if value is None:missing.append('REPORT_E_'+name.upper()+'_UNVERIFIED')
    oi_alignment=checks.get('futures_oi_quality',{}).get('factors',{}).get('directional_alignment')
    if oi_alignment is False:conditions['oi_confirmation']=0
    readiness=sum(v for v in conditions.values() if v is not None)/len(conditions) if any(v is not None for v in conditions.values()) else None
    coverage=sum(v is not None for v in conditions.values())/len(conditions)*100
    plan=item.get('plan') or {}
    movement_basis=plan.get('movement_basis') or item.get('data_freshness',{}).get('history_basis')
    execution_prices_ok=quote_ok and bar_ok and not missing_bars and all(checks.get(k,{}).get('status')=='PASS' for k in REQUIRED_EXECUTION) and checks.get('listed_futures_contract',{}).get('status')=='PASS' and item.get('execution_reviewed') and item.get('scan_gates',{}).get('intraday_entry_window',{}).get('status')=='PASS' and not after
    entry=plan.get('entry') if execution_prices_ok and numeric(plan.get('entry')) and plan['entry']>0 else None
    policy_match=policy.get('target_fraction')==.003 and policy.get('stop_fraction')==.002 and movement_basis=='FUTURES'
    target=stop=rr=None
    if entry is not None and policy_match and numeric(plan.get('target')) and numeric(plan.get('stop_loss')):
        if abs(plan['target']/entry-(1+sign*.003))<1e-8 and abs(plan['stop_loss']/entry-(1-sign*.002))<1e-8:
            target,stop,rr=plan['target'],plan['stop_loss'],plan.get('net_risk_reward')
    if not policy_match:missing.append('REPORT_E_FUTURES_PERCENT_POLICY_REQUIRES_REVIEW')
    if target is None or stop is None:missing.append('VALIDATED_FIXED_FUTURES_LEVELS_UNAVAILABLE')
    timing=setup.get('timing') or item.get('timing');exhausted=timing=='TOO LATE' or bool(e.get('selling_or_buying_exhaustion'))
    if rejected:status='REJECT'
    elif after:status='UNKNOWN'
    elif item.get('final_decision')=='REJECT' or exhausted:status='REJECT'
    elif missing:status='UNKNOWN'
    elif failed:status='REJECT'
    elif item.get('final_decision')=='APPROVED' and all(v==100 for v in conditions.values()) and target is not None:status='READY'
    elif setup.get('confirmed'):status='WAIT'
    else:status='WATCH'
    conditional='REJECT' if exhausted or rejected else 'WAIT' if timing in ('WAIT','WAIT FOR PULLBACK') else 'WATCH'
    invalidation=e.get('setup_invalidation_price')
    retest=None
    if numeric(vwap) and numeric(candle.get('Close')):
        boundary=candle.get('Low' if item['side']=='LONG' else 'High')
        if numeric(boundary):retest=(boundary-vwap)*sign<=0 and (candle['Close']-vwap)*sign>0
    return {'entry_readiness_score':round(readiness,2) if readiness is not None else None,'entry_evidence_coverage_percent':round(coverage,2),
        'entry_status':status,'execution_status':'NOT LIVE' if after else 'ORIGINAL APPROVAL REQUIRED','conditional_watchlist_status':conditional,
        'setup_type':item.get('setup_type'),'conditional_entry_trigger':trigger,'conditional_trigger_basis':'Retained exact Futures structure; requires a new completed-candle confirmation' if numeric(trigger) else 'UNKNOWN',
        'required_candle_confirmation':'Completed five-minute setup/candle-close confirmation, followed by a verified Futures trigger crossing; VWAP/EMA alignment, RVOL, RSI/MACD/ADX and original mandatory gates. Conditional levels are not order fills.',
        'futures_reference_price':price if numeric(price) else None,'reference_timestamp':e.get('signal_timestamp'),
        'reference_basis':'COMPLETED_FUTURES_CANDLE_RESEARCH' if numeric(price) else 'UNKNOWN',
        'live_quote_timestamp':quote.get('timestamp'),'live_quote_fresh':quote_ok and not after,
        'entry':entry,'target':target,'stop_loss':stop,'execution_rr':rr,'existing_movement_basis':movement_basis,
        'daily_discovery':deepcopy(item.get('daily_discovery',{})),
        'futures_execution_confirmation':deepcopy(item.get('futures_execution_confirmation',{})),
        'scan_gates':deepcopy(item.get('scan_gates',{})),
        'validated_execution_economics':{'gross_target_profit':plan.get('gross_profit_at_target'),'gross_stop_loss':-plan['gross_loss_at_stop'] if numeric(plan.get('gross_loss_at_stop')) else None,
            'net_target_profit':plan.get('net_profit_at_target'),'net_stop_loss':-plan['net_loss_at_stop'] if numeric(plan.get('net_loss_at_stop')) else None,
            'target_costs':deepcopy(plan.get('target_costs')),'stop_costs':deepcopy(plan.get('stop_costs'))} if target is not None else None,
        'policy_review_required':not policy_match,'vwap':vwap,'vwap_retest_hold_or_rejection':retest,'ema_alignment':ema,
        'rsi_direction_change':rsi_delta,'atr':e.get('daily_atr'),'support':e.get('support'),'resistance':e.get('resistance'),
        'structural_invalidation':invalidation,'structural_invalidation_status':'RESEARCH REFERENCE ONLY; does not replace fixed stop' if numeric(invalidation) else 'UNKNOWN',
        'available_target_space':deepcopy(item.get('futures_target_space')),'underlying_target_space':deepcopy(item.get('underlying_target_space')),
        'retained_target_space_percent':e.get('target_space_percent'),'conditions':conditions,
        'liquidity_spread_status':{k:deepcopy(checks.get(k,{'status':'UNKNOWN'})) for k in ('futures_spread_quality','futures_depth_quality','futures_oi_quality')},
        'missing_mandatory_checks':list(dict.fromkeys(missing)),'additional_mandatory_failures':list(dict.fromkeys(failed)),
        'missing_candles':missing_bars,'missing_equity_candles':equity_missing,'completed_candle_at':bar_at,
        'no_live_approval_override':True,'timing_note':'Move already extended/exhausted; do not chase' if exhausted else 'Conditional research only; original decisions remain authoritative'}


def build_report_e(report,*,config=None,contexts=None,histories=None,limit=5):
    config=config or ReportEConfig.from_env();contexts=contexts or {};histories=histories or {}
    policy=report.get('config') or {};minimum=policy.get('minimum_historical_signals',30)
    rejected=list(report.get('report_d',{}).get('candidates',[]))
    index={(i.get('symbol'),i.get('side')):i for i in report.get('reviewed',[])}
    def analyse(item,d=None):
        symbol,side=item.get('symbol'),item.get('side');q=quality(item,config,policy)
        timing=entry_analysis(item,report,contexts.get(symbol+':'+side),rejected=d is not None)
        contract=item.get('contract',{}).get('tradingsymbol')
        historical=candidate_history(histories.get(contract),side,item.get('setup_type'),minimum,contract,policy)
        score=q['score'];readiness=timing['entry_readiness_score'];rank_score=(config.quality_rank_weight*(score or 0)+(1-config.quality_rank_weight)*(readiness or 0)) if score is not None else None
        return {'symbol':symbol,'side':side,'original_technical_score':item.get('technical_score'),
            'original_fundamental_score':((item.get('company_research') or {}).get('fundamental_assessment') or {}).get('score'),
            'original_research_status':(item.get('company_research') or {}).get('status','UNKNOWN'),
            'original_decision':item.get('final_decision'),'label':'REJECTED / RESEARCH ONLY' if d else 'APPROVED IN ORIGINAL REPORT C' if item.get('final_decision')=='APPROVED' and not report.get('research_only') else 'RESEARCH ONLY',
            'quality':q,'timing':timing,'historical':historical,'report_e_rank_score':round(rank_score,2) if rank_score is not None else None,
            'news_event_status':{'news':item.get('news',{}).get('news_state','UNKNOWN'),'news_checked_at':item.get('news',{}).get('checked_at'),
                                 'event_risk':deepcopy(item.get('event_risk'))},
            'data_freshness':deepcopy(item.get('data_freshness',{})),
            'original_rejection_classification':d.get('classification') if d else None,
            'original_rejection_reasons':deepcopy(d.get('exact_rejection_reasons',[])) if d else deepcopy(item.get('reason_codes',[])),
            'retained_report_d_checks':deepcopy(d.get('remaining_checks')) if d else None,
            'retained_report_d_missing_information':deepcopy(d.get('missing_information')) if d else None}
    all_rows={};missing_technical=[];unranked_quality=[]
    for key,item in index.items():
        if item.get('side') not in ('LONG','SHORT'):continue
        if numeric(item.get('technical_score')):
            analysed=analyse(item)
            if analysed['quality']['score'] is not None:
                all_rows[key]=analysed
            else:
                unranked_quality.append(analysed)
        else:missing_technical.append({'symbol':key[0],'side':key[1],'reason':'TECHNICAL SCORE UNAVAILABLE; retained audit is not a ranked opportunity',
                                      'original_reason_codes':deepcopy(item.get('reason_codes',[])),
                                      'source_failures':deepcopy(item.get('source_failures',{}))})
    def order(row):
        return (row['quality']['score'] is not None,row['report_e_rank_score'] if row['report_e_rank_score'] is not None else -1,
                row['quality']['coverage_percent'],row['original_technical_score'] if numeric(row['original_technical_score']) else -1)
    long=sorted((r for r in all_rows.values() if r['side']=='LONG'),key=order,reverse=True)
    short=sorted((r for r in all_rows.values() if r['side']=='SHORT'),key=order,reverse=True)
    d_rows=[]
    for d in rejected:
        key=(d.get('symbol'),d.get('side'))
        item=index.get(key)
        if item is None:
            item={'symbol':key[0],'side':key[1],'technical_score':d.get('technical_score'),'final_decision':d.get('original_decision','REJECT'),
                  'reason_codes':d.get('exact_rejection_reasons',[])}
        d_rows.append(analyse(item,d))
    d_rows.sort(key=order,reverse=True)
    sections={key:{'title':title,'candidate_count':len(rows),'display_limit':limit if key!='section_e3' else None,
                   'candidates':rows,'empty_reason':None if rows else 'No valid scored candidates in retained scan records; missing evidence is not ranked alphabetically.'}
              for (key,title),rows in zip(SECTIONS,(long,short,d_rows))}
    return {'title':TITLE,'generated_at':report.get('generated_at'),'source':'Same completed scan; no second universe discovery or market-data requests',
            'original_decisions_authoritative':True,'research_only':bool(report.get('research_only')),
            'configuration':asdict(config),'score_interpretation':'Independent evidence scores with full denominators; unavailable features earn no points. Hypotheses, not probabilities.',
            'ranking_method':'0-100 quality/entry weighted evidence score; coverage and original technical score break ties. LONG/SHORT independent. Original A-D ordering unchanged.',
            'historical_scope':'Observed technical setup target-first frequency only; point-in-time fundamental/news/OI/depth gates unavailable. Costs/expectancy provisional without fill calibration.',
            'policy_notice':'Fixed execution levels use Futures entry references. Equity daily discovery remains research only. Incompatible cached historical evidence stays UNKNOWN; quoted references are not executed fills.',
            'summary':{'long_candidates':len(long),'short_candidates':len(short),'report_d_records':len(d_rows),
                       'high_quality_report_d_records':sum(r['quality']['strong_quality_evidence'] for r in d_rows),
                       'ready_count':sum(r['timing']['entry_status']=='READY' for r in (*long,*short)),
                       'historically_sufficient_candidates':sum(r['historical']['status']=='OBSERVED_OUT_OF_SAMPLE' for r in (*long,*short))},
            **sections,'unranked_missing_technical_records':missing_technical,'unranked_quality_records':unranked_quality}


def append_report_e(report,*,config=None,contexts=None,histories=None,limit=5):
    started=perf_counter()
    try:
        analysis=build_report_e(report,config=config,contexts=contexts,histories=histories,limit=limit)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        analysis={'title':TITLE,'status':'UNKNOWN','reason':f'{type(exc).__name__}: {exc}',
                  'original_decisions_authoritative':True,'summary':{},'source':'Additional analysis unavailable; existing Reports A-D remain intact.'}
    analysis['analysis_seconds']=perf_counter()-started
    report.pop('report_e',None)
    report['report_e']=analysis
    return report


def render_report_e(analysis):
    lines=['## '+TITLE,'','Additional analysis only. Existing approval/rejection decisions remain authoritative.','',analysis.get('source',''),'',
           'Summary: '+json.dumps(analysis.get('summary',{})),'']
    if analysis.get('reason'):lines.extend(['UNKNOWN: '+analysis['reason'],''])
    if analysis.get('configuration'):
        lines.extend(['Configurable hypotheses: '+json.dumps(analysis['configuration']),''])
    for field in ('score_interpretation','ranking_method','historical_scope','policy_notice'):
        if analysis.get(field):lines.extend([analysis[field],''])
    for key,title in SECTIONS:
        section=analysis.get(key,{})
        lines.extend(['### '+title,''])
        rows=section.get('candidates',[])
        if not rows:lines.extend([section.get('empty_reason') or 'Additional evidence unavailable.','']);continue
        limit=section.get('display_limit');selected=rows if limit is None else rows[:limit]
        lines.extend([f"Showing {len(selected)} of {len(rows)} retained candidates. Full analysis is available in JSON.",''])
        for row in selected:
            q,t,h=row['quality'],row['timing'],row['historical']
            lines.extend([f"#### {row['symbol']} — {row['side']} — {row['label']}",'',
                          f"Original technical/fundamental scores: {row['original_technical_score']}/{row['original_fundamental_score']}; original decision {row['original_decision']}.",
                          f"NEW stock quality: {q['score'] if q['score'] is not None else 'UNKNOWN'}; coverage {q['coverage_percent']}%; NEW entry readiness: {t['entry_readiness_score'] if t['entry_readiness_score'] is not None else 'UNKNOWN'}; status {t['entry_status']} ({t['execution_status']}).",''])
            fmt=lambda value:'UNKNOWN' if value is None else str(value)
            lines.extend([f"Futures entry: {fmt(t['entry'])}; target: {fmt(t['target'])}; stop-loss: {fmt(t['stop_loss'])}; execution RR: {fmt(t['execution_rr'])}.",
                          f"Conditional trigger: {fmt(t['conditional_entry_trigger'])}; Futures research reference: {fmt(t['futures_reference_price'])} at {fmt(t['reference_timestamp'])}.",
                          f"ATR: {fmt(t['atr'])}; VWAP: {fmt(t['vwap'])}; support/resistance: {fmt(t['support'])}/{fmt(t['resistance'])}.",
                          f"Historical probability: {fmt(h.get('target_first_percent'))}; sample count: {fmt(h.get('sample_count'))}; {h.get('reason') or h.get('observed_scope')}.",
                          f"Net expectancy: {fmt(h.get('net_expectancy'))} ({h.get('cost_adjusted_expectancy_status')}); break-even target-first rate: {fmt(h.get('break_even_target_first_percent'))}.",'',
                          '| Quality group | Weight | Evidence score | Coverage % |','|---|---:|---:|---:|'])
            for name,component in q['components'].items():
                lines.append(f"| {name} | {component['weight']} | {fmt(component['score'])} | {component['coverage_percent']} |")
            lines.append('')
            for name,value in [('Setup and conditional timing',t),('Historical evidence and cost-adjusted expectancy',h),
                               ('News and event risk',row['news_event_status']),('Data freshness',row['data_freshness']),
                               ('Main strengths',q['strengths']),('Main weaknesses',q['weaknesses']),('Unavailable quality features',q['missing_features']),
                               ('Original rejection classification',row['original_rejection_classification']),('Original rejection reasons',row['original_rejection_reasons']),
                               ('Retained Report D PASS/FAIL/UNKNOWN checks',row['retained_report_d_checks'])]:
                lines.extend([f"- **{name}:** {json.dumps(value,ensure_ascii=False,default=str) if value is not None else 'UNKNOWN'}",''])
    return '\n'.join(lines)


def save_report_e(report,directory=None):
    from tempfile import NamedTemporaryFile
    analysis=report.get('report_e')
    if analysis is None:return
    try:
        folder=Path(directory or os.getenv('FUTURES_REPORT_DIRECTORY','reports/prepared_scans'));folder.mkdir(parents=True,exist_ok=True)
        stamp_text=''.join(c for c in str(report.get('generated_at','unknown')) if c.isalnum())
        paths={ext:str(folder/f'report_E_{stamp_text}.{ext}') for ext in ('json','md')}
        analysis['saved_files']=paths
        for ext,path in paths.items():
            body=json.dumps(analysis,indent=2,default=str) if ext=='json' else render_report_e(analysis)
            with NamedTemporaryFile('w',dir=folder,encoding='utf-8',delete=False) as handle:
                handle.write(body);temporary=Path(handle.name)
            try:os.replace(temporary,path)
            finally:temporary.unlink(missing_ok=True)
    except OSError as exc:analysis['storage_error']=str(exc)
