"""Manual, fixed-universe AI infrastructure research using the existing scanner."""
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from src.application.platform import TradingPlatform
from src.futures.config import FuturesScanConfig
from src.futures.runtime import ScanRuntime
from src.futures.scanner import FuturesOpportunityScanner
from src.quality.futures_selection import active_contracts
from src.ui.futures_history import fetch_missing_fundamentals, missing_data_rows


AI_STOCKS={
    'CGPOWER':'Transformers, Motors, Power Infrastructure',
    'ABB':'Electrification, Automation',
    'POWERINDIA':'Grid Equipment, Transformers',
    'CUMMINSIND':'Data Centre Backup Generators',
    'BLUESTARCO':'Data Centre Cooling, HVAC',
    'VOLTAS':'Cooling, HVAC Systems',
    'AMBER':'Cooling Components, Electronics',
    'POLYCAB':'Power Cables, Electrical Infrastructure',
    'KEI':'Power Cables',
    'SIEMENS':'Electrification, Industrial Automation',
    'TATACOMM':'Data Centre Connectivity, Cloud',
    'KAYNES':'Electronics Manufacturing',
}


class ThemeProvider:
    def __init__(self,provider):
        self.original=provider
        self.master=None

    def __getattr__(self,name):return getattr(self.original,name)

    @property
    def recovery_provider(self):return self.original

    def get_nfo_instruments(self):
        if self.master is None:self.master=self.original.get_nfo_instruments()
        return self.master


class ThemePlatform:
    restricted_symbols=tuple(AI_STOCKS)
    def __init__(self,platform,stocks=None):
        self.original=platform
        self.provider=ThemeProvider(platform.provider)
        self.restricted_symbols=tuple(AI_STOCKS if stocks is None else stocks)

    def __getattr__(self,name):return getattr(self.original,name)

    def _universe_symbols(self):return list(self.restricted_symbols)


class AiStockHistory:
    """Separate archives in the existing UI database; no Futures archive writes."""
    table='ai_stock_scan_runs'
    def __init__(self,database):
        self.database=database
        with closing(database._connect()) as c:
            c.execute(f'''CREATE TABLE IF NOT EXISTS {self.table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT UNIQUE NOT NULL,
                generated_at TEXT NOT NULL,scan_mode TEXT NOT NULL,report_json TEXT NOT NULL)''')
            c.commit()

    def save(self,report,run_id):
        with closing(self.database._connect()) as c:
            c.execute(f'INSERT INTO {self.table}(run_id,generated_at,scan_mode,report_json) VALUES(?,?,?,?)',
                (run_id,report['generated_at'],report['scan_mode'],json.dumps(report,default=str)))
            c.commit()
            return c.execute(f'SELECT id FROM {self.table} WHERE run_id=?',(run_id,)).fetchone()[0]

    def list(self):
        with closing(self.database._connect()) as c:
            return [dict(row) for row in c.execute(f'SELECT id,generated_at,scan_mode FROM {self.table} ORDER BY id DESC LIMIT 100')]

    def get(self,scan_id):
        with closing(self.database._connect()) as c:
            row=c.execute(f'SELECT report_json FROM {self.table} WHERE id=?',(int(scan_id),)).fetchone()
            return json.loads(row[0]) if row else None


