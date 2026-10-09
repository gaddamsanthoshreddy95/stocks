"""Additional historical evidence, isolated from existing backtest metrics/decisions."""
from collections import defaultdict
from copy import deepcopy
from math import sqrt, isfinite
from statistics import mean
from dataclasses import asdict
import os
import pandas as pd
from src.futures.cache import fingerprint

VERSION=2
from src.futures.sessions import CALCULATION_VERSION, normalise_candles
UNAVAILABLE_FEATURES=['Point-in-time company filings and valuation','Historical institutional/delivery data',
                      'Historical news and corporate-event coverage','Historical OI, bid/ask quotes and order book',
                      'Historical sector/market-relative confirmation where absent']


from src.futures.backtest import FuturesIntradayBacktester


class ReportEBacktester(FuturesIntradayBacktester):
    """Separate evidence-only prefix filters; the original backtester is unchanged."""
    def __init__(self,config=None,costs=None):
        super().__init__(config,costs)
        from src.quality.futures_execution import FuturesExecutionConfig
        self.execution_limits=FuturesExecutionConfig.from_env()
        self.historical_daily=None

    def run(self,candles,lot_size,**kwargs):
        self.historical_daily=normalise_candles(kwargs['daily_history'], 'day') if kwargs.get('daily_history') is not None else None
        result=super().run(candles,lot_size,**kwargs)
        result['historical_oi_status']='POINT_IN_TIME_USED' if 'OI' in candles and self.historical_daily is not None and 'OI' in self.historical_daily else 'UNAVAILABLE'
        result['report_e_timing_validated']=kwargs.get('signal_factory') is None
        result['report_e_entry_definition']='Completed-prefix core READY plus reconstructible Report E EMA/RSI/MACD/ADX/VWAP/ATR/matched-time-volume and available point-in-time OI; next-bar execution'
        return result

    def _signals(self,prefix,market_regime='UNKNOWN',**kwargs):
        holidays={v.strip() for v in os.getenv('MARKET_HOLIDAYS_IST','').split(',')}
        if prefix.index[-1].weekday()>=5 or prefix.index[-1].date().isoformat() in holidays:
            return []
        candidates=super()._signals(prefix,market_regime=market_regime,**kwargs)
        if not candidates:return []
        row,previous=prefix.iloc[-1],prefix.iloc[-2]
        session=prefix.loc[prefix.index.date==prefix.index[-1].date()]
        expected=pd.date_range(prefix.index[-1].normalize()+pd.Timedelta(hours=9,minutes=15),prefix.index[-1],freq='5min')
        if not session.index.equals(expected):return []
        volume=float(session.Volume.sum())
        if volume<=0:return []
        baseline=prefix.loc[(prefix.index.date<prefix.index[-1].date()) & (prefix.index.time<=prefix.index[-1].time())]
        times=list(session.index.strftime('%H:%M'))
        totals=[float(group.Volume.sum()) for _,group in baseline.groupby(baseline.index.date)
                if list(group.index.strftime('%H:%M'))==times][-20:]
        limits=self.execution_limits
        if len(totals)<limits.minimum_volume_sessions or sum(totals)<=0 or volume/(sum(totals)/len(totals))<limits.minimum_rvol:
            return []
        daily_atr=kwargs.get('daily_atr')
        if daily_atr is None or not isfinite(daily_atr) or not limits.minimum_daily_atr_percent<=daily_atr/row.Close*100<=limits.maximum_daily_atr_percent:
            return []
        if not isfinite(row.ATR) or not limits.minimum_five_minute_atr_percent<=row.ATR/row.Close*100<=limits.maximum_five_minute_atr_percent:
            return []
        vwap=float((((session.High+session.Low+session.Close)/3)*session.Volume).sum()/volume)
        output=[]
        for signal in candidates:
            sign=1 if signal['side']=='LONG' else -1
            values=(row.RSI,previous.RSI,row.ADX,row.RVOL,row.MACD_HISTOGRAM)
            if not all(isfinite(v) for v in values):continue
            if not (50<row.RSI<70 if signal['side']=='LONG' else 30<row.RSI<50):continue
            if 'OI' in prefix and self.historical_daily is not None and 'OI' in self.historical_daily:
                past=self.historical_daily.loc[self.historical_daily.index.date<prefix.index[-1].date()]
                if past.empty:continue
                old=past.iloc[-1]
                if not all(isfinite(v) and v>0 for v in (row.OI,old.OI,old.Close)):
                    continue
                # The live scanner accepts known price/OI regimes but still
                # requires directional alignment for Report E timing.
                if (row.Close/old.Close-1)*sign<=0:continue
                if signal['side']=='SHORT' and 'OI' in previous and previous.OI>0 and previous.Close>0:
                    if (row.Close/previous.Close-1)*100>=self.config.short_covering_price_percent and (row.OI/previous.OI-1)*100<=-self.config.short_covering_oi_percent:
                        continue
            if ((row.RSI-previous.RSI)*sign<=0 or (row.EMA9-row.EMA21)*sign<=0
                    or row.ADX<self.config.minimum_adx or row.ADX<=limits.minimum_adx or row.RVOL<self.config.minimum_rvol
                    or row.MACD_HISTOGRAM*sign<=0 or (row.Close-vwap)*sign<=0
                    or (row.Close-row.Open)*sign<=0):
                continue
            if signal['side']=='LONG':
                trigger=float(prefix.High.iloc[-21:-1].max())
                if row.Close<=trigger:
                    continue
                signal['entry_trigger']=trigger
            output.append(signal)
        return output

    def simulate(self,frame,start,side,setup_type,lot_size,**kwargs):
        trigger=kwargs.get('entry_trigger')
        if side=='LONG' and trigger is not None and float(frame.iloc[start].Open)<trigger:
            return None  # Do not infer a fill on an unconfirmed gap back below trigger.
        return super().simulate(frame,start,side,setup_type,lot_size,**kwargs)


