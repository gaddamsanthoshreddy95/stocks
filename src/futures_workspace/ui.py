"""Additive Streamlit workspace, persisted results and isolated background jobs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Lock
import json
import time
import streamlit as st
import pandas as pd
from src.futures_workspace.config import WorkspaceConfig
from src.futures_workspace.store import WorkspaceStore, LISTS
from src.futures_workspace.service import FuturesWorkspace
from src.application.platform import TradingPlatform

SECTIONS=('Overview','Daily Trading','Shorting Stocks','Recovering Stocks','Weekly Rotation','Rotation History','Analysis & Performance','Settings')

class JobController:
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='futures-workspace')
        self.future=None
        self.progress=(0,0,'')
        self.label=''
        self.next_schedule=0.
        self.started_at=0.
        self.updated_at=0.
        self.lock=Lock()

    def submit(self,label,operation):
        with self.lock:
            if self.future and not self.future.done():
                return False
            self.label=label; self.progress=(0,0,'')
            self.started_at=self.updated_at=time.monotonic()
            self.next_schedule=float('inf')
            def run():
                try:
                    return operation()
                finally:
                    with self.lock:
                        self.next_schedule=time.monotonic()+300
            self.future=self.pool.submit(run)
        return True

    def update(self,current,total,symbol):
        with self.lock:
            self.progress=(current,total,symbol)
            self.updated_at=time.monotonic()

@st.cache_resource
def controller(path):
    return JobController()

def latest(store,kind):
    return store.latest_result(kind)

def details_and_export(label,payload,key,filename):
    # Expander contents are still rendered by Streamlit. Use an explicit control
    # so large nested reports never reach the browser on initial navigation.
    if st.checkbox(label,key=key):
        if isinstance(payload,dict) and payload.get('evaluations'):
            symbol=st.selectbox('Stock evidence',sorted(payload['evaluations']),key=key+'-symbol')
            st.json(payload['evaluations'][symbol])
        else:
            st.caption('Full evidence is available in the JSON export.')
        st.download_button('Download full report JSON',json.dumps(payload,indent=2),file_name=filename,key=key+'-export')

def result_rows(items):
    rows=[]
    for r in items:
        contract=r.get('contract') or {}; evidence=r.get('evidence') or {}; plan=r.get('plan') or {}; quote=r.get('futures_quote') or {}
        checks=r.get('execution_checks') or {}
        factors=lambda name:(checks.get(name) or {}).get('factors',{})
        freshness=(r.get('scan_gates') or {}).get('final_futures_quote_freshness',{}).get('status','UNKNOWN')
        rows.append({'Symbol':r['symbol'],'Watchlist':r.get('watchlist'),'Direction':r.get('side'),
            'Contract':contract.get('tradingsymbol'),'Expiry':contract.get('expiry'),'Price':quote.get('last_price',evidence.get('price')),
            'Strategy':r.get('setup_type'),'Daily score':r.get('original_daily_score'),'Weekly score':r.get('weekly_discovery_score'),
            'Sector':r.get('sector'),'Sector strength':(evidence.get('sector_relative') or {}).get('excess_percentage_points'),
            'Market Bias':r.get('market_bias',{}).get('classification'),'Counter trend':r.get('counter_trend'),
            'VWAP':factors('futures_vwap_quality').get('vwap',evidence.get('vwap')),
            'VWAP position':checks.get('futures_vwap_quality',{}).get('status','UNKNOWN'),
            'Volume':factors('futures_rvol_quality').get('rvol',evidence.get('rvol',evidence.get('relative_volume'))),
            'OI':', '.join(checks.get('futures_oi_quality',{}).get('reason_codes',['UNKNOWN'])),'Entry':plan.get('entry'),
            'Target':plan.get('target'),'Stop':plan.get('stop_loss'),'Net R:R':plan.get('net_risk_reward'),
            'Quote time':quote.get('timestamp'),'Data freshness':freshness,'Decision':r.get('workspace_decision'),'Original decision':r.get('final_decision'),
            'Confidence':r.get('weekly_confidence'),'Liquidity':checks.get('futures_spread_quality',{}).get('status','UNKNOWN'),
            'Reasons':', '.join(r.get('reason_codes',[]))})
    return rows

def render(platform,database):
    store=WorkspaceStore(database.path)
    config=replace(WorkspaceConfig.from_env(),**store.settings())
    st.title('Futures Trading')
    st.caption('Weekly universe discovery • two independent watchlists • one selected-stock daily scan')
    if not config.enabled:
        st.info('Enable FUTURES_WORKSPACE_ENABLED=true in .env, then restart the application.')
        return
    jobs=controller(str(store.path))
    def new_workspace():
        jobs.update(0,0,'Initializing market-data services')
        return FuturesWorkspace(TradingPlatform(settings=platform.settings),WorkspaceStore(store.path))
    def submit(label,operation):
        jobs.submit(label,operation)
    @st.fragment(run_every='2s')
    def job_status():
        saved_job=store.locked_job()
        local_busy=bool(jobs.future and not jobs.future.done())
        if saved_job and not local_busy:
            st.warning('A saved job lock is blocking scans. This does not prove a worker is still running.')
            st.write({'Job ID':saved_job['id'],'Job':saved_job['kind'],'Started':saved_job['started_at']})
            st.caption('Use recovery only after stopping any other Streamlit or scheduler worker. Recovery preserves watchlists and history.')
            if st.button('Recover stopped job',key='ft-recover-job'):
                store.recover_job(saved_job['id'],worker_stopped=True)
                jobs.future=None
                jobs.next_schedule=time.monotonic()+300
                st.rerun(scope='app')
        if jobs.future:
            future=jobs.future
            if not jobs.future.done():
                current,total,symbol=jobs.progress
                st.progress(current/total if total else 0,text=f'{jobs.label}: {current}/{total} {symbol}' if total else f'{jobs.label}: {symbol or "Starting worker"}')
                st.caption(f'Elapsed: {int(time.monotonic()-jobs.started_at)} seconds')
                if time.monotonic()-jobs.updated_at>120:
                    st.warning('No progress update for over two minutes. The worker may be waiting on a data source. Check the Streamlit terminal before restarting; another scan cannot run while this worker is active.')
            else:
                # Fragment refreshes do not refresh the scan buttons outside it.
                # Refresh the whole page once per completed job and browser session.
                completed_key='ft-completed-job-'+str(store.path)
                if st.session_state.get(completed_key)!=id(future):
                    st.session_state[completed_key]=id(future)
                    from streamlit.runtime.scriptrunner import get_script_run_ctx
                    context=get_script_run_ctx()
                    if context is not None and context.fragment_ids_this_run:
                        st.rerun(scope='app')
                try:
                    output=jobs.future.result()
                    st.caption(f'Last job: {jobs.label} — {output.get("status","completed")}')
                    if output.get('status')=='AUTO_RETRY_PAUSED':
                        st.warning('Automatic full-scan retries are paused. '+str(output.get('reason') or 'The weekly cycle was already attempted.')+' Use Weekly Rotation to retry manually.')
                except Exception as exc:
                    st.error(f'{jobs.label}: {exc}')
        if st.button('Refresh workspace view',key='ft-refresh'):
            st.rerun(scope='app')
    job_status()
    busy=bool(jobs.future and not jobs.future.done()) or store.locked_job() is not None
    section=st.radio('Futures Trading section',SECTIONS,horizontal=True,key='ft-section')
    st.caption('Manual mode: scans run only when you click their buttons.')
    if section=='Settings':
        daily=None; rotation=None; members=[]
    else:
        members=store.members()
        daily=latest(store,'DAILY_TRADING') if section in ('Overview','Daily Trading') else None
        rotation=latest(store,'WEEKLY_ROTATION') if section in ('Overview','Weekly Rotation') else None
    if section=='Overview':
        cols=st.columns(3)
        cols[0].metric('Eligible stock Futures',rotation.get('eligible_universe_size',0) if rotation else 0)
        for col,category in zip(cols[1:],LISTS):
            col.metric(category,sum(m['category']==category and m['enabled'] and m['status']=='ACTIVE' for m in members))
        st.write({'Market Bias':(daily or {}).get('market_bias',{}).get('classification','UNKNOWN'),
            'Last daily scan':(daily or {}).get('generated_at','Never'),'Last weekly rotation':(rotation or {}).get('as_of','Never'),
            'System status':(rotation or {}).get('status','INITIALIZING'),'Latest LONG':len((daily or {}).get('long',[])),
            'Latest SHORT':len((daily or {}).get('short',[]))})
        st.info('Manual trading only. Stop new workspace entries at 15:00 IST; close manually by 15:10 IST. Membership is not a signal.')
    elif section=='Daily Trading':
        if st.button('SCAN SELECTED STOCKS',type='primary',disabled=busy,key='ft-scan'):
            submit('Combined Daily Trading scan',lambda:new_workspace().scan_daily())
            st.rerun()
        if daily:
            st.caption(f'{daily.get("selected_count",0)} unique selected stocks • completed {daily.get("generated_at")}')
            st.caption('Persisted scan results are historical snapshots; scan again to evaluate current quotes and entry conditions.')
            for key,title in (('long','Futures LONG Opportunities'),('short','Futures SHORT Opportunities'),('other','NO TRADE / REJECTED / UNKNOWN')):
                st.subheader(title)
                st.dataframe(pd.DataFrame(result_rows(daily.get(key,[]))),hide_index=True,width='stretch')
            details_and_export('Prepare full scan export including Reports A–E',daily,'ft-daily-details','futures_daily_scan.json')
        else:
            st.info('Initialize both watchlists with Weekly Rotation, then run one combined daily scan.')
    elif section in ('Shorting Stocks','Recovering Stocks'):
        category=LISTS[0] if section=='Shorting Stocks' else LISTS[1]
        selected=[m for m in members if m['category']==category]
        rows=[{'Symbol':m['symbol'],'Status':m['status'],'Enabled':bool(m['enabled']),'Pinned':bool(m['pinned']),
            'Added':m['added_at'],'Evaluated':m['evaluated_at'],'Weekly score':m['evidence'].get('discovery',{}).get('ranking_score'),
            'Confidence':m['evidence'].get('confidence'),'Recovery':m['evidence'].get('recovery_status'),
            '52-week low':m['evidence'].get('metrics',{}).get('low_52w'),
            'Distance from 52-week low %':m['evidence'].get('metrics',{}).get('distance_52w_low_percent'),
            '52-week high drawdown %':m['evidence'].get('metrics',{}).get('drawdown_52w_percent'),
            'Weekly volume evidence':m['evidence'].get('futures_quality',{}).get('status','UNKNOWN'),
            'Daily execution liquidity':'Requires fresh live checks',
            'Reason':', '.join(m['evidence'].get('reason_codes',[]))} for m in selected]
        st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
        with st.form('ft-management-'+category):
            symbol=st.text_input('NSE stock symbol')
            action=st.selectbox('Action',('add','remove','enable','disable','pin','unpin'))
            applied=st.form_submit_button('Apply watchlist change',disabled=busy)
        if applied:
            try:
                new_workspace().manage(symbol,action,category)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if selected and st.checkbox('Show individual selection evidence',key='ft-member-details-'+category):
            symbol=st.selectbox('Stock', [m['symbol'] for m in selected],key='ft-member-symbol-'+category)
            st.json(next(m for m in selected if m['symbol']==symbol))
    elif section=='Weekly Rotation':
        st.caption('Post-market discovery uses completed sessions. Live spread and depth are checked during Daily Trading. Fundamentals and news are fetched for shortlisted candidates and existing members.')
        attempts=store.jobs('WEEKLY_ROTATION',limit=1,include_results=False)
        if attempts and attempts[0]['status'] in ('INCOMPLETE','FAILED'):
            last=attempts[0]
            reason=last.get('error') or (rotation or {}).get('reason','Data unavailable')
            st.warning(f'Saved rotation result: {last["status"]}. {reason}. No automatic retry will run. Use Run Full Universe Scan when ready.')
        if st.button('Run Full Universe Scan',disabled=busy,type='primary'):
            submit('Full universe weekly rotation',lambda:new_workspace().rotate(progress=jobs.update))
            st.rerun()
        if st.button('Recheck Selected Watchlists',disabled=busy):
            submit('Selected watchlist recheck',lambda:new_workspace().rotate(selected_only=True,progress=jobs.update))
            st.rerun()
        if rotation:
            st.caption('Last saved rotation result')
            st.write({k:rotation.get(k) for k in ('status','eligible_universe_size','evaluated_count','technical_evaluated_count','context_evaluated_count','elapsed_seconds','as_of','reason') if k in rotation})
            st.write({'Technical classifications':rotation.get('technical_classification_counts',{}),
                      'Histories fetched':rotation.get('history_fetched_count','UNKNOWN'),
                      'Histories usable':rotation.get('usable_history_count','UNKNOWN'),
                      'Fundamentals available':rotation.get('fundamental_available_count','UNKNOWN'),
                      'Optional context unavailable':rotation.get('optional_context_unavailable_count','UNKNOWN'),
                      'Final classifications':rotation.get('classification_counts',{}),
                      'History source':rotation.get('history_source','UNKNOWN'),
                      'Benchmark data':rotation.get('benchmark_quality',{})})
            failures=rotation.get('data_failure_reasons',{})
            if failures:
                st.subheader('Why data could not be used')
                st.dataframe(pd.DataFrame([{'Reason':reason,'Stocks':count} for reason,count in sorted(failures.items(),key=lambda v:-v[1])]),hide_index=True)
            st.dataframe(pd.DataFrame(rotation.get('changes',[])),hide_index=True,width='stretch')
            details_and_export('Show individual stock evidence and export',rotation,'ft-rotation-details','futures_weekly_rotation.json')
        st.caption('Routine changes apply automatically. Unknown data preserves the last version or marks membership REVIEW_REQUIRED.')
    elif section=='Rotation History':
        count=store.version_count()
        page=st.number_input('History page',1,max(1,(count+49)//50),1)
        versions=store.version_headers((page-1)*50)
        st.dataframe(pd.DataFrame([{'Version':v['id'],'Created':v['created_at'],'Kind':v['kind'],'Previous version':v['previous_version'],'Members':v['member_count']} for v in versions]),hide_index=True)
        if versions:
            choice=st.selectbox('Historical version',[v['id'] for v in versions])
            version=store.version(choice)
            st.write({'Version':version['id'],'Created':version['created_at'],'Members':len(version['snapshot'])})
            if st.button('Restore this watchlist version',disabled=busy):
                store.rollback(choice)
                st.rerun()
            details_and_export('Prepare version export',version,'ft-version-details',f'futures_watchlists_{choice}.json')
            st.caption('Restored members require a recheck. Rollback never changes broker execution history.')
    elif section=='Analysis & Performance':
        st.json(FuturesWorkspace(platform,store,config=config).performance())
        payload=st.file_uploader('Archived point-in-time research data (JSON)',type=['json'],key='ft-replay-data')
        if st.button('Run offline research replay',disabled=busy or payload is None):
            try:
                replay_data=json.loads(payload.getvalue())
                submit('Offline point-in-time research replay',lambda:new_workspace().replay(replay_data))
                st.rerun()
            except (ValueError,TypeError) as exc:
                st.error(str(exc))
        replay_result=latest(store,'RESEARCH_REPLAY')
        if replay_result:
            st.write({k:replay_result.get(k) for k in ('status','selection_count','futures_metrics','cost_status') if k in replay_result})
            details_and_export('Prepare full replay export',replay_result,'ft-replay-details','futures_research_replay.json')
        st.info('Win rates and expectancy remain UNKNOWN until linked manual fills and point-in-time out-of-sample evidence exist. Existing technical backtests remain in Reports A–E.')
    else:
        st.caption('Experimental discovery settings are independent of daily strategy weights. All jobs are manual; saved schedule values are inactive.')
        with st.form('ft-settings'):
            minimum=st.number_input('Minimum weekly score',0.,100.,float(config.minimum_score))
            count=st.number_input('Maximum stocks per list',1,200,int(config.maximum_per_list))
            workers=st.number_input('Adaptive worker resource limit',2,128,int(config.maximum_workers))
            healthy_batches=st.number_input('Healthy batches before doubling workers',1,20,int(config.healthy_parallel_batches))
            confirmation=st.number_input('Confirmation sessions',2,20,int(config.confirmation_sessions))
            hysteresis=st.number_input('Hysteresis points',0.,100.,float(config.hysteresis))
            decline=st.number_input('Recovery six-month decline %',0.,100.,float(config.recovery_decline_percent))
            drawdown=st.number_input('Recovery high drawdown %',0.,100.,float(config.recovery_drawdown_percent))
            bias_directional=st.number_input('Market Bias directional threshold',1.,99.,float(config.bias_directional_threshold))
            bias_strong=st.number_input('Market Bias strong threshold',2.,100.,float(config.bias_strong_threshold))
            history_source=st.selectbox('Weekly adjusted equity history',('yahoo_adjusted','kite'),index=0 if config.weekly_history_source=='yahoo_adjusted' else 1)
            adjusted=st.checkbox('Require verified corporate-action-adjusted history',value=config.require_adjusted_history)
            if st.form_submit_button('Save workspace settings',disabled=busy):
                payload={**store.settings(),'minimum_score':minimum,'maximum_per_list':count,'confirmation_sessions':confirmation,
                    'maximum_workers':workers,'healthy_parallel_batches':healthy_batches,
                    'hysteresis':hysteresis,'recovery_decline_percent':decline,'recovery_drawdown_percent':drawdown,
                    'bias_directional_threshold':bias_directional,'bias_strong_threshold':bias_strong,
                    'require_adjusted_history':adjusted,'weekly_history_source':history_source}
                try:
                    replace(config,**payload)
                    store.save_settings(payload)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        st.json(asdict(config))
        st.caption('Use Weekly Rotation and Daily Trading buttons to start jobs. The schedule command is disabled.')
