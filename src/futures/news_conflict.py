"""Point-in-time material news plus observed reversal; never a scoring bonus."""
from copy import deepcopy
import math
import re
from urllib.parse import urlparse
import pandas as pd
from src.futures.sessions import ist, normalise_candles
from src.futures_workspace.validation import require_valid_ohlcv

TRUSTED=('nseindia.com','bseindia.com','hindustanpetroleum.com','hpcl.co.in',
         'ten.com','globenewswire.com','reuters.com','moneycontrol.com',
         'economictimes.indiatimes.com','livemint.com','cnbctv18.com','business-standard.com')


def stamp(value):
    try:
        date=pd.Timestamp(value)
        return ist(date) if date.tzinfo is not None and not pd.isna(date) else None
    except (TypeError,ValueError):return None


def news_evidence(news,symbol,now,config):
    checked=stamp(news.get('checked_at'))
    result={'status':'UNAVAILABLE','events':[],'discarded':[]}
    if news.get('fetch_failed') or news.get('collection_state')=='FETCH_FAILED':
        result['reason']='NEWS_COLLECTION_FAILED';return result
    if checked is None or not 0<=(now-checked).total_seconds()<=config.news_conflict_snapshot_age_seconds:
        result['reason']='NEWS_SNAPSHOT_STALE_MISSING_OR_FUTURE';return result
    if news.get('news_state')=='NO_RELEVANT_NEWS':
        result.update(status='NO_RELEVANT_NEWS',reason='VERIFIED_COLLECTION_FOUND_NO_RELEVANT_ARTICLES');return result
    if news.get('news_state')!='ANALYZED':
        result['reason']='NEWS_COLLECTION_OR_ANALYSIS_UNAVAILABLE';return result
    if not news.get('article_assessments'):
        result['reason']='ANALYZED_WITHOUT_ARTICLE_EVIDENCE';return result
    seen_urls=set();seen_titles=set();unknown=False;neutral=False
    for article in news.get('article_assessments',[]):
        published=stamp(article.get('published'))
        available=stamp(article.get('available_at') or article.get('first_seen_at')
                        or (article.get('published') if article.get('materiality_verified') is not True else None))
        analyzed=stamp(article.get('assessment_at') if article.get('materiality_verified') is True
                       else article.get('available_at') or article.get('published'))
        reason=None
        if any(x is None for x in (published,available,analyzed)):
            reason='TIMESTAMP_UNVERIFIED';unknown=True
        elif max(published,available,analyzed)>now:reason='NOT_AVAILABLE_AT_DECISION'
        elif (now-published).total_seconds()>config.news_conflict_max_age_hours*3600:reason='STALE_NEWS'
        url=str(article.get('canonical_url') or article.get('url') or '')
        parsed=urlparse(url);host=(parsed.hostname or '').lower()
        identity=parsed._replace(query='',fragment='').geturl().rstrip('/')
        title=re.sub(r'\W+',' ',str(article.get('title','')).casefold()).strip()
        if reason is None and (identity in seen_urls or title in seen_titles):reason='DUPLICATE_NEWS'
        if reason is None:
            seen_urls.add(identity);seen_titles.add(title)
            trusted=parsed.scheme=='https' and any(host==domain or host.endswith('.'+domain) for domain in TRUSTED)
            if not trusted or article.get('source_verified') is not True:
                reason='SOURCE_UNVERIFIED';unknown=True
            elif article.get('company_specific') is not True or symbol not in article.get('company_symbols',[]):
                reason='COMPANY_IMPACT_UNVERIFIED';unknown=True
            elif article.get('materiality_verified') is not True:
                reason='MATERIALITY_UNVERIFIED';unknown=True
            elif not article.get('verification_basis'):
                reason='VERIFICATION_BASIS_MISSING';unknown=True
            else:
                sentiment=article.get('sentiment')
                if sentiment not in ('NEUTRAL','BULLISH','POSITIVE','BEARISH','NEGATIVE'):
                    reason='SEMANTICS_UNVERIFIED';unknown=True
                if sentiment=='NEUTRAL':neutral=True
                elif article.get('materiality')=='HIGH' and sentiment in ('BULLISH','POSITIVE','BEARISH','NEGATIVE'):
                    result['events'].append({k:article.get(k) for k in ('title','url','published','available_at','assessment_at','sentiment','materiality','event_id','verification_basis')})
        if reason:result['discarded'].append({'title':article.get('title'),'reason':reason})
    result['status']='PARTIAL' if unknown and result['events'] else 'UNAVAILABLE' if unknown else 'MATERIAL_EVENTS' if result['events'] else 'VERIFIED_NEUTRAL' if neutral else 'NO_FRESH_MATERIAL_NEWS'
    return result


