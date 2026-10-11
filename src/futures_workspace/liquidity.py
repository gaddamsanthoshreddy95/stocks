"""Completed-session liquidity evidence for research; never entry approval."""
import math
import pandas as pd
from dataclasses import replace
from src.futures.sessions import ist, normalise_candles, trading_day


def completed_day(now):
    now=ist(now)
    day=now.normalize()
    if now<day+pd.Timedelta(hours=15,minutes=30) or not trading_day(day):
        day-=pd.Timedelta(days=1)
    while not trading_day(day):
        day-=pd.Timedelta(days=1)
    return day


def closing_volume(quote,config,now):
    try:
        stamp=ist(quote.get('timestamp') or quote.get('last_trade_time'))
        volume=float(quote['volume'])
        end=completed_day(now)
        # Do not mistake today's partial/session-reset volume for a completed day.
        if stamp>ist(now) or stamp.normalize()>end or (stamp.hour,stamp.minute)<(15,25):
            raise ValueError('Not closing-session evidence')
        age=sum(trading_day(d) for d in pd.date_range(stamp.normalize()+pd.Timedelta(days=1),end,freq='D'))
        if age>config.maximum_history_age_sessions or not math.isfinite(volume) or volume<0:
            raise ValueError('Stale or invalid closing volume')
        if volume==0:
            return {'status':'UNKNOWN','volume':volume,'as_of':stamp.isoformat(),
                    'basis':'ZERO_QUOTE_VOLUME_REQUIRES_COMPLETED_HISTORY',
                    'reason':'ZERO_QUOTE_VOLUME_NOT_VERIFIED_BY_COMPLETED_CANDLES',
                    'execution_liquidity':'UNVERIFIED_REQUIRES_DAILY_LIVE_CHECKS'}
        return {'status':'PASS' if volume>=config.minimum_futures_volume else 'FAIL',
                'volume':volume,'as_of':stamp.isoformat(),'basis':'CLOSING_SESSION_FUTURES_VOLUME',
                'execution_liquidity':'UNVERIFIED_REQUIRES_DAILY_LIVE_CHECKS'}
    except (ValueError,TypeError,KeyError):
        return {'status':'UNKNOWN','basis':'COMPLETED_SESSION_VOLUME_UNAVAILABLE',
                'execution_liquidity':'UNVERIFIED_REQUIRES_DAILY_LIVE_CHECKS'}


def historical_volume(frame,config,now):
    try:
        data=normalise_candles(frame,'day',now)
        if data.empty:
            raise ValueError('Completed Futures history missing')
        end=completed_day(now)
        age=sum(trading_day(d) for d in pd.date_range(data.index[-1]+pd.Timedelta(days=1),end,freq='D'))
        if age>config.maximum_history_age_sessions:
            raise ValueError('Completed Futures history stale')
        volume=pd.to_numeric(data.Volume.tail(5),errors='coerce')
        if len(volume)<3 or not volume.map(math.isfinite).all() or (volume<0).any():
            raise ValueError('Insufficient valid volume sessions')
        average=float(volume.mean())
        return {'status':'PASS' if average>=config.minimum_futures_volume else 'FAIL',
                'average_volume':average,'latest_volume':float(volume.iloc[-1]),'sessions':len(volume),
                'as_of':data.index[-1].isoformat(),'basis':'COMPLETED_FUTURES_HISTORY',
                'execution_liquidity':'UNVERIFIED_REQUIRES_DAILY_LIVE_CHECKS'}
    except (ValueError,TypeError,KeyError,IndexError):
        return {'status':'UNKNOWN','basis':'COMPLETED_FUTURES_HISTORY_UNAVAILABLE',
                'execution_liquidity':'UNVERIFIED_REQUIRES_DAILY_LIVE_CHECKS'}


def stock_volume(frame,config,now):
    """Underlying share participation; independent of Futures execution gates."""
    result=historical_volume(frame,replace(config,minimum_futures_volume=config.minimum_stock_volume),now)
    result['basis']='COMPLETED_UNDERLYING_STOCK_HISTORY'
    result['volume_units']='shares'
    result['minimum_average_volume']=config.minimum_stock_volume
    result.pop('execution_liquidity',None)
    return result