def scan_ai_stock(settings,database,limit,mode,run_id,*,stocks=None,title='AI-Stock',cache_name='ai_stock_prepared',history_class=AiStockHistory,saved_id='_ai_saved_scan_id',companies=None,all_universe=False,postprocess=None):
    stocks=AI_STOCKS if stocks is None else stocks
    platform=TradingPlatform(settings=settings)
    from src.data_provider.kite_data_provider import KiteDataProvider
    if isinstance(getattr(platform,'provider',None),KiteDataProvider):
        platform.provider.provider.reload_saved_credentials()
    if all_universe:
        stocks={symbol:"Current stock-Futures universe" for symbol in platform._universe_symbols()}
    scoped=ThemePlatform(platform,stocks)
    config=FuturesScanConfig.from_env()
    entry=min((config.entry_cutoff_hour,config.entry_cutoff_minute),(15,0))
    exit_at=min((config.intraday_exit_hour,config.intraday_exit_minute),(15,10))
    config=replace(config,review_per_direction=max(config.review_per_direction,len(stocks)),
        entry_cutoff_hour=entry[0],entry_cutoff_minute=entry[1],
        intraday_exit_hour=exit_at[0],intraday_exit_minute=exit_at[1])
    runtime=ScanRuntime.from_env()
    # Dedicated universe/cache; the execution ledger stays global across features.
    runtime=replace(runtime,cache_directory=str(Path(runtime.cache_directory).parent/cache_name))
    scan_limit=max(1,len(stocks)*2) if all_universe else limit
    report=FuturesOpportunityScanner(scoped,config=config).scan(scan_limit,mode=mode,runtime=runtime)
    report=platform._serialize(report)
    report=fetch_missing_fundamentals(report,database=database)
    day=pd.Timestamp(report['generated_at']).date()
    verified=not report.get('source_failures',{}).get('universe_verification') and scoped.provider.master is not None
    report['feature']=title
    report['universe']=[{'symbol':symbol,'company':(companies or {}).get(symbol),'connection':connection,
        'futures_status':('ACTIVE_CONTRACT_VERIFIED' if active_contracts(symbol,scoped.provider.master,day) else 'NO_ACTIVE_CONTRACT') if verified else 'UNVERIFIED'}
        for symbol,connection in stocks.items()]
    # Defensive isolation: no returned row may escape the user-supplied universe.
    for key in ('reviewed','report_a','report_b','report_c'):
        report[key]=[row for row in report.get(key,[]) if row.get('symbol') in stocks]
    if postprocess is not None:
        report=postprocess(report)
    report[saved_id]=history_class(database).save(report,run_id)
    return report


