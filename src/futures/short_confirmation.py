"""Strict, prefix-only SHORT confirmation; never manufactures missing market data."""
from math import isfinite
import pandas as pd
from src.futures.sessions import ist,normalise_candles,completed_session_check
from src.quality.futures_execution import clean


def still_valid(result,now,config):
    try:
        stamp=pd.Timestamp(result['checked_at'])
        return stamp.tz is not None and result.get('status')=='PASS' and 0<=(ist(now)-stamp).total_seconds()<=config.short_confirmation_max_age_seconds
    except (KeyError,TypeError,ValueError):return False


def session(raw,now,label):
    frame=clean(normalise_candles(raw,'5minute',now))
    if completed_session_check(frame,now)['status']!='PASS':raise ValueError(label+'_SESSION_MISSING_OR_INCOMPLETE')
    return frame.loc[frame.index.date==now.date()]


def vwap(frame):
    volume=frame.Volume.cumsum()
    if len(frame)<2 or (volume<=0).any():raise ValueError('VWAP_VOLUME_UNAVAILABLE')
    return (((frame.High+frame.Low+frame.Close)/3)*frame.Volume).cumsum()/volume


def evaluate(data,oi_check,now,config):
    now=ist(now)
    result={'status':'UNKNOWN','checked_at':now.isoformat(),'reason_codes':[],
        'basis':'COMPLETED_CURRENT_SESSION_BARS_AND_FRESH_EXACT_CONTRACT_QUOTE','evidence':{}}
    if not config.short_confirmation_gate_enabled:
        return {**result,'status':'DISABLED','reason_codes':['SHORT_CONFIRMATION_GATE_DISABLED']}
    try:
        stock=session(data.get('underlying_intraday'),now,'STOCK')
        futures=session(data.get('intraday'),now,'FUTURES')
        nifty=session(data.get('short_nifty_intraday'),now,'NIFTY')
        sector=session(data.get('short_sector_intraday'),now,'SECTOR')
        quote=data.get('quote') or {}
        age=(now-ist(quote['timestamp'])).total_seconds()
        price=float(quote['last_price'])
        if not isfinite(price) or price<=0 or not 0<=age<=config.short_confirmation_max_age_seconds:
            raise ValueError('SHORT_ENTRY_QUOTE_NOT_FRESH')
        spot=data.get('spot_quote') or {}
        spot_age=(now-ist(spot['timestamp'])).total_seconds()
        spot_price=float(spot['last_price'])
        if not isfinite(spot_price) or spot_price<=0 or not 0<=spot_age<=config.short_confirmation_max_age_seconds:
            raise ValueError('SHORT_STOCK_QUOTE_NOT_FRESH')
        if data.get('instrument_token') is None or quote.get('instrument_token') is None or int(data['instrument_token'])!=int(quote['instrument_token']):
            raise ValueError('SHORT_EXACT_CONTRACT_UNVERIFIED')
        if min(len(stock),len(futures))<config.short_confirmation_support_bars+2:
            raise ValueError('SHORT_BREAK_RETEST_HISTORY_INSUFFICIENT')
        sv,fv=vwap(stock),vwap(futures)
        evidence=result['evidence']
        evidence.update(stock_vwap=float(sv.iloc[-1]),futures_vwap=float(fv.iloc[-1]),
            stock_vwap_change=float(sv.iloc[-1]-sv.iloc[-2]),futures_vwap_change=float(fv.iloc[-1]-fv.iloc[-2]),
            stock_candle_at=stock.index[-1].isoformat(),futures_candle_at=futures.index[-1].isoformat(),quote_at=ist(quote['timestamp']).isoformat())
        conflicts=[];pending=[]
        if float(stock.Close.iloc[-1])>=sv.iloc[-1] or spot_price>=sv.iloc[-1] or float(futures.Close.iloc[-1])>=fv.iloc[-1] or price>=fv.iloc[-1]:conflicts.append('SHORT_PRICE_NOT_BELOW_BOTH_VWAPS')
        if sv.iloc[-1]>sv.iloc[-2] or fv.iloc[-1]>fv.iloc[-2]:conflicts.append('SHORT_VWAP_RISING')
        recent=stock.tail(3)
        share=float(recent.loc[recent.Close>recent.Open,'Volume'].sum()/recent.Volume.sum()) if recent.Volume.sum()>0 else 0.
        higher=bool((recent.High.diff().dropna()>0).all() and (recent.Low.diff().dropna()>0).all())
        evidence.update(buying_volume_share=share,higher_highs_and_lows=higher)
        if higher and share>=config.short_confirmation_buying_volume_share:conflicts.append('SHORT_BULLISH_PRICE_VOLUME_CONFLICT')
        # All three return windows must cover exactly the same completed session.
        if not stock.index.equals(nifty.index) or not stock.index.equals(sector.index):raise ValueError('SHORT_RELATIVE_STRENGTH_WINDOWS_UNALIGNED')
        ret=lambda f:float((f.Close.iloc[-1]/f.Open.iloc[0]-1)*100)
        sr,nr,br=ret(stock),ret(nifty),ret(sector)
        evidence.update(stock_session_return_percent=sr,nifty_session_return_percent=nr,sector_session_return_percent=br)
        if sr>nr:conflicts.append('SHORT_STOCK_STRONGER_THAN_NIFTY_INTRADAY')
        if sr>br:conflicts.append('SHORT_STOCK_STRONGER_THAN_SECTOR_INTRADAY')
        if getattr(oi_check,'status','UNKNOWN')=='UNKNOWN':raise ValueError('SHORT_OI_CONFIRMATION_UNAVAILABLE')
        pc,oc=[float(oi_check.factors[k]) for k in ('price_change_percent','oi_change_percent')]
        if not all(isfinite(v) for v in (pc,oc)):raise ValueError('SHORT_OI_CONFIRMATION_INVALID')
        evidence.update(price_change_percent=pc,oi_change_percent=oc)
        if pc>-config.oi_minimum_price_change_percent or oc<config.oi_minimum_change_percent:
            conflicts.append('SHORT_PRICE_FALL_WITH_RISING_OI_REQUIRED')
        found=None
        n=config.short_confirmation_support_bars
        for b in range(max(n,len(futures)-8),len(futures)-1):
            level=float(futures.Low.iloc[b-n:b].min())
            if not float(futures.Close.iloc[b])<level<=float(futures.Close.iloc[b-1]):continue
            for r in range(max(b+1,len(futures)-config.short_confirmation_retest_max_age_bars),len(futures)):
                bar=futures.iloc[r]
                # A traded revisit, bearish rejection and subsequent trigger crossing.
                if bar.High>=level and bar.Close<level and bar.Close<bar.Open and bar.Close<futures.Close.iloc[r-1] and futures.Close.iloc[-1]<=bar.Close:
                    found={'support':level,'breakdown_at':futures.index[b].isoformat(),'retest_at':futures.index[r].isoformat(),'trigger':float(bar.Low),'invalidation':float(bar.High)}
        evidence['breakdown_retest']=found
        if found is None:pending.append('SHORT_COMPLETED_BREAKDOWN_AND_FAILED_RETEST_REQUIRED')
        elif price>found['trigger']:pending.append('SHORT_RETEST_TRIGGER_NOT_CROSSED')
        result.update(status='BLOCKED' if conflicts else 'WAIT' if pending else 'PASS',
            reason_codes=conflicts+pending if conflicts or pending else ['SHORT_VWAP_BREAK_RETEST_VOLUME_OI_CONFIRMED'])
    except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError,OverflowError) as exc:
        result['reason_codes']=[str(exc)]
    return result
