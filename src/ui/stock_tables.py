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
    fundamental=research.get('fundamentals') or (weekly.get('fundamentals') or {}).get('snapshot') or {}
    checks=row.get('execution_checks') or {}
    evidence=row.get('evidence') or {}
    factors=lambda k:(checks.get(k) or {}).get('factors') or {}
    metrics=weekly.get('metrics') or {}
    news=row.get('news')
    origin='Daily scan'
    if news is None:
        saved=weekly.get('news_events') or {}
        news=saved.get('news') or {}
        origin='Weekly rotation'
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
    elif news.get('requested') or news.get('available'):
        state='No relevant headlines found'
    else:
        state='Not checked / unknown'
    columns={'Sector':row.get('sector') or weekly.get('sector','UNKNOWN'),
        'P/E':fundamental.get('pe_ratio'),'Sector P/E':fundamental.get('sector_pe'),
        'ROE %':fundamental.get('roe'),'ROCE %':fundamental.get('roce'),
        'Debt/equity':fundamental.get('debt_to_equity'),'Revenue growth':fundamental.get('revenue_growth'),
        'Profit growth':fundamental.get('profit_growth'),'Promoter pledge %':fundamental.get('promoter_pledge'),
        'Fundamental source':fundamental.get('source','UNKNOWN'),'Fundamentals as of':fundamental.get('as_of'),
        'Fundamental snapshot':'Daily scan' if research.get('fundamentals') else 'Weekly rotation',
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
    if frame.empty:
        st.info('No stocks in this result.')
        return
    available=list(frame.columns)
    preferred=['Symbol','Direction','Watchlist','Weekly score','Latest daily score','Latest daily decision',
               'Latest daily scan','Daily score status',
               'Daily score','Decision','Sector','P/E','Sector P/E','Futures VWAP','RSI','Relative volume',
               'Relevant news','News status','News link','News published','News checked']
    preferred+=['News source','News snapshot','Fundamental source','Fundamentals as of']
    defaults=[c for c in preferred if c in available]
    saved=store.view_columns(view)
    initial=[c for c in (saved if saved is not None else defaults) if c in available]
    key='stock-columns-'+view
    if key in st.session_state:
        st.session_state[key]=[c for c in st.session_state[key] if c in available]
    with st.expander('Choose visible columns'):
        selected=st.multiselect('Visible columns',available,default=initial,key=key)
        if st.button('Save column selection',key=key+'-save'):
            store.save_view_columns(view,selected)
            st.success('Column selection saved for this view.')
    if selected:
        st.dataframe(frame[selected],hide_index=True,width='stretch',column_config={
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
