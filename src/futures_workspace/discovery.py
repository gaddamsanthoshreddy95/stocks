"""Completed-session discovery, independent of daily strategy scores."""
from dataclasses import asdict, is_dataclass
from math import isfinite
import numpy as np
import pandas as pd
from src.futures.sessions import ist, normalise_candles, trading_day
from src.futures.scoring import prepare, relative_returns
from src.news.today import INDEX_FUTURES
from src.futures_workspace.store import LISTS
from src.futures_workspace.validation import ohlcv_diagnostics


def universe(instruments,now):
    """All eligible names from the complete master, nearest valid stock contract."""
    result={}
    for raw in instruments:
        symbol=str(raw.get('name','')).strip().upper()
        try:
            expiry=pd.Timestamp(raw['expiry']).date()
            token=int(raw['instrument_token']); lot=int(raw['lot_size'])
        except (ValueError,TypeError,KeyError):
            continue
        if (raw.get('instrument_type')!='FUT' or raw.get('segment')!='NFO-FUT'
                or raw.get('exchange')!='NFO' or not symbol or symbol in INDEX_FUTURES
                or expiry<ist(now).date() or (expiry==ist(now).date() and (ist(now).hour,ist(now).minute)>=(15,30))
                or token<=0 or lot<=0 or not raw.get('tradingsymbol')
                or raw.get('tradable') is False):
            continue
        contract={**raw,'expiry':expiry.isoformat(),'instrument_token':token,'lot_size':lot,'security_id':'NSE:'+symbol}
        if symbol not in result or expiry<pd.Timestamp(result[symbol]['expiry']).date():
            result[symbol]=contract
    return result


def unknown(reason,details=None):
    result={'classification':'UNKNOWN_DATA','recovery_status':'UNKNOWN','reason_codes':[reason],
            'technical_score':None,'confidence':0,'data_completeness':0}
    if details:
        result['history_quality']=details
    return result


