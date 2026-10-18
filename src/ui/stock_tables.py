"""Shared saved stock context and per-view column preferences; no market reads."""
import json
from urllib.parse import urlparse
import pandas as pd
import streamlit as st


def matching_daily(member,daily):
    side='SHORT' if member['category']=='SHORTING_STOCKS' else 'LONG'
    return next((r for bucket in ('long','short','other') for r in (daily or {}).get(bucket,[])
                 if r.get('symbol')==member['symbol'] and r.get('side')==side and r.get('watchlist')==member['category']),{})


def context_columns(row,weekly=None):
    weekly=weekly or {}
    research=row.get('company_research') or {}
    original=research.get('fundamentals') or (weekly.get('fundamentals') or {}).get('snapshot') or {}
    supplemental=(row.get('display_fundamentals') or {}).get('snapshot') or {}
    fundamental={**supplemental,**{k:v for k,v in original.items() if v is not None}}
    supplemental_used=any(original.get(k) is None and supplemental.get(k) is not None for k in ('pe_ratio','sector_pe','roe','roce'))
    checks=row.get('execution_checks') or {}
    evidence=row.get('evidence') or {}
    factors=lambda k:(checks.get(k) or {}).get('factors') or {}
    metrics=weekly.get('metrics') or {}
    research_checks=research.get('checks') or {}
    valuation=(research_checks.get('valuation_quality') or {}).get('factors') or {}
    measured=lambda key,fallback:original[key] if original.get(key) is not None else valuation[fallback] if valuation.get(fallback) is not None else fundamental.get(key)
    missing_reasons=list(row.get('reason_codes',[]))
    score_failure=row.get('score_failure') or {}
    history_failure=score_failure.get('history_failure') or {}
    score_detail=history_failure.get('detail') or score_failure.get('detail')
    intraday_failure=row.get('intraday_score_failure') or {}
    if score_detail:missing_reasons.append('Daily scoring: '+score_detail)
    if intraday_failure.get('detail'):missing_reasons.append('Futures intraday scoring: '+intraday_failure['detail'])
    for name,check in research_checks.items():
        if check.get('status')=='UNKNOWN':
            missing_reasons.append(name+': '+', '.join(check.get('reason_codes',[]) or ['Data unavailable']))
    news=row.get('news')
    origin='Daily scan'
    if news is None:
        saved=weekly.get('news_events') or {}
        news=saved.get('news') or {}
        origin='Weekly rotation' if saved.get('news') is not None else 'Not available'
    titles=[]; articles=[]; seen=set()
    for article in news.get('article_assessments',[])+news.get('headlines',[]):
        if not isinstance(article,dict) or article.get('relevant') is False or article.get('is_relevant') is False:
            continue
        title=article.get('title') or article.get('headline')
        if not title or title in seen:
            continue
        seen.add(title);titles.append(title);articles.append(article)
    first=articles[0] if articles else {}
    link=first.get('url') or first.get('link')
    if not isinstance(link,str) or urlparse(link).scheme not in ('http','https'):
        link=None
    if articles:
        state='Headlines available' if not news.get('fetch_failed') else 'Headlines available; analysis unavailable'
    elif news.get('fetch_failed') or news.get('news_state')=='FETCH_FAILED':
        state='Fetch failed'
    elif news.get('news_state')=='NO_RELEVANT_NEWS' or news.get('requested') or news.get('available'):
        state='No relevant headlines found'
    else:
        state='Not checked / unknown'
    columns={'Sector':row.get('sector') or weekly.get('sector','UNKNOWN'),
        'Entry quality':(row.get('entry_quality') or {}).get('classification','NOT_EVALUATED'),
        'Entry quality assessed':(row.get('entry_quality') or {}).get('assessed_at'),
        'Entry quality mode':(row.get('entry_quality') or {}).get('mode','NOT_EVALUATED'),
        'Entry quality reasons':', '.join((row.get('entry_quality') or {}).get('reason_codes',[])),
        'Entry zone':str((row.get('entry_quality') or {}).get('entry_zone') or 'Unavailable'),
        '15-minute confirmation':((row.get('entry_quality') or {}).get('retracement_15m') or {}).get('status','NOT_EVALUATED'),
        'Entry valid until':row.get('entry_valid_until'),
        'Long confirmation':(row.get('long_confirmation') or {}).get('status','NOT_EVALUATED'),
        'Long confirmation reasons':', '.join((row.get('long_confirmation') or {}).get('reason_codes',[])),
        'Short confirmation':(row.get('short_confirmation') or {}).get('status','NOT_EVALUATED'),
        'Short confirmation reasons':', '.join((row.get('short_confirmation') or {}).get('reason_codes',[])),
        'Daily ranking score':row.get('daily_ranking_score'),
        'OI score':(row.get('oi_ranking') or {}).get('score'),
        'OI ranking status':(row.get('oi_ranking') or {}).get('status','NOT_EVALUATED'),
        'OI regime':(row.get('oi_ranking') or {}).get('regime'),
        'OI change %':(row.get('oi_ranking') or {}).get('oi_change_percent'),
        'OI price change %':(row.get('oi_ranking') or {}).get('price_change_percent'),
        'OI baseline time':(row.get('oi_ranking') or {}).get('baseline_timestamp'),
        'OI quote time':(row.get('oi_ranking') or {}).get('quote_timestamp'),
        'Futures OI':(row.get('oi_ranking') or {}).get('current_oi'),
        'OI ranking reasons':', '.join((row.get('oi_ranking') or {}).get('reason_codes',[])),
        'Score basis':row.get('technical_score_timeframe','DAILY_UNDERLYING_RESEARCH') if row.get('technical_score') is not None else 'UNAVAILABLE',
        'Score failure':score_detail or intraday_failure.get('detail'),
        'News reversal gate':(row.get('news_conflict_gate') or {}).get('status','NOT_ENABLED'),
        'News reversal reasons':', '.join((row.get('news_conflict_gate') or {}).get('reason_codes',[])),
        'Stock volume status':(weekly.get('stock_quality') or {}).get('status','UNKNOWN'),
        'Stock average volume':(weekly.get('stock_quality') or {}).get('average_volume'),
        'Stock volume as of':(weekly.get('stock_quality') or {}).get('as_of'),
        'Futures liquidity context':(weekly.get('futures_quality') or {}).get('status','UNKNOWN'),
        'Sector identity status':(weekly.get('sector_identity') or {}).get('status','UNKNOWN'),
        'Sector identity source':(weekly.get('sector_identity') or {}).get('source','UNKNOWN'),
        'Sector benchmark':(weekly.get('sector_benchmark') or {}).get('index'),
        'Sector benchmark status':(weekly.get('sector_benchmark') or {}).get('status','UNKNOWN'),
        'Sector benchmark source':(weekly.get('sector_benchmark') or {}).get('source','UNKNOWN'),
        'Sector benchmark as of':(weekly.get('sector_benchmark') or {}).get('as_of'),
        'P/E':measured('pe_ratio','stock_pe'),'Sector P/E':measured('sector_pe','sector_pe'),
        'Fundamental status':'AVAILABLE' if fundamental else 'PARTIAL_VALUATION_ONLY' if any(valuation.get(k) is not None for k in ('stock_pe','sector_pe')) else 'UNAVAILABLE',
        'Research status':research.get('status','UNKNOWN'),
        'Stock VWAP':(research_checks.get('vwap_quality') or {}).get('factors',{}).get('vwap'),
        'Futures VWAP status':(checks.get('futures_vwap_quality') or {}).get('status','NOT_EXECUTION_REVIEWED' if not row.get('execution_reviewed') else 'UNKNOWN'),
        'Missing data reasons':'; '.join(dict.fromkeys(missing_reasons)),
        'ROE %':fundamental.get('roe'),'ROCE %':fundamental.get('roce'),
        'Debt/equity':fundamental.get('debt_to_equity'),'Revenue growth':fundamental.get('revenue_growth'),
        'Profit growth':fundamental.get('profit_growth'),'Promoter pledge %':fundamental.get('promoter_pledge'),
        'Fundamental source':fundamental.get('source') or ('Saved valuation check' if valuation else 'UNKNOWN'),'Fundamentals as of':fundamental.get('as_of'),
        'Fundamental snapshot':'Supplemental company data · display only' if supplemental_used else 'Daily scan' if research.get('fundamentals') else 'Weekly rotation' if weekly.get('fundamentals') else 'Saved valuation check' if valuation else 'Not available',
        'Supplemental fundamental source':supplemental.get('source'),
        'Supplemental fundamentals as of':supplemental.get('as_of'),
        'Supplemental fundamentals fetched':(row.get('display_fundamentals') or {}).get('retrieved_at'),
        'Supplemental fundamentals status':(row.get('display_fundamentals') or {}).get('status'),
        'Supplemental data origin':(row.get('display_fundamentals') or {}).get('data_origin','PROVIDER' if supplemental else 'UNAVAILABLE'),
        'Supplemental field sources':json.dumps((row.get('display_fundamentals') or {}).get('field_provenance',{})),
        'News status':state,'Relevant news':' | '.join(titles[:3]) or state,'News source':first.get('source'),
        'News link':link,'News published':first.get('published') or first.get('published_at'),
        'News checked':news.get('checked_at') or news.get('generated_at'), 'News snapshot':origin,
        'Futures VWAP':factors('futures_vwap_quality').get('vwap'),
        'Signal VWAP':evidence.get('vwap'),'VWAP relationship':evidence.get('vwap_relationship','UNKNOWN'),
        'RSI':factors('futures_rsi_quality').get('rsi_14',evidence.get('rsi_14')),
        'ADX':evidence.get('adx_14'),'MACD':evidence.get('macd'),'MACD signal':evidence.get('macd_signal'),
        'MACD histogram':evidence.get('macd_histogram'),'Daily ATR':factors('futures_atr_quality').get('daily_atr_14',evidence.get('daily_atr')),
        'Intraday ATR':factors('futures_atr_quality').get('five_minute_atr_14'),
        'Relative volume':factors('futures_rvol_quality').get('rvol',evidence.get('relative_volume')),
        'Candle pattern':evidence.get('pattern'),'Directional candle fraction':evidence.get('directional_candles_fraction'),
        'Directional volume share':evidence.get('directional_candle_volume_share'),
        'Support':evidence.get('support'),'Resistance':evidence.get('resistance'),
        'Spread %':factors('futures_spread_quality').get('spread_percent'),
        'OI check':(checks.get('futures_oi_quality') or {}).get('status','UNKNOWN'),
        'Event risk':(row.get('event_risk') or {}).get('event_risk_level','UNKNOWN')}
    for k,v in (evidence.get('ema_values') or {}).items():
        columns[k]=v
    for k,v in metrics.items():
        if isinstance(v,(str,int,float,bool)) or v is None:
            columns['Weekly '+k]=v
        elif isinstance(v,dict):
            for name,value in v.items():
                if isinstance(value,(str,int,float,bool)) or value is None:
                    columns['Weekly '+k+' '+name]=value
    for k,v in fundamental.items():
        if isinstance(v,(str,int,float,bool)) or v is None:
            columns['Fundamental '+k]=v
        elif isinstance(v,(list,tuple)):
            columns['Fundamental '+k]=', '.join(str(x) for x in v)
    for direction,assessment in (weekly.get('score_evidence') or {}).items():
        for name in ('confirmed_score','possible_score','coverage_percent','observed_condition_score'):
            columns[direction+' '+name]=assessment.get(name)
    # Preserve every additional measured execution factor as a selectable field.
    for name,check in checks.items():
        columns[name+' status']=check.get('status','UNKNOWN')
        for key,value in (check.get('factors') or {}).items():
            if isinstance(value,(str,int,float,bool)) or value is None:
                columns[name+' '+key]=value
    columns['_news_articles']=[{k:article.get(k) for k in ('title','source','url','published')} for article in articles[:20]]
    return columns