def policy_key(config,costs,e_config,contract):
    from src.quality.futures_execution import FuturesExecutionConfig
    from src.futures.costs import fee_schedule
    return fingerprint({'version':VERSION,'calculation_version':CALCULATION_VERSION,'fee_schedule_version':fee_schedule()['version'],'entry_definition':'REPORT_E_PREFIX_TECHNICAL_READY_NEXT_BAR',
                        'strategy':asdict(config),'cost_model':asdict(costs),
                        'spread_bps':e_config.historical_spread_bps,'contract':contract,
                        'execution_thresholds':asdict(FuturesExecutionConfig.from_env()),'holidays':os.getenv('MARKET_HOLIDAYS_IST',''),
                        'movement_basis':'FUTURES','historical_features_unavailable':UNAVAILABLE_FEATURES})


def unknown_history(reason='NO VALID CACHED HISTORICAL EVIDENCE',minimum=30):
    return {'status':'UNKNOWN','reason':reason,'minimum_samples':minimum,'sample_count':None,
            'target_first_count':None,'stop_first_count':None,'time_exit_count':None,
            'target_first_percent':None,'stop_first_percent':None,'time_exit_percent':None,
            'confidence_interval_95_percent':None,'net_expectancy':None,'break_even_target_first_percent':None,
            'net_reward_risk':None,'cost_model_status':'UNKNOWN','cost_adjusted_expectancy_status':'UNKNOWN',
            'unavailable_historical_features':UNAVAILABLE_FEATURES,'forecast_probability':None}


def wilson(targets,total):
    if not total:return None
    z=1.959963984540054; p=targets/total; denom=1+z*z/total
    center=(p+z*z/(2*total))/denom
    half=z*sqrt(p*(1-p)/total+z*z/(4*total*total))/denom
    return [max(0,center-half)*100,min(1,center+half)*100]