def technical(frame,config,now,benchmark=None,sector=None):
    if frame is None or frame.empty:
        return unknown('HISTORY_MISSING')
    basis=frame.attrs.get('price_adjustment','UNKNOWN')
    if config.require_adjusted_history and basis not in ('ADJUSTED','CORPORATE_ACTION_VALIDATED'):
        return unknown('CORPORATE_ACTION_ADJUSTMENT_UNVERIFIED')
    try:
        data=normalise_candles(frame,'day',now)
        if len(data)<253:
            return unknown('INSUFFICIENT_253_SESSION_HISTORY',{'fetched_rows':len(frame),'completed_rows':len(data),'minimum_rows':253,
                'excluded_rows':data.attrs.get('session_normalization',{}).get('excluded_rows'),
                'latest_session':data.index[-1].isoformat() if not data.empty else None})
        expected=ist(now).normalize()
        if ist(now)<expected+pd.Timedelta(hours=15,minutes=30) or not trading_day(expected):
            expected-=pd.Timedelta(days=1)
        while not trading_day(expected):
            expected-=pd.Timedelta(days=1)
        missing=sum(trading_day(day) for day in pd.date_range(data.index[-1]+pd.Timedelta(days=1),expected,freq='D'))
        if missing>config.maximum_history_age_sessions:
            return unknown('HISTORY_STALE')
        # Validate the complete required 253-session indicator window, rather
        # than demanding identical start dates across older provider caches.
        window_start=data.index[-253]
        session_grid=pd.DatetimeIndex([day for day in pd.date_range(window_start,data.index[-1],freq='D') if trading_day(day)])
        # A covering broad-market history provides observed exchange sessions,
        # including weekday closures absent from the configured holiday list.
        if benchmark is not None:
            try:
                calendar=normalise_candles(benchmark,'day',now)
                closes=pd.to_numeric(calendar.Close,errors='coerce')
                if (not calendar.empty and calendar.index[0]<=window_start
                        and calendar.index[-1]>=data.index[-1]
                        and np.isfinite(closes.to_numpy()).all() and (closes>0).all()):
                    session_grid=calendar.index[(calendar.index>=window_start) & (calendar.index<=data.index[-1])]
            except (ValueError,KeyError,TypeError):
                pass
        gaps=session_grid.difference(data.index)
        if not gaps.empty:
            return unknown('HISTORY_MISSING_SESSIONS',{'missing_session_count':len(gaps),'sample_missing_sessions':[day.isoformat() for day in gaps[:10]],
                'fetched_rows':len(frame),'completed_rows':len(data),'first_session':data.index[0].isoformat(),'latest_session':data.index[-1].isoformat()})
        validation=ohlcv_diagnostics(data)
        if validation['violations']:
            return unknown('HISTORY_INVALID',validation)
        p=prepare(data)
        c=p.Close; last=p.iloc[-1]
        ret=lambda n:float((c.iloc[-1]/c.iloc[-n-1]-1)*100)
        metrics={f'return_{n}d_percent':ret(n) for n in (5,20,21,63,126)}
        metrics.update({'drawdown_52w_percent':float((c.iloc[-1]/p.High.iloc[-252:].max()-1)*100),
            'low_52w':float(p.Low.iloc[-252:].min()),
            'distance_52w_low_percent':float((c.iloc[-1]/p.Low.iloc[-252:].min()-1)*100),
            'distance_6m_low_percent':float((c.iloc[-1]/p.Low.iloc[-126:].min()-1)*100),
            'sma20':float(c.tail(20).mean()),'sma50':float(c.tail(50).mean()),'sma200':float(c.tail(200).mean()),
            'rsi':float(last.RSI),'macd':float(last.MACD),'macd_signal':float(last.MACD_SIGNAL),
            'atr':float(last.ATR),'trend_slope_percent':float(np.polyfit(np.arange(20),c.tail(20).to_numpy(),1)[0]/c.iloc[-1]*100),
            'support':float(p.Low.iloc[-21:-1].min()),'resistance':float(p.High.iloc[-21:-1].max()),
            'relative_volume':float(last.RVOL),'volatility_20d_percent':float(c.pct_change().tail(20).std()*100),
            'volume_trend_percent':float((p.Volume.tail(5).mean()/p.Volume.iloc[-25:-5].mean()-1)*100) if p.Volume.iloc[-25:-5].mean()>0 else None,
            'price':float(c.iloc[-1]),'history_as_of':p.index[-1].isoformat(),'price_adjustment':basis})
        for name,other in (('nifty',benchmark),('sector',sector)):
            try:
                rel=relative_returns(p,normalise_candles(other,'day',now)) if other is not None else None
            except (ValueError,KeyError,TypeError):
                rel=None
            metrics['relative_strength_'+name]=rel
        recent,prior=p.iloc[-5:],p.iloc[-10:-5]
        higher=recent.Low.min()>prior.Low.min() and recent.High.max()>prior.High.max()
        lower=recent.Low.min()<prior.Low.min() and recent.High.max()<prior.High.max()
        metrics.update({'higher_high_higher_low':bool(higher),'lower_high_lower_low':bool(lower),
            'breakout':bool(c.iloc[-1]>metrics['resistance']),'breakdown':bool(c.iloc[-1]<metrics['support'])})
        n=config.confirmation_sessions
        sma=c.rolling(20).mean()
        bearish_confirmation=bool((c.iloc[-n:]<sma.iloc[-n:]).all() and (p.MACD_HISTOGRAM.iloc[-n:]<0).all())
        recovery_confirmation=bool((c.iloc[-n:]>sma.iloc[-n:]).all() and (p.MACD_HISTOGRAM.iloc[-n:]>0).all() and higher and ret(5)>0)
        beaten=ret(126)<=-config.recovery_decline_percent or metrics['drawdown_52w_percent']<=-config.recovery_drawdown_percent
        rs=metrics['relative_strength_nifty']; sr=metrics['relative_strength_sector']
        weak=(rs is not None and rs['excess_percentage_points']<0)
        strong=(rs is not None and rs['excess_percentage_points']>0)
        short_checks=[ret(20)<0,ret(5)<0,lower,c.iloc[-1]<metrics['sma20'],c.iloc[-1]<metrics['sma50'],last.MACD_HISTOGRAM<0,metrics['rsi']<45,metrics['breakdown'],weak,sr is not None and sr['excess_percentage_points']<0]
        recovery_checks=[beaten,ret(5)>0,higher,c.iloc[-1]>metrics['sma20'],last.MACD_HISTOGRAM>0,metrics['rsi']>50,recovery_confirmation,metrics['breakout'],strong,sr is not None and sr['excess_percentage_points']>0]
        bearish=10*sum(bool(x) for x in short_checks)
        recovery=10*sum(bool(x) for x in recovery_checks)
        short_ok=bearish_confirmation and bearish>=config.minimum_score
        recovery_ok=beaten and recovery_confirmation and recovery>=config.minimum_score
        classification='TRANSITION' if short_ok and recovery_ok else LISTS[0] if short_ok else LISTS[1] if recovery_ok else 'NONE'
        recovery_state='RECOVERY_CONFIRMED' if recovery_ok else 'RECOVERY_INVALIDATED' if beaten and bearish_confirmation else 'RECOVERY_WATCH' if beaten else 'NOT_RECOVERY'
        score=bearish if classification==LISTS[0] else recovery if classification==LISTS[1] else max(bearish,recovery)
        metrics.update({'bearish_confirmation':bearish_confirmation,'recovery_confirmation':recovery_confirmation,
            'confirmation_sessions':n,'recovery_start_date':p.index[-n].isoformat() if recovery_confirmation else None})
        if any(isinstance(v,float) and not isfinite(v) for v in metrics.values()):
            return unknown('INDICATORS_UNAVAILABLE')
        completeness=70+15*int(rs is not None)+15*int(sr is not None)
        return {'classification':classification,'recovery_status':recovery_state,'technical_score':score,
            'bearish_score':bearish,'recovery_score':recovery,'metrics':metrics,'confidence':completeness,
            'data_completeness':completeness,'reason_codes':[classification,recovery_state,'MULTI_SESSION_CONFIRMED' if short_ok or recovery_ok else 'NOT_CONFIRMED']}
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        return unknown('HISTORY_INVALID:'+type(exc).__name__)


