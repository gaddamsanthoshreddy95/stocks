"""Offline point-in-time discovery replay and exact-contract Futures simulation.

The caller supplies archived eligibility snapshots and adjustment provenance.
Current instrument masters cannot establish historical universe membership.
"""
from dataclasses import replace
from collections import defaultdict
import pandas as pd
from src.futures_workspace.discovery import universe,technical
from src.futures_workspace.store import LISTS
from src.futures_workspace.config import WorkspaceConfig
from src.futures.sessions import ist,normalise_candles
from src.futures.config import FuturesScanConfig
from src.futures.backtest import FuturesIntradayBacktester
from src.futures.costs import FuturesCosts


def decode_frame(payload):
    frame=pd.DataFrame(payload.get('rows',[]))
    if frame.empty:
        return frame
    frame.index=pd.DatetimeIndex(frame.pop('date'))
    frame.attrs['price_adjustment']=payload.get('price_adjustment','UNKNOWN')
    return frame


def summarize(trades):
    if not trades:
        return {'signals':0,'win_rate':None,'average_win':None,'average_loss':None,'profit_factor':None,'net_expectancy':None,'maximum_drawdown':None}
    pnl=[float(t['net_pnl']) for t in trades]
    wins=[p for p in pnl if p>0]; losses=[p for p in pnl if p<0]
    equity=peak=drawdown=0
    for t in sorted(trades,key=lambda t:t['exit_timestamp']):
        equity+=t['net_pnl']; peak=max(peak,equity); drawdown=max(drawdown,peak-equity)
    return {'signals':len(pnl),'win_rate':len(wins)/len(pnl)*100 if len(pnl)>=30 else None,'observed_win_rate':len(wins)/len(pnl)*100,'average_win':sum(wins)/len(wins) if wins else None,
        'average_loss':sum(losses)/len(losses) if losses else None,'profit_factor':sum(wins)/abs(sum(losses)) if losses else None,
        'net_expectancy':sum(pnl)/len(pnl),'maximum_drawdown':drawdown,
        'sample_status':'SUFFICIENT' if len(pnl)>=30 else 'INSUFFICIENT','future_probability':None}