def summarise_trades(trades,minimum,spread_bps=None):
    # Do not mutate legacy trades, costs or their metrics. Spread is an explicit
    # per-leg half-spread charge; existing fees/slippage are charged exactly once.
    adjusted=[]
    for original in trades:
        trade=deepcopy(original)
        required=('entry','exit','quantity','net_pnl','gross_pnl','total_costs')
        if any(not isinstance(trade.get(k),(int,float)) or not isfinite(trade[k]) for k in required):
            continue
        if trade['entry']<=0 or trade['exit']<=0 or trade['quantity']<=0 or int(trade['quantity'])!=trade['quantity'] or trade['total_costs']<0:
            continue
        if not trade.get('cost_breakdown') or any(not isinstance(v,(int,float)) or not isfinite(v) or v<0 for v in trade['cost_breakdown'].values()):
            continue
        if trade.get('outcome') not in ('TARGET_FIRST','STOP_FIRST','INTRADAY_EXIT'):
            continue
        existing=sum(trade.get('cost_breakdown',{}).values())
        if not trade.get('cost_breakdown') or abs(existing-trade['total_costs'])>1e-5 or abs(trade['gross_pnl']-trade['total_costs']-trade['net_pnl'])>1e-5:
            continue
        # The base engine has no separate spread charge. Reject incompatible data
        # rather than silently adding a second spread deduction.
        if 'spread' in trade['cost_breakdown']:
            continue
        spread=(trade['entry']+trade['exit'])*trade['quantity']*spread_bps/20000 if spread_bps is not None else 0
        trade['report_e_spread_cost']=spread
        trade['report_e_total_costs']=trade['total_costs']+spread
        trade['report_e_net_pnl']=trade['net_pnl']-spread
        adjusted.append(trade)
    n=len(adjusted)
    result=unknown_history('INSUFFICIENT VALID SAMPLES',minimum)
    groups={o:[t for t in adjusted if t['outcome']==o] for o in ('TARGET_FIRST','STOP_FIRST','INTRADAY_EXIT')}
    result.update(sample_count=n,target_first_count=len(groups['TARGET_FIRST']),stop_first_count=len(groups['STOP_FIRST']),time_exit_count=len(groups['INTRADAY_EXIT']),
                  observed_scope='Chronological out-of-sample technical setup outcomes; not the full live approval strategy',
                  cost_model_status='PROVISIONAL',cost_assumptions={'historical_spread_bps':spread_bps,'spread_source':'Explicit hypothesis' if spread_bps is not None else 'UNKNOWN',
                  'spread_charged_once':'Half the configured spread on each leg; existing slippage and fees retained once',
                  'calibration':'Broker contract-note / historical fill calibration unavailable'})
    if n<minimum:return result
    average=lambda rows,key:mean(t[key] for t in rows) if rows else None
    target,stop,timed=(groups[o] for o in ('TARGET_FIRST','STOP_FIRST','INTRADAY_EXIT'))
    target_net,stop_net,time_net=(average(rows,'report_e_net_pnl') for rows in (target,stop,timed))
    expected=sum(len(rows)/n*average(rows,'report_e_net_pnl') for rows in (target,stop,timed) if rows)
    q=len(timed)/n
    required_rate=None
    if target_net is not None and stop_net is not None and target_net>stop_net:
        required_rate=-((1-q)*stop_net+q*(time_net or 0))/(target_net-stop_net)*100
    equity=peak=drawdown=0
    for trade in sorted(adjusted,key=lambda t:t['exit_timestamp']):
        equity+=trade['report_e_net_pnl'];peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    gross_target,gross_stop=average(target,'gross_pnl'),average(stop,'gross_pnl')
    result.update(status='OBSERVED_OUT_OF_SAMPLE',reason=None,target_first_percent=len(target)/n*100,stop_first_percent=len(stop)/n*100,
                  time_exit_percent=len(timed)/n*100,confidence_interval_95_percent=wilson(len(target),n),
                  gross_target_profit=gross_target,gross_stop_loss=gross_stop,net_target_profit=target_net,net_stop_loss=stop_net,
                  average_net_time_exit_pnl=time_net,net_expectancy=expected,cost_adjusted_expectancy_status='PROVISIONAL',
                  break_even_target_first_percent=required_rate,
                  break_even_feasible=0<=required_rate<=100*(1-q) if required_rate is not None else None,
                  gross_reward_risk=gross_target/abs(gross_stop) if gross_target is not None and gross_stop else None,
                  net_reward_risk=target_net/abs(stop_net) if target_net is not None and stop_net else None,
                  total_transaction_cost_estimate=sum(t['report_e_total_costs'] for t in adjusted),
                  average_transaction_cost=average(adjusted,'report_e_total_costs'),
                  average_net_winning_trade=average([t for t in adjusted if t['report_e_net_pnl']>0],'report_e_net_pnl'),
                  average_net_losing_trade=average([t for t in adjusted if t['report_e_net_pnl']<0],'report_e_net_pnl'),
                  maximum_drawdown=drawdown,unavailable_historical_features=UNAVAILABLE_FEATURES,
                  uncertainty='Wilson interval assumes independent outcomes and measures sampling uncertainty only; correlated signals may widen uncertainty. Technical filters and estimated costs are not calibrated forecasts.')
    return result


