"""Completed NSE-session fifteen-minute context derived from verified five-minute bars."""
import pandas as pd
from src.futures.sessions import ist, normalise_candles, completed_session_check
from src.quality.futures_execution import clean


def completed_bars(raw, now):
    now=ist(now)
    data=normalise_candles(raw,'5minute',now)
    if completed_session_check(data,now)['status']!='PASS':
        raise ValueError('FIFTEEN_MINUTE_SOURCE_SESSION_INCOMPLETE')
    data=clean(data.loc[data.index.date==now.date()])
    start=now.normalize()+pd.Timedelta(hours=9,minutes=15)
    rows=[];index=[]
    for stamp in pd.date_range(start,now,freq='15min'):
        if stamp+pd.Timedelta(minutes=15)>now:break
        required=pd.date_range(stamp,periods=3,freq='5min')
        if not required.isin(data.index).all():raise ValueError('FIFTEEN_MINUTE_SOURCE_BAR_MISSING')
        group=data.loc[required]
        rows.append([float(group.Open.iloc[0]),float(group.High.max()),float(group.Low.min()),
            float(group.Close.iloc[-1]),float(group.Volume.sum())]);index.append(stamp)
    return pd.DataFrame(rows,index=pd.DatetimeIndex(index),columns=['Open','High','Low','Close','Volume'])


def evaluate(stock, futures, now, side, level_bars=2, retest_age=2):
    result={'status':'UNKNOWN','reason_codes':[],'checked_at':ist(now).isoformat(),
        'basis':'COMPLETE_3_X_5_MINUTE_GROUPS_ANCHORED_0915_IST','instruments':{}}
    if side not in ('LONG','SHORT'):raise ValueError('Invalid direction')
    sign=1 if side=='LONG' else -1
    try:
        for name,raw in (('stock',stock),('futures',futures)):
            frame=completed_bars(raw,now)
            evidence={'completed_bars':len(frame),'last_bar_at':frame.index[-1].isoformat() if len(frame) else None}
            result['instruments'][name]=evidence
            if len(frame)<level_bars+2:
                evidence['status']='WAIT';result['reason_codes'].append(side+'_15M_'+name.upper()+'_BREAK_RETEST_NOT_YET_AVAILABLE');continue
            volume=frame.Volume.cumsum()
            if (volume<=0).any():raise ValueError('FIFTEEN_MINUTE_VWAP_VOLUME_UNAVAILABLE')
            vw=(((frame.High+frame.Low+frame.Close)/3)*frame.Volume).cumsum()/volume
            trend=sign*(frame.Close.iloc[-1]-vw.iloc[-1])>0 and sign*(vw.iloc[-1]-vw.iloc[-2])>=0
            found=None
            for b in range(level_bars,len(frame)-1):
                prior=frame.iloc[b-level_bars:b]
                level=float(prior.High.max() if sign==1 else prior.Low.min())
                if sign*(frame.Close.iloc[b]-level)<=0 or sign*(frame.Close.iloc[b-1]-level)>0:continue
                for r in range(max(b+1,len(frame)-retest_age),len(frame)):
                    bar=frame.iloc[r]
                    touched=bar.Low<=level if sign==1 else bar.High>=level
                    if (touched and sign*(bar.Close-level)>0 and sign*(bar.Close-bar.Open)>0
                            and sign*(bar.Close-frame.Close.iloc[r-1])>0 and sign*(frame.Close.iloc[-1]-bar.Close)>=0):
                        found={'level':level,'break_at':frame.index[b].isoformat(),'retest_at':frame.index[r].isoformat(),
                            'trigger':float(bar.High if sign==1 else bar.Low),
                            'invalidation':float(bar.Low if sign==1 else bar.High)}
            evidence.update(status='BLOCKED' if not trend else 'PASS' if found else 'WAIT',
                vwap=float(vw.iloc[-1]),vwap_change=float(vw.iloc[-1]-vw.iloc[-2]),retest=found)
            if not trend:result['reason_codes'].append(side+'_15M_'+name.upper()+'_TREND_CONFLICT')
            elif not found:result['reason_codes'].append(side+'_15M_'+name.upper()+'_COMPLETED_RETEST_REQUIRED')
        states=[v['status'] for v in result['instruments'].values()]
        result['status']='BLOCKED' if 'BLOCKED' in states else 'WAIT' if 'WAIT' in states else 'PASS'
        if result['status']=='PASS':result['reason_codes']=[side+'_COMPLETED_15M_TREND_AND_RETEST_CONFIRMED']
    except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError) as exc:
        result['reason_codes']=[str(exc)]
    return result
