"""Offline confirmation replay using recorded decision-time snapshots only.

This measures confirmation timing, not the full scanner's approval policy.
No missing quote, OI, news, or depth is reconstructed from subsequent prices.
"""
import pandas as pd
from types import SimpleNamespace

from src.futures.short_confirmation import evaluate as short_confirmation
from src.futures.long_confirmation import evaluate as long_confirmation
from src.futures.sessions import ist, normalise_candles
from src.futures.backtest import FuturesIntradayBacktester, metrics


def replay(snapshots, futures_candles, lot_size, config):
    frame=normalise_candles(futures_candles,'5minute')
    engine=FuturesIntradayBacktester(config=config)
    decisions=[];trades=[];occupied={}
    ordered=sorted(snapshots,key=lambda s:ist(s['decision_at']))
    for snapshot in ordered:
        now=ist(snapshot['decision_at']);side=snapshot['side']
        if side not in ('LONG','SHORT'):raise ValueError('Invalid replay direction')
        if ist(snapshot['available_at'])>now:
            decisions.append({'decision_at':now.isoformat(),'side':side,'status':'UNKNOWN',
                'reason_codes':['SNAPSHOT_NOT_AVAILABLE_AT_DECISION']})
            continue
        data=dict(snapshot['data'])
        for key in FRAME_KEYS:
            if isinstance(data.get(key),list):
                rows=data[key]
                data[key]=pd.DataFrame(rows)
                if rows:
                    data[key].index=pd.to_datetime(data[key].pop('timestamp'),utc=True).dt.tz_convert('Asia/Kolkata')
        oi=snapshot['oi_check']
        if isinstance(oi,dict):oi=SimpleNamespace(status=oi['status'],factors=oi.get('factors',{}))
        gate=(short_confirmation if side=='SHORT' else long_confirmation)(data,oi,now,config)
        decisions.append({'decision_at':now.isoformat(),'side':side,**gate})
        if gate['status']!='PASS' or (side in occupied and now<=occupied[side]):continue
        # Next bar must start at the decision instant: no filling at a past open.
        if now not in frame.index:
            decisions[-1]['simulation_status']='NO_FIVE_MINUTE_BAR_STARTING_AT_DECISION'
            continue
        retest=gate['evidence'].get('breakdown_retest' if side=='SHORT' else 'breakout_retest') or {}
        trade=engine.simulate(frame,frame.index.get_loc(now),side,'CONFIRMATION_REPLAY',lot_size,
            entry_trigger=retest.get('trigger'),invalidation=retest.get('invalidation'))
        decisions[-1]['simulation_status']='SIMULATED' if trade else 'NO_STOP_COMPATIBLE_OR_COMPLETE_SIMULATION'
        if trade:
            occupied[side]=frame.index[trade.pop('_exit_index')]
            trade['decision_at']=now.isoformat();trades.append(trade)
    return {'scope':'CONFIRMATION_ONLY; NOT_FULL_SCANNER_OR_LIVE_FILL_VALIDATION',
        'fill_basis':'NEXT_BAR_OPEN_OR_TRIGGER; FIVE_MINUTE_STOP_FIRST_IF_AMBIGUOUS',
        'decisions':decisions,'trades':trades,'metrics':metrics(trades,config.minimum_historical_signals),
        'groups':{side:metrics([t for t in trades if t['side']==side],config.minimum_historical_signals)
            for side in ('LONG','SHORT')},
        'time_groups':{hour:metrics([t for t in trades if t['entry_timestamp'][11:13]==hour],config.minimum_historical_signals)
            for hour in sorted({t['entry_timestamp'][11:13] for t in trades})}}



FRAME_KEYS=('underlying_intraday','intraday','short_nifty_intraday','short_sector_intraday')


def capture(data,oi_check,now,side):
    """JSON-safe current-session evidence, bounded to 75 completed bars per input."""
    now=ist(now)
    saved={k:data.get(k) for k in ('instrument_token',)}
    for key in ('quote','spot_quote'):
        raw=data.get(key) or {}
        saved[key]={k:raw.get(k) for k in ('timestamp','last_price','instrument_token')}
        if saved[key]['timestamp'] is not None:saved[key]['timestamp']=str(saved[key]['timestamp'])
    for key in FRAME_KEYS:
        try:
            frame=normalise_candles(data.get(key),'5minute',now)
            frame=frame.loc[frame.index.date==now.date()].tail(75)
            saved[key]=[{'timestamp':stamp.isoformat(),**{c:float(row[c]) for c in ('Open','High','Low','Close','Volume')}}
                for stamp,row in frame.iterrows()]
        except (ValueError,TypeError,KeyError,AttributeError):saved[key]=[]
    return {'decision_at':now.isoformat(),'available_at':now.isoformat(),'side':side,'data':saved,
        'oi_check':{'status':oi_check.status,'factors':dict(oi_check.factors)}}
