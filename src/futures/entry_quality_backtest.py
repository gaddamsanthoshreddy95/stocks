"""Offline paired time-slot audit. Missing point-in-time signals remain unavailable."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import pandas as pd
from src.futures.entry_quality import evaluate,EntryQualityConfig,VERSION,number
from src.futures.sessions import ist,normalise_candles
from src.futures.backtest import FuturesIntradayBacktester,metrics
from src.futures.costs import FuturesCosts
from src.futures.config import FuturesScanConfig
from src.quality.futures_execution import clean

SLOTS=('09:30','09:45','10:00')


def compare(samples,histories,config=None,costs=None):
    """Samples are recorded input snapshots, histories are exact-contract outcome bars.

    Never uses later snapshots to fill earlier evidence. Outcomes are five-minute,
    stop-first-if-ambiguous simulations; intrabar decisions without a complete
    post-decision price path are not simulated. Both arms use the same inputs.
    """
    config=config or EntryQualityConfig();costs=costs or FuturesCosts()
    engine=FuturesIntradayBacktester(FuturesScanConfig(),costs)
    groups={}
    for sample in samples:
        key=(ist(sample['decision_at']).date().isoformat(),sample['symbol'],sample['side'])
        groups.setdefault(key,[]).append(sample)
    results=[];arms={slot:{'baseline':[],'framework':[]} for slot in SLOTS}
    for (day,symbol,side),recorded in sorted(groups.items()):
        for slot in SLOTS:
            now=ist(day+' '+slot)
            eligible=[s for s in recorded if 0<=(now-ist(s['decision_at'])).total_seconds()<=config.quote_age_seconds
                      and ist(s['available_at'])<=now]
            row={'day':day,'symbol':symbol,'side':side,'slot_ist':slot,'status':'UNAVAILABLE','baseline':None,'framework':None}
            if not eligible:
                row['reason']='POINT_IN_TIME_INPUT_NOT_RECORDED';results.append(row);continue
            sample=max(eligible,key=lambda s:ist(s['decision_at']))
            row['original_ranking_score']=sample.get('original_ranking_score')
            assessment=evaluate(sample,now,config);row['assessment']=assessment
            contract=(sample.get('contract') or {}).get('tradingsymbol');raw=histories.get(contract)
            if raw is None or raw.empty:
                row['reason']='EXACT_CONTRACT_OUTCOME_HISTORY_UNAVAILABLE';results.append(row);continue
            if 'tradingsymbol' not in raw or set(raw.tradingsymbol.dropna())!={contract}:
                row['reason']='OUTCOME_CONTRACT_IDENTITY_UNVERIFIED';results.append(row);continue
            try:frame=clean(normalise_candles(raw,'5minute'))
            except (ValueError,TypeError,KeyError):
                row['reason']='OUTCOME_HISTORY_INVALID';results.append(row);continue
            if now not in frame.index:
                row['reason']='POST_DECISION_FIVE_MINUTE_PATH_UNAVAILABLE';results.append(row);continue
            if sample.get('original_decision')!='APPROVED':
                row.update(status='NONACTIONABLE_BASELINE',reason='ORIGINAL_SIGNAL_NOT_APPROVED');results.append(row);continue
            plan=sample.get('plan') or {};quantity=number(plan.get('quantity'));entry=number(plan.get('entry'))
            if quantity is None or entry is None or quantity<=0 or entry<=0 or quantity!=int(quantity):
                row['reason']='RECORDED_EXECUTION_PLAN_UNAVAILABLE';results.append(row);continue
            # Entry is the originally observed executable estimate; raw OHLC is never rewritten.
            spread=sample.get('execution_spread_bps')
            if spread is None:
                row['reason']='HISTORICAL_SPREAD_UNAVAILABLE';results.append(row);continue
            trade=engine.simulate(frame,frame.index.get_loc(now),side,'RECORDED_SIGNAL',quantity,
                entry_reference=entry,exit_spread_bps=spread)
            if trade is None:
                row['reason']='OUTCOME_PATH_INCOMPLETE';results.append(row);continue
            trade.pop('_exit_index',None)
            row['baseline']=trade;arms[slot]['baseline'].append(trade)
            row.update(status='EVALUATED',reason=None)
            if assessment['classification']=='ENTER':
                row['framework']=trade.copy();arms[slot]['framework'].append(trade)
            row['missed_target_opportunity']=assessment['classification']!='ENTER' and trade['outcome']=='TARGET_FIRST'
            row['avoided_stop']=assessment['classification']!='ENTER' and trade['outcome']=='STOP_FIRST'
            results.append(row)
    summary={}
    for slot in SLOTS:
        subset=[r for r in results if r['slot_ist']==slot]
        summary[slot]={'coverage':dict(Counter(r['status'] for r in subset)),
            **{arm:metrics(trades,30) for arm,trades in arms[slot].items()},
            'missed_target_opportunities':sum(r.get('missed_target_opportunity',False) for r in subset),
            'avoided_stops':sum(r.get('avoided_stop',False) for r in subset),
            'withheld_candidates':sum(r.get('status')=='EVALUATED' and r.get('framework') is None for r in subset)}
    portfolio={}
    for slot in SLOTS:
        portfolio[slot]={}
        for arm in ('baseline','framework'):
            selected=[];unranked=0
            for day in sorted({r['day'] for r in results}):
                candidates=[r for r in results if r['slot_ist']==slot and r['day']==day and r.get(arm)]
                ranked=[r for r in candidates if isinstance(r.get('original_ranking_score'),(int,float))]
                unranked+=len(candidates)-len(ranked)
                conflicts={r['symbol'] for r in ranked if len({v['side'] for v in ranked if v['symbol']==r['symbol']})>1}
                ranked=sorted((r for r in ranked if r['symbol'] not in conflicts),key=lambda r:(-r['original_ranking_score'],r['symbol']))
                selected.extend(r[arm] for r in ranked[:2])
            portfolio[slot][arm]={'maximum_entries_per_day':2,'unranked_excluded':unranked,
                'metrics':metrics(selected,30),'scope':'ALTERNATIVE_FIXED_TIME_STRATEGY; EACH_SLOT_HAS_ITS_OWN_TWO_ENTRY_DAILY_CAP'}
    return {'version':VERSION,'status':'EVALUATED' if any(r['status']=='EVALUATED' for r in results) else 'INSUFFICIENT_POINT_IN_TIME_EVIDENCE',
        'scope':'PAIRED_RECORDED_SIGNALS; NOT_RECONSTRUCTED_NEWS_DEPTH_OR_WIN_PROBABILITY',
        'slots_ist':list(SLOTS),'policy':config.__dict__,'summary':summary,'comparisons':results,'portfolio_by_slot':portfolio,
        'slippage_assumptions':costs.__dict__,
        'outcome_resolution':'Completed post-decision 5-minute OHLC; ambiguous stop first; full exit-bar MAE is conservative',
        'trade_limit_scope':'Independent counterfactual slots; not a portfolio claiming more than two live entries',
        'calibration':'UNVALIDATED; missing samples are not losses or missed opportunities'}


def audit_saved_reports(paths):
    """Read-only coverage audit of actual saved reports; never consults current prices."""
    runs=[];samples=[]
    for filename in sorted(map(Path,paths)):
        content=filename.read_bytes()
        try:report=json.loads(content)
        except (ValueError,UnicodeError):continue
        if not isinstance(report,dict) or not isinstance(report.get('reviewed'),list):continue
        candidates=report['reviewed'];captured=[i['entry_quality_input'] for i in candidates if i.get('entry_quality_input')]
        samples.extend(captured)
        runs.append({'file':str(filename),'sha256':sha256(content).hexdigest(),'generated_at':report.get('generated_at'),
            'original_candidates':len(candidates),'recorded_entry_quality_inputs':len(captured),
            'file_unchanged':sha256(filename.read_bytes()).hexdigest()==sha256(content).hexdigest()})
    paired=compare(samples,{})
    return {'status':paired['status'],'historical_sources':runs,'saved_runs':len(runs),
        'original_candidate_records':sum(r['original_candidates'] for r in runs),
        'recorded_input_snapshots':len(samples),'comparison':paired,
        'coverage_by_slot':{slot:{'archived_candidate_records':sum(r['original_candidates'] for r in runs),
            'records_without_point_in_time_input':sum(r['original_candidates']-r['recorded_entry_quality_inputs'] for r in runs),
            'paired_outcome_evaluations':paired['summary'][slot]['coverage'].get('EVALUATED',0)} for slot in SLOTS},
        'limitations':['No current data is substituted for historical input.',
            'Legacy reports without timestamped source inputs cannot establish 09:30/09:45/10:00 policy performance.',
            'A real replay additionally needs exact-contract post-decision OHLC and recorded execution costs/quotes.']}