def price_confirmation(bars,publication,now,direction,config):
    try:
        data=normalise_candles(bars,'5minute',now)
        require_valid_ohlcv(data)
        data=data.loc[data.index.date==now.date()]
        n=config.news_conflict_confirmation_bars
        if len(data)<n+3:raise ValueError('INSUFFICIENT_INTRADAY_BARS')
        if (now-(data.index[-1]+pd.Timedelta(minutes=5))).total_seconds()>config.news_conflict_price_age_seconds:
            raise ValueError('INTRADAY_BARS_STALE')
        post=data.loc[data.index>=publication]
        if len(post)<n:raise ValueError('POST_NEWS_COMPLETED_BARS_MISSING')
        tail=post.tail(n);prior=data.loc[data.index<tail.index[0]]
        if len(prior)<3:raise ValueError('PRE_CONFIRMATION_BASELINE_MISSING')
        # No gaps or still-forming bars can stand in for the confirmation window.
        if len(tail.index)>1 and not ((tail.index[1:]-tail.index[:-1])==pd.Timedelta(minutes=5)).all():
            raise ValueError('CONFIRMATION_BARS_GAPPED')
        typical=(data.High+data.Low+data.Close)/3
        vwap=(typical*data.Volume).cumsum()/data.Volume.cumsum()
        baseline=float(prior.Volume.tail(config.news_conflict_volume_lookback).mean())
        if baseline<=0:raise ValueError('VOLUME_BASELINE_UNAVAILABLE')
        rvol=float(tail.Volume.mean()/baseline)
        change=float((tail.Close.iloc[-1]/prior.Close.iloc[-1]-1)*100)
        prices=data.Close.iloc[-len(tail):];averages=vwap.iloc[-len(tail):]
        bullish=direction=='BULLISH'
        vwap_ok=bool((prices>averages).all() if bullish else (prices<averages).all())
        structure=bool(tail.Close.iloc[-1]>prior.High.tail(config.news_conflict_volume_lookback).max() if bullish else tail.Close.iloc[-1]<prior.Low.tail(config.news_conflict_volume_lookback).min())
        move=change if bullish else -change
        confirmed=vwap_ok and structure and rvol>=config.news_conflict_minimum_rvol and move>=config.news_conflict_minimum_move_percent
        if not all(math.isfinite(x) for x in (rvol,change,float(vwap.iloc[-1]))):raise ValueError('NON_FINITE_CONFIRMATION')
        return {'status':'CONFIRMED' if confirmed else 'NOT_CONFIRMED','direction':direction,
                'last_completed_bar':tail.index[-1].isoformat(),'change_percent':change,'relative_volume':rvol,
                'session_vwap':float(vwap.iloc[-1]),'vwap_confirmed':vwap_ok,'structure_confirmed':structure}
    except (ValueError,TypeError,KeyError,IndexError,AttributeError) as exc:
        return {'status':'UNAVAILABLE','reason':type(exc).__name__+':'+str(exc)}


def evaluate(symbol,side,news,bars,now,config):
    now=ist(now)
    evidence=news_evidence(news or {},symbol,now,config)
    opposite='BULLISH' if side=='SHORT' else 'BEARISH'
    result={'enabled':bool(config.news_conflict_gate_enabled),'excluded':False,
            'decision_at':now.isoformat(),'news':evidence,'confirmations':[],'reason_codes':[]}
    for event in evidence['events']:
        if event['sentiment'] not in (('BULLISH','POSITIVE') if side=='SHORT' else ('BEARISH','NEGATIVE')):continue
        confirmation=price_confirmation(bars,stamp(event['published']),now,opposite,config)
        result['confirmations'].append({'event':event,'market':confirmation})
        if confirmation['status']=='CONFIRMED':
            result['excluded']=bool(config.news_conflict_gate_enabled)
            result['reason_codes'].append('MATERIAL_POSITIVE_NEWS_BULLISH_REVERSAL_EXCLUDES_SHORT' if side=='SHORT' else 'MATERIAL_NEGATIVE_NEWS_BEARISH_REVERSAL_EXCLUDES_LONG')
    result['status']='EXCLUDED' if result['excluded'] else 'UNAVAILABLE' if evidence['status'] in ('UNAVAILABLE','PARTIAL') or any(c['market']['status']=='UNAVAILABLE' for c in result['confirmations']) else 'CLEAR'
    if result['status']=='UNAVAILABLE':result['reason_codes'].append('NEWS_REVERSAL_PROTECTION_UNVERIFIED')
    return result


def research_policy(research,gate):
    """Replace only the legacy headline-only news gate when explicitly enabled."""
    if not gate['enabled']:return research
    research=deepcopy(research)
    research['legacy_news_check']=deepcopy(research.get('checks',{}).get('recent_news_quality'))
    for field in ('failed_checks','unavailable_checks'):
        research[field]=[key for key in research[field] if key!='recent_news_quality']
    research['review_flags']=[r for r in research.get('review_flags',[]) if r['check']!='recent_news_quality']
    research['policy_review_required']=bool(research['review_flags'])
    research['eligible']=not research['failed_checks'] and not research['unavailable_checks']
    research['status']='UNKNOWN' if research['unavailable_checks'] else 'FAIL' if research['failed_checks'] else 'PASS'
    research['news_policy']='LEGACY_HEADLINE_ONLY_CHECK_REPLACED_BY_VERIFIED_MATERIAL_EVENT_AND_PRICE_CONFIRMATION'
    return research


def shortlist(items,limit):
    return [item for item in items if not (item.get('news_conflict_gate') or {}).get('excluded')][:limit]