def render_stock_table(rows,view,store):
    frame=pd.DataFrame([{k:v for k,v in row.items() if not k.startswith('_')} for row in rows])
    if view.startswith('legacy-futures-'):
        frame=frame.drop(columns=[c for c in frame if c in ('Watchlist','Weekly score','Membership status','Enabled')
            or c.startswith('Weekly ') or c.startswith('Stock volume ') or c in ('Stock average volume','Futures liquidity context')
            or c.startswith('Sector identity ') or c.startswith('Sector benchmark')],errors='ignore')
    if frame.empty:
        st.info('No stocks in this result.')
        return
    if 'Entry quality mode' in frame and (frame['Entry quality mode']=='ENFORCE').any():
        st.caption('Entry Quality ENFORCE was active for this saved scan. Only ENTER with Decision APPROVED and current entry validity passed the recorded checks. WAIT FOR PULLBACK and SKIP are not entry signals. Scans remain manual.')
    if 'Entry quality mode' in frame and (frame['Entry quality mode']=='REPORT_ONLY').any():
        st.caption('Entry Quality is REPORT_ONLY: ENTER / WAIT FOR PULLBACK / SKIP are proposed assessments, not active entry protection. Existing Decision remains authoritative.')
    from src.futures.entry_validity import entry_status
    now=pd.Timestamp.now(tz='Asia/Kolkata')
    frame['Entry status']=[entry_status(row,now) for row in rows]
    if 'Decision' in frame:
        frame.loc[frame['Entry status'].isin(['EXPIRED — RESCAN','UNVERIFIED — RESCAN']), 'Decision']='SAVED APPROVAL — RESCAN'
    st.caption('LONG / SHORT indicates the direction evaluated. Scores alone are not entry permission. Only APPROVED with a current entry validity can be considered; expired saved results require a manual rescan.')
    available=list(frame.columns)
    preferred=['Symbol','Company','Direction','Stock price','Futures price','Discount %','AI infrastructure connection','Power sector connection','Defence business','Futures availability','Delivery %','Monthly delivery %','Watchlist','Membership status','Enabled','Weekly score','Latest daily score','Latest daily decision',
               'Latest daily scan','Daily score status',
               'Daily score','Daily ranking score','OI score','OI regime','OI change %','OI ranking status','Score basis','Score failure','Decision','Sector','P/E','Sector P/E','Futures VWAP','RSI','Relative volume',
               'Relevant news','News status','News link','News published','News checked']
    preferred+=['News source','News snapshot','Fundamental source','Fundamentals as of']
    preferred+=['Fundamental snapshot','Supplemental fundamental source','Supplemental fundamentals as of','Supplemental fundamentals fetched','Supplemental fundamentals status','Supplemental data origin']
    preferred+=['Fundamental status','Research status','Stock VWAP','Futures VWAP status','Missing data reasons']
    preferred+=['News reversal gate','News reversal reasons']
    preferred+=['Entry quality','Entry quality assessed','Entry quality mode','Entry quality reasons','Entry zone','15-minute confirmation','Entry status','Entry valid until','Long confirmation','Long confirmation reasons','Short confirmation','Short confirmation reasons']
    preferred+=['Stock volume status','Stock average volume','Futures liquidity context']
    preferred+=['Sector identity status','Sector identity source','Sector benchmark','Sector benchmark status',
                'bearish confirmed_score','bearish possible_score','bearish coverage_percent',
                'recovery confirmed_score','recovery possible_score','recovery coverage_percent']
    defaults=[c for c in preferred if c in available]
    saved=store.view_columns(view)
    if saved is not None:saved=['Entry quality' if c=='Entry quality proposal' else c for c in saved]
    initial=[c for c in (saved if saved is not None else defaults) if c in available]
    key='stock-columns-'+view
    if key in st.session_state:
        st.session_state[key]=[('Entry quality' if c=='Entry quality proposal' else c) for c in st.session_state[key] if c in available or c=='Entry quality proposal']
    with st.expander('Choose visible columns'):
        selected=st.multiselect('Visible columns',available,default=initial,key=key)
        if st.button('Save column selection',key=key+'-save'):
            store.save_view_columns(view,selected)
            st.success('Column selection saved for this view.')
    if 'Entry status' not in selected:selected=['Entry status']+selected
    if selected:
        display=frame[selected].copy()
        for column in display:
            if column!='News link' and display[column].isna().all():display[column]='Unavailable'
        st.caption('Unavailable or blank values were not verified in this saved scan. See status and missing-data reasons. Stock VWAP and Futures VWAP use separate instruments.')
        st.dataframe(display,hide_index=True,width='stretch',column_config={
            'News link':st.column_config.LinkColumn('News link',display_text='Read article')})
    else:
        st.info('Select at least one column above.')
    if st.checkbox('Show saved news for one stock',key=key+'-news'):
        choice=st.selectbox('Stock news',range(len(rows)),format_func=lambda i:str(rows[i].get('Symbol','Stock'))+' '+str(rows[i].get('Direction','')),key=key+'-news-stock')
        articles=rows[choice].get('_news_articles',[])
        if articles:
            safe=[]
            for article in articles:
                link=article.get('url')
                if not isinstance(link,str) or urlparse(link).scheme not in ('http','https'):
                    link=None
                safe.append({'Headline':article.get('title'),'Source':article.get('source'),'Published':article.get('published'),'Article':link})
            st.dataframe(pd.DataFrame(safe),hide_index=True,width='stretch',column_config={'Article':st.column_config.LinkColumn('Article',display_text='Read article')})
        else:
            st.info(rows[choice].get('News status','Not checked / unknown'))
    st.caption('Saved scan data only. Blank numeric values are unavailable. Weekly and daily indicators refer to different sessions.')