def build_history_evidence(result,config,e_config,*,instrument=None,period=None):
    if result.get('movement_basis')!='FUTURES' or result.get('ambiguous_candle_policy')!='STOP_FIRST':
        return {'status':'UNKNOWN','reason':'INCOMPATIBLE MOVEMENT BASIS OR AMBIGUITY POLICY','groups':{}}
    grouped=defaultdict(list)
    seen=set()
    for t in result.get('trades',[]):
        if t.get('side') not in ('LONG','SHORT') or not t.get('setup_type'):
            continue
        try:
            signal=pd.Timestamp(t['signal_timestamp']);entry=pd.Timestamp(t['entry_timestamp']);exit_stamp=pd.Timestamp(t['exit_timestamp'])
            if signal.tz is None or entry.tz is None or exit_stamp.tz is None:
                continue
            if entry.date()!=exit_stamp.date() or entry!=signal+pd.Timedelta(minutes=5) or not (9,20)<=(entry.hour,entry.minute)<(config.intraday_exit_hour,config.intraday_exit_minute):
                continue
            if exit_stamp<entry or (exit_stamp.hour,exit_stamp.minute)>(config.intraday_exit_hour,config.intraday_exit_minute):
                continue
            if entry.weekday()>=5:
                continue
            sign=1 if t['side']=='LONG' else -1
            if abs(t['target']/t['entry']-(1+sign*config.target_fraction))>1e-8 or abs(t['stop_loss']/t['entry']-(1-sign*config.stop_fraction))>1e-8:
                continue
        except (ValueError,KeyError,TypeError,ZeroDivisionError):
            continue
        partition=t.get('partition')
        if partition not in ('IN_SAMPLE','OUT_OF_SAMPLE'):continue
        holdout=stamp_date(result.get('holdout_start'))
        if holdout is None or (partition=='OUT_OF_SAMPLE')!=(signal.date()>=holdout):
            continue
        identity=(t['side'],t['setup_type'],t['signal_timestamp'],t['entry_timestamp'])
        if identity in seen:continue
        seen.add(identity)
        grouped[f"{t['side']}:{t['setup_type']}:{partition}"].append(t)
    groups={key:summarise_trades(rows,config.minimum_historical_signals,e_config.historical_spread_bps) for key,rows in grouped.items()}
    unavailable=list(UNAVAILABLE_FEATURES)
    if result.get('historical_oi_status')=='POINT_IN_TIME_USED':
        unavailable=[v.replace('Historical OI, bid/ask quotes and order book','Historical bid/ask quotes and order book') for v in unavailable]
    for key,group in groups.items():
        group['unavailable_historical_features']=unavailable
        group['historical_oi_status']=result.get('historical_oi_status','UNAVAILABLE')
        partition=key.rsplit(':',1)[-1]
        group['partition']=partition
        if partition=='IN_SAMPLE':
            if group['status']=='OBSERVED_OUT_OF_SAMPLE':group['status']='OBSERVED_IN_SAMPLE'
            group['observed_scope']='In-sample technical setup outcomes; not out-of-sample validation or a forecast'
    return {'status':'AVAILABLE' if groups else 'UNKNOWN','reason':None if groups else 'NO VALID TRADES',
            'version':VERSION,'movement_basis':'FUTURES','target_fraction':config.target_fraction,'stop_fraction':config.stop_fraction,
            'instrument':instrument,'period':period,'holdout_start':result.get('holdout_start'),
            'entry_definition':result.get('report_e_entry_definition','Legacy technical subset only; Report E timing gates not validated'),
            'report_e_timing_validated':result.get('report_e_timing_validated',False),
            'cost_model_status':'PROVISIONAL','cost_assumptions':result.get('cost_assumptions'),
            'historical_basis':result.get('historical_basis'),'unavailable_historical_features':unavailable,
            'groups':groups}