def render(platform,database,start_job,is_running,*,stocks=None,title='AI-Stock',feature='ai_stock',history_class=AiStockHistory,scan_function=None,connection_label='AI infrastructure connection',companies=None,all_universe=False):
    stocks=AI_STOCKS if stocks is None else stocks
    scan_function=scan_function or scan_ai_stock
    view_prefix=feature.replace('_','-')
    st.header(title)
    st.caption(f'Your {"full stock-Futures universe" if all_universe else str(len(stocks))+"-stock watchlist"} · existing fundamental, technical, news and OI analysis · manual scans only')
    if stocks:
        st.dataframe([{'Stock':symbol,**({'Company':companies[symbol]} if companies and symbol in companies else {}),connection_label:connection,'Futures':'Verify active contract during scan'}
            for symbol,connection in stocks.items()],hide_index=True,width='stretch')
    mode=st.selectbox(title+' scan mode',['AFTER_MARKET_RESEARCH','LIVE_SCAN','DAILY_PREP','FULL_RESEARCH'],index=1 if all_universe else 0,key=view_prefix+'-mode')
    st.caption(f'After market: AFTER_MARKET_RESEARCH. During market: LIVE_SCAN uses prepared history. DAILY_PREP / FULL_RESEARCH prepare the selected universe.' if all_universe else f'After market: AFTER_MARKET_RESEARCH. During market: LIVE_SCAN uses prepared history. DAILY_PREP / FULL_RESEARCH prepare only these {len(stocks)} stocks.')
    limit=st.number_input('Candidates per direction',min_value=1,max_value=100 if all_universe else len(stocks),value=5 if all_universe else min(5,len(stocks)),key=view_prefix+'-limit')
    busy=is_running(feature)
    if st.button('Scan '+title,type='primary',disabled=busy):
        run_id=str(uuid4())
        start_job(feature,lambda:scan_function(platform.settings,database,int(limit),mode,run_id))
        st.rerun()
    if busy:
        st.info(title+' scan is running. Navigation does not start another scan.')
        if st.button('Refresh '+title+' status'):st.rerun()
    if st.session_state.get(feature+'_job_error'):st.error(st.session_state[feature+'_job_error'])
    archive=history_class(database)
    history=archive.list()
    report=st.session_state.get(feature+'_report')
    if report is None and history:
        report=archive.get(history[0]['id'])
        st.info(f'Showing a saved {title} scan. No scan started automatically.')
    if report is None:
        st.info(f'Click Scan {title} to analyse this list. Futures availability is verified from the instrument master, not assumed from the theme list.')
        return
    st.caption(f"Saved scan: {report.get('generated_at')} · {report.get('scan_mode')} · scores are not success probabilities")
    scored=sum(item.get('technical_score') is not None for item in report.get('reviewed',[]))
    st.caption(f"{scored} of {len(report.get('reviewed',[]))} directional evaluations have a technical score.")
    if all_universe:
        st.json(report.get('discount_summary',{}))
    if not scored:st.warning('No verified technical scores in this snapshot. Open Missing data for the recorded failures; no prices or scores have been filled in.')
    st.info('Research rows are not buy/sell recommendations. Only APPROVED rows passed the recorded checks. No automated orders; new entries stop by 15:00 IST and manual exit is by 15:10 IST.')
    from src.ui.stock_tables import context_columns,render_stock_table
    from src.futures_workspace.ui import result_rows
    from src.futures_workspace.store import WorkspaceStore
    preferences=WorkspaceStore(database.path)
    def table(snapshot,key,view):
        candidates=snapshot.get(key,[])
        availability={i['symbol']:i['futures_status'] for i in snapshot.get('universe',[])}
        rows=[]
        for row,item in zip(result_rows(candidates),candidates):
            display={**item,'evidence':(item.get('futures_setup') or {}).get('evidence') or item.get('evidence',{})}
            original=(item.get('company_research') or {}).get('fundamentals') or {}
            facts={**((item.get('display_fundamentals') or {}).get('snapshot') or {}),
                **{key:value for key,value in original.items() if value is not None}}
            rows.append({**row,**context_columns(display),**({'Stock price':(item.get('futures_discount') or {}).get('stock_price'),'Futures price':(item.get('futures_discount') or {}).get('futures_price'),'Discount %':(item.get('futures_discount') or {}).get('discount_percent')} if all_universe else {}),**({'Company':companies[item['symbol']]} if companies and item['symbol'] in companies else {}),connection_label:stocks.get(item['symbol']),
                'Futures availability':availability.get(item['symbol'],'UNVERIFIED'),
                'Delivery %':facts.get('delivery_percent'),'Monthly delivery %':facts.get('monthly_delivery_percent')})
        render_stock_table(rows,view,preferences)
    tabs=st.tabs(['All reviewed','Bullish research','Bearish research','Approved setups','Missing data','History'])
    for tab,key in zip(tabs[:4],('reviewed','report_a','report_b','report_c')):
        with tab:table(report,key,view_prefix+'-'+key)
    with tabs[4]:
        if all_universe:st.dataframe(report.get('discount_diagnostics',[]),hide_index=True,width='stretch')
        st.dataframe(missing_data_rows(report),hide_index=True,width='stretch')
        st.json({'futures_availability':report.get('universe',[]),'source_failures':report.get('source_failures',{}),'scoring_failures':report.get('failures',[])})
    with tabs[5]:
        if history:
            selected=st.selectbox('Saved '+title+' scan',[h['id'] for h in history],
                format_func=lambda value:next(f"#{h['id']} · {h['generated_at']} · {h['scan_mode']}" for h in history if h['id']==value))
            saved=archive.get(selected)
            st.warning('Historical snapshot; not current execution approval.')
            table(saved,'reviewed',view_prefix+'-history')
            st.download_button('Download '+title+' history JSON',json.dumps(saved,default=str),file_name=f'{feature}_{selected}.json',mime='application/json')
    if report.get('reviewed'):
        with st.expander('Inspect all saved analysis for one stock'):
            choices=report['reviewed']
            selected=st.selectbox('Stock and evaluated direction',range(len(choices)),format_func=lambda i:f"{choices[i]['symbol']} · {choices[i]['side']}",key=view_prefix+'-inspect')
            st.json(choices[selected])
    st.download_button('Download current '+title+' analysis JSON',json.dumps(report,default=str),file_name=feature+'_current.json',mime='application/json')
