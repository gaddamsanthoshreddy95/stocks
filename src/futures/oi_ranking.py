"""Daily ranking overlay from verified exact-contract OI; no admission changes."""
from math import isfinite

from src.quality.futures_execution import FuturesExecutionConfig


def apply_oi_ranking(item, data, oi_check, config):
    base=item.get('technical_score')
    result={'status':'UNAVAILABLE','score':None,'weight':config.oi_ranking_weight,
        'basis':'EXACT_CONTRACT_QUOTE_VS_PREVIOUS_COMPLETED_SESSION',
        'reason_codes':['VERIFIED_FUTURES_OI_REQUIRED']}
    # Repeated execution refreshes must always blend with the original price score.
    item['daily_ranking_score']=base
    item['oi_ranking']=result
    if config.oi_ranking_weight==0:
        result.update(status='DISABLED',reason_codes=['OI_RANKING_DISABLED'])
        return
    try:
        if base is None or not isfinite(float(base)) or not 0<=float(base)<=100:
            raise ValueError('PRICE_TECHNICAL_SCORE_UNAVAILABLE')
        if item.get('technical_score_timeframe')!='COMPLETED_FUTURES_5_MINUTE':
            raise ValueError('VERIFIED_INTRADAY_PRICE_SCORE_REQUIRED')
        if oi_check.status=='UNKNOWN':raise ValueError('VERIFIED_FUTURES_OI_REQUIRED')
        quote=data.get('quote') or {}
        token=data.get('instrument_token')
        if token is None or quote.get('instrument_token') is None or int(token)<=0 or int(token)!=int(quote['instrument_token']):
            raise ValueError('EXACT_CONTRACT_OI_IDENTITY_UNVERIFIED')
        factors=oi_check.factors
        pc,oc,oi,prior,close,age=[float(factors[k]) for k in (
            'price_change_percent','oi_change_percent','current_oi','previous_session_oi','previous_session_close','quote_age_seconds')]
        if not all(isfinite(v) for v in (pc,oc,oi,prior,close,age)) or min(oi,prior,close)<=0 or not 0<=age<=FuturesExecutionConfig.from_env().maximum_quote_age_seconds:
            raise ValueError('OI_VALUES_OR_FRESHNESS_INVALID')
        if not factors.get('baseline_timestamp'):raise ValueError('OI_BASELINE_TIMESTAMP_REQUIRED')
        sign=1 if item['side']=='LONG' else -1
        if abs(pc)<config.oi_minimum_price_change_percent or abs(oc)<config.oi_minimum_change_percent:
            regime='UNCHANGED_OR_SMALL_CHANGE';score=50.
        else:
            regime='LONG_BUILDUP' if pc>0 and oc>0 else 'SHORT_BUILDUP' if pc<0 and oc>0 else 'SHORT_COVERING' if pc>0 else 'LONG_UNWINDING'
            strength=min(abs(oc)/config.oi_full_strength_change_percent,1)
            score=(75+25*strength if oc>0 else 25+25*strength) if pc*sign>0 else 0.
        result.update(status='AVAILABLE',score=round(score,2),regime=regime,
            price_change_percent=pc,oi_change_percent=oc,current_oi=oi,previous_session_oi=prior,
            baseline_timestamp=factors['baseline_timestamp'],quote_timestamp=quote.get('timestamp'),
            reason_codes=['OI_PRICE_DIRECTIONAL_RANKING_'+regime])
        item['daily_ranking_score']=round((1-config.oi_ranking_weight)*float(base)+config.oi_ranking_weight*score,2)
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        result['reason_codes']=[str(exc)]