def stamp_date(value):
    try:return pd.Timestamp(value).date() if value else None
    except (ValueError,TypeError):return None


def candidate_history(evidence,side,setup,minimum,contract,config):
    if not evidence:return unknown_history(minimum=minimum)
    if evidence.get('instrument')!=contract or evidence.get('movement_basis')!='FUTURES':
        return unknown_history('INCOMPATIBLE HISTORICAL INSTRUMENT OR MOVEMENT BASIS',minimum)
    if not evidence.get('report_e_timing_validated'):
        return unknown_history('LEGACY BACKTEST DOES NOT VERIFY REPORT E TIMING CONDITIONS',minimum)
    if evidence.get('target_fraction')!=.003 or evidence.get('stop_fraction')!=.002 or config.get('target_fraction')!=.003 or config.get('stop_fraction')!=.002:
        return unknown_history('HISTORICAL TARGET/STOP POLICY REQUIRES REVIEW',minimum)
    oos=deepcopy(evidence.get('groups',{}).get(f'{side}:{setup}:OUT_OF_SAMPLE') or unknown_history('INSUFFICIENT VALID SAMPLES',minimum))
    if not isinstance(oos.get('sample_count'),int) or oos['sample_count']<minimum:
        oos.update(status='UNKNOWN',reason='INSUFFICIENT VALID SAMPLES',target_first_percent=None,confidence_interval_95_percent=None,net_expectancy=None)
    oos.update(instrument=contract,period=evidence.get('period'),holdout_start=evidence.get('holdout_start'),
               saved_at=evidence.get('saved_at'),expires_at=evidence.get('expires_at'),entry_definition=evidence.get('entry_definition'),
               in_sample=evidence.get('groups',{}).get(f'{side}:{setup}:IN_SAMPLE'),forecast_probability=None)
    return oos


def input_windows(frames):
    windows={}
    for name,frame in frames.items():
        if frame is not None and not frame.empty:
            windows[name]={'start':frame.index[0].isoformat(),'end':frame.index[-1].isoformat(),
                           'columns':list(frame.columns)}
    return windows


def inputs_match(evidence,frames):
    from src.futures.cache import candle_fingerprint
    for name,window in evidence.get('input_windows',{}).items():
        frame=frames.get(name)
        if frame is None or any(c not in frame for c in window['columns']):return False
        subset=frame.loc[(frame.index>=pd.Timestamp(window['start'])) & (frame.index<=pd.Timestamp(window['end'])),window['columns']]
        if candle_fingerprint(subset)!=evidence.get('input_fingerprints',{}).get(name):return False
    return bool(evidence.get('input_windows'))