class WorkspaceResearchReplay:
    def __init__(self,config=None):
        self.config=config or WorkspaceConfig()

    def run(self,payload):
        snapshots=payload.get('universe_snapshots',[])
        if not snapshots:
            return {'status':'UNKNOWN','reason':'POINT_IN_TIME_FULL_UNIVERSE_SNAPSHOTS_REQUIRED','future_probability':None}
        equity={s:decode_frame(f) for s,f in payload.get('equity_histories',{}).items()}
        futures=payload.get('futures_histories',{})
        selections=[]; failures=[]; trades=[]; seen=set(); diagnostics=[]
        snapshots=sorted(snapshots,key=lambda s:ist(s['as_of']))
        cfg=replace(FuturesScanConfig.from_env(),entry_cutoff_hour=self.config.entry_cutoff_hour,
                    entry_cutoff_minute=self.config.entry_cutoff_minute,intraday_exit_hour=self.config.flat_hour,
                    intraday_exit_minute=self.config.flat_minute)
        for i,snapshot in enumerate(snapshots):
            at=ist(snapshot['as_of'])
            if not snapshot.get('source') or not snapshot.get('complete_universe') or ist(snapshot.get('recorded_at',at+pd.Timedelta(days=1)))>at:
                failures.append({'as_of':str(at),'reason':'UNVERIFIED_POINT_IN_TIME_UNIVERSE'}); continue
            contracts=universe(snapshot.get('instruments',[]),at)
            until=ist(snapshots[i+1]['as_of']) if i+1<len(snapshots) else at+pd.Timedelta(days=7)
            for symbol,contract in contracts.items():
                raw=equity.get(symbol)
                if raw is None:
                    failures.append({'symbol':symbol,'reason':'HISTORICAL_EQUITY_MISSING'}); continue
                # Publication-time corporate-action adjustments are supplied by caller.
                prefix=normalise_candles(raw,'day',at)
                prefix.attrs.update(raw.attrs)
                result=technical(prefix,self.config,at)
                if result['classification'] not in LISTS:
                    continue
                side='SHORT' if result['classification']==LISTS[0] else 'LONG'
                selections.append({'symbol':symbol,'side':side,'selected_at':at.isoformat(),'classification':result,
                    'contract':contract['tradingsymbol']})
                f_payload=futures.get(contract['tradingsymbol'])
                if f_payload is None:
                    failures.append({'symbol':symbol,'reason':'EXACT_CONTRACT_HISTORY_MISSING'}); continue
                # Equity continuation measures selection quality only, not Futures profit.
                future=normalise_candles(raw,'day').loc[lambda f:f.index>at]
                if len(future)>=5:
                    move=float((future.Close.iloc[4]/prefix.Close.iloc[-1]-1)*100)
                    diagnostics.append({'symbol':symbol,'side':side,'as_of':at.isoformat(),'five_session_return_percent':move,
                        'failed_recovery':side=='LONG' and move<0,'false_bearish':side=='SHORT' and move>0})
                bars=decode_frame(f_payload)
                if bars.empty:
                    continue
                bars=normalise_candles(bars,'5minute',until)
                bars=bars.loc[bars.index.date<=pd.Timestamp(contract['expiry']).date()]
                def signals(prefix_frame):
                    from src.futures.scoring import directional_setup
                    stamp=prefix_frame.index[-1]
                    if not at<=stamp<until:
                        return []
                    setup=directional_setup(prefix_frame,side,config=cfg,prepared=True)
                    if setup.get('technical_score') is None or setup['technical_score']<cfg.minimum_score or setup.get('timing')!='READY' or not setup.get('confirmed'):
                        return []
                    return [{'side':side,'setup_type':setup['setup_type'],'entry_trigger':setup.get('evidence',{}).get('short_trigger_price') if side=='SHORT' else None,
                             'setup_invalidation_price':setup.get('evidence',{}).get('setup_invalidation_price') if side=='SHORT' else None}]
                try:
                    simulation=FuturesIntradayBacktester(cfg,FuturesCosts.from_env()).run(bars,contract['lot_size'],signal_factory=signals)
                except (ValueError,KeyError,TypeError) as exc:
                    failures.append({'symbol':symbol,'reason':'BACKTEST_UNAVAILABLE:'+type(exc).__name__}); continue
                for trade in simulation.get('trades',[]):
                    identity=(contract['tradingsymbol'],trade['side'],trade['entry_timestamp'])
                    if identity in seen:
                        continue
                    seen.add(identity)
                    trades.append({**trade,'symbol':symbol,'contract':contract['tradingsymbol'],'selected_at':at.isoformat(),
                        'sector':snapshot.get('sectors',{}).get(symbol,'UNKNOWN'),'regime':'UNKNOWN'})
        trades=[t for t in trades if t.get('partition')=='OUT_OF_SAMPLE']
        by_sector={s:summarize([t for t in trades if t['sector']==s]) for s in sorted({t['sector'] for t in trades})}
        return {'status':'AVAILABLE_RESEARCH_ONLY','selection_count':len(selections),'selections':selections,
            'selection_diagnostics':diagnostics,'failed_recoveries':sum(d['failed_recovery'] for d in diagnostics),
            'false_bearish_signals':sum(d['false_bearish'] for d in diagnostics),'futures_metrics':summarize(trades),
            'by_sector':by_sector,'by_regime':{'UNKNOWN':summarize(trades)},'trades':trades,'failures':failures,
            'cost_status':'PROVISIONAL_EXISTING_COSTS_AND_SLIPPAGE','universe_status':'CALLER_ARCHIVED_POINT_IN_TIME',
            'fundamental_contribution':'UNKNOWN_NO_POINT_IN_TIME_ABLATION','news_contribution':'UNKNOWN_NO_POINT_IN_TIME_ABLATION',
            'market_bias_effects':'UNKNOWN_NO_POINT_IN_TIME_BIAS_HISTORY',
            'limitations':['Technical research simulation; historical broker books, depth, news and full approval gates are unavailable.',
                          'Archived coverage and corporate-action adjustment provenance must be independently verified.',
                          'No calibrated future win probability. Directional simulations do not enforce a portfolio-wide two-entry day.'],
            'entry_cutoff_ist':f'{cfg.entry_cutoff_hour:02}:{cfg.entry_cutoff_minute:02}','flat_deadline_ist':f'{cfg.intraday_exit_hour:02}:{cfg.intraday_exit_minute:02}'}