def fundamental_context(snapshot,sector,now):
    """Interpret dated quarterly growth; avoid industrial debt rules for lenders."""
    raw=asdict(snapshot) if is_dataclass(snapshot) else dict(snapshot or {})
    financial=any(word in str(sector).lower() for word in ('bank','financial','insurance','nbfc'))
    evidence=raw.get('evidence',{}); source=raw.get('source','UNKNOWN')
    observations={}; periods={}; publications={}
    for scalar,quarterly in (('revenue_growth','quarterly_revenue_growth_pct'),('profit_growth','quarterly_profit_growth_pct')):
        field=quarterly if raw.get(quarterly) is not None else scalar
        values=raw.get(field)
        values=list(values) if isinstance(values,(tuple,list)) else [values]
        provenance=evidence.get(field,{})
        period=provenance.get('period')
        try:
            age=(ist(now)-ist(period)).total_seconds()/86400
            fresh=0<=age<=180
        except (ValueError,TypeError):
            fresh=False
        valid=values and all(isinstance(v,(float,int)) and isfinite(v) for v in values)
        if source!='UNKNOWN' and fresh and valid:
            observations[scalar]=values
            periods[scalar]=period
            publications[scalar]=provenance.get('publication_date')
    available='profit_growth' in observations and (financial or 'revenue_growth' in observations)
    score=None
    if available:
        values=observations['profit_growth'] if financial else observations['profit_growth']+observations['revenue_growth']
        score=sum(100 if value>0 else 0 for value in values)/len(values)
    return {'status':'AVAILABLE' if available else 'UNKNOWN','source':source,'reporting_period':periods,
        'snapshot_as_of':raw.get('as_of'),'retrieved_at':ist(now).isoformat(),
        'completeness':len(observations),'interpretation':'FINANCIAL_PROFITABILITY_ONLY_CAPITAL_ASSET_QUALITY_UNKNOWN' if financial else 'REVENUE_PROFIT_GROWTH_CONTEXT',
        'bullish_score':score,'bearish_score':100-score if score is not None else None,'snapshot':raw,
        'publication_date':publications,'publication_date_status':'AVAILABLE' if publications and all(publications.values()) else 'UNKNOWN'}


def discovery_score(item,fundamentals,news,config):
    direction='bearish_score' if item['classification']==LISTS[0] else 'bullish_score'
    components={'technical':item.get('technical_score'),'fundamental':fundamentals.get(direction),'news':news.get(direction)}
    weights={'technical':config.technical_weight,'fundamental':config.fundamental_weight,'news':config.news_weight}
    coverage=sum(weights[k] for k,v in components.items() if v is not None)
    experiment=sum(weights[k]*v for k,v in components.items() if v is not None)/coverage if coverage else None
    return {'components':components,'coverage_percent':coverage*100,'experimental_score':experiment,
        'ranking_score':experiment if config.context_ranking_validated else item.get('technical_score'),
        'context_ranking_mode':'VALIDATED_CONFIG_OVERRIDE' if config.context_ranking_validated else 'REPORT_ONLY',
        'validated_out_of_sample':False,'weights':weights}
