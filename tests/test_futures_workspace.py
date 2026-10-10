"""Offline contract tests for discovery/rotation and selected-only trading."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sqlite3
import numpy as np
import pandas as pd
import pytest
from src.application.platform import TradingPlatform
from src.futures_workspace.config import WorkspaceConfig
from src.futures_workspace.store import WorkspaceStore, LISTS
from src.futures_workspace.discovery import universe,technical,fundamental_context,discovery_score
from src.futures_workspace.service import FuturesWorkspace
from src.futures_workspace.bias import MarketBias
from src.futures_workspace.scheduler import WorkspaceScheduler
from src.futures.config import FuturesScanConfig

NOW=pd.Timestamp('2026-10-10 10:00',tz='Asia/Kolkata')
CFG=WorkspaceConfig(enabled=True)

def contract(symbol='BEAR',**updates):
    return {'name':symbol,'tradingsymbol':symbol+'26OCTFUT','exchange':'NFO','segment':'NFO-FUT',
            'instrument_type':'FUT','expiry':'2026-10-29','lot_size':100,'instrument_token':42,**updates}

def frame(prices):
    c=np.array(prices,dtype=float)
    dates=pd.bdate_range(end='2026-10-09',periods=len(c),tz='Asia/Kolkata')
    f=pd.DataFrame({'Open':c*.998,'High':c*1.003,'Low':c*.997,'Close':c,'Volume':np.full(len(c),100000.)},index=dates)
    f.attrs['price_adjustment']='ADJUSTED'
    return f

def evaluation(category=LISTS[0],score=80):
    return {'classification':category,'technical_score':score,'confidence':85,'data_completeness':85,
        'recovery_status':'RECOVERY_CONFIRMED' if category==LISTS[1] else 'NOT_RECOVERY',
        'reason_codes':[category],'metrics':{},'discovery':{'ranking_score':score}}

class Adapter:
    def __init__(self):
        self.master=[contract('BEAR'),contract('RECOVER'),contract('UNSELECTED')]
        self.counts={}
        self.frames={s:frame(np.linspace(200,100,280)) for s in ('BEAR','RECOVER','UNSELECTED','NIFTY 50')}
    def begin(self,now): self.counts['begin']=self.counts.get('begin',0)+1
    def end(self): self.counts['end']=self.counts.get('end',0)+1
    def instruments(self):
        self.counts['instruments']=self.counts.get('instruments',0)+1
        return self.master
    def quotes(self,contracts):
        return {'NFO:'+c['tradingsymbol']:{'last_price':100,'volume':2000,'timestamp':'2026-10-09 15:29:00+05:30','depth':{'buy':[{'price':99.99}],'sell':[{'price':100.01}]}} for c in contracts.values()}
    def history(self,symbol): return self.frames[symbol]
    def sector(self,symbol): return 'IT'
    def sector_history(self,symbol): return None
    def fundamentals(self,symbol,now): return {'status':'UNKNOWN','bullish_score':None,'bearish_score':None}
    def news(self,symbol,now): return {'status':'UNKNOWN','bullish_score':None,'bearish_score':None}
    def market_bias(self,now): return MarketBias.evaluate({})

@pytest.fixture
def workspace(tmp_path):
    store=WorkspaceStore(tmp_path/'ui.db')
    platform=SimpleNamespace(_serialize=TradingPlatform._serialize,provider=object(),settings=SimpleNamespace(market_data_source='cache'))
    return FuturesWorkspace(platform,store,config=CFG,adapter=Adapter())

def rotate(w,values=None,**kwargs):
    values=values or {s:evaluation(LISTS[0] if s=='BEAR' else LISTS[1] if s=='RECOVER' else 'NONE') for s in ('BEAR','RECOVER','UNSELECTED')}
    def classify(f,*args):
        symbol=next(s for s,v in w.adapter.frames.items() if v is f and s!='NIFTY 50')
        return dict(values.get(symbol,evaluation('NONE')))
    with patch('src.futures_workspace.service.technical',side_effect=classify):
        return w.rotate(NOW,**kwargs)

@pytest.mark.parametrize('bad',[
    {'name':'NIFTY'},{'expiry':'2026-10-01'},{'lot_size':0},{'instrument_token':0},
    {'instrument_type':'CE'},{'exchange':'BSE'},{'segment':'NFO-OPT'},{'tradable':False},
    {'tradingsymbol':''},{'expiry':'bad'}])
def test_universe_rejects_ineligible_contracts(bad):
    assert universe([contract(**bad)],NOW)=={}

def test_complete_master_not_a_permanent_stock_list():
    master=[contract('NAME'+str(i),instrument_token=i+1) for i in range(250)]
    master+=[contract('NAME0',expiry='2026-11-29',tradingsymbol='NAME026NOVFUT')]
    found=universe(master,NOW)
    assert len(found)==250 and found['NAME0']['expiry']=='2026-10-29'
    assert found['NAME0']['security_id']=='NSE:NAME0'


def test_actual_bearish_and_recovery_multisession_discovery():
    bear=technical(frame(np.linspace(220,100,280)),CFG,NOW)
    assert bear['classification']==LISTS[0]
    # Credible stabilization followed by a multi-session rise, still substantially drawn down.
    recovery=technical(frame(np.r_[np.linspace(250,100,245),np.linspace(100,145,35)]),CFG,NOW)
    assert recovery['classification']==LISTS[1]
    assert recovery['recovery_status']=='RECOVERY_CONFIRMED'
    assert recovery['metrics']['drawdown_52w_percent']<-20
    assert set(('sma20','sma50','sma200','rsi','macd','atr','relative_strength_nifty'))<=recovery['metrics'].keys()
    single=technical(frame(np.r_[np.linspace(250,100,279),105]),CFG,NOW)
    assert single['classification']!=LISTS[1]
    assert single['recovery_status']!='RECOVERY_CONFIRMED'
    assert bear['recovery_status']=='RECOVERY_INVALIDATED'

@pytest.mark.parametrize('mode',['missing','short','stale','unadjusted','duplicate','invalid'])
def test_bad_history_honest_unknown(mode):
    f=frame(np.linspace(200,100,280))
    if mode=='missing': f=None
    if mode=='short': f=f.tail(50)
    if mode=='stale': f=f.iloc[:-20]
    if mode=='unadjusted': f.attrs={}
    if mode=='duplicate': f.index=pd.DatetimeIndex([f.index[0]]*len(f))
    if mode=='invalid': f.iloc[-1,f.columns.get_loc('Close')]=float('nan')
    assert technical(f,CFG,NOW)['classification']=='UNKNOWN_DATA'


def test_first_initialization_independent_watchlists_and_audit(workspace):
    assert workspace.store.members()==[]
    report=rotate(workspace)
    assert report['eligible_universe_size']==3
    assert {m['category'] for m in workspace.store.members(active=True)}==set(LISTS)
    assert len(workspace.store.versions())==1
    assert len(workspace.store.evidence('BEAR'))==3
    assert all(m['evidence']['fundamentals']['status']=='UNKNOWN' for m in workspace.store.members())
    assert all(m['evidence']['discovery']['components']['fundamental'] is None for m in workspace.store.members())


def test_add_remove_transfer_pin_and_hysteresis(workspace):
    rotate(workspace)
    workspace.store.manage('RECOVER','pin')
    # Confirmed transfer of BEAR and pinned recovery invalidation.
    report=rotate(workspace,{'BEAR':evaluation(LISTS[1],90),'RECOVER':evaluation('NONE',20),'UNSELECTED':evaluation(LISTS[0],85)})
    members={m['symbol']:m for m in workspace.store.members()}
    assert members['BEAR']['category']==LISTS[1]
    assert members['RECOVER']['category']==LISTS[1] and members['RECOVER']['status']=='REVIEW_REQUIRED'
    assert members['UNSELECTED']['category']==LISTS[0]
    assert any(c['action']=='TRANSFERRED' for c in report['changes'])
    rotate(workspace,{'BEAR':evaluation('NONE',20),'RECOVER':evaluation('NONE',20),'UNSELECTED':evaluation(LISTS[0],85)})
    assert 'BEAR' not in {m['symbol'] for m in workspace.store.members()}
    # Borderline classification loss is retained but cannot provide an active daily signal.
    rotate(workspace,{'UNSELECTED':evaluation('NONE',55)})
    assert next(m for m in workspace.store.members() if m['symbol']=='UNSELECTED')['status']=='REVIEW_REQUIRED'


def test_missing_and_conflicting_data_never_silently_remove(workspace):
    rotate(workspace)
    old=workspace.store.versions()[0]['id']
    report=rotate(workspace,{s:{'classification':'UNKNOWN_DATA','technical_score':None,'confidence':0,'reason_codes':['MISSING']} for s in ('BEAR','RECOVER','UNSELECTED')})
    assert report['status']=='INCOMPLETE'
    assert workspace.store.versions()[0]['id']==old
    assert len(workspace.store.members())==2
    report=rotate(workspace,{'BEAR':evaluation('TRANSITION'),'RECOVER':evaluation(LISTS[1]),'UNSELECTED':evaluation('NONE')})
    assert next(m for m in workspace.store.members() if m['symbol']=='BEAR')['status']=='REVIEW_REQUIRED'


def test_weekly_key_idempotent_and_concurrency_blocked(workspace):
    first=rotate(workspace,key='week1')
    calls=workspace.adapter.counts['instruments']
    assert workspace.rotate(NOW,key='week1')==first
    assert workspace.adapter.counts['instruments']==calls
    with workspace.store.job('HELD'):
        with pytest.raises(RuntimeError,match='Another workspace job'):
            workspace.rotate(NOW)
    assert workspace.store.jobs()[0]['status']=='COMPLETED'


def test_failed_rotation_does_not_publish_and_rollback_versions(workspace):
    rotate(workspace)
    first=workspace.store.versions()[0]
    with patch.object(workspace.adapter,'instruments',side_effect=RuntimeError('API down')):
        with pytest.raises(RuntimeError): workspace.rotate(NOW)
    assert workspace.store.versions()[0]['id']==first['id']
    assert len(workspace.store.members())==2
    assert any(j['status']=='FAILED' for j in workspace.store.jobs())
    workspace.store.manage('BEAR','remove')
    workspace.store.rollback(first['id'])
    assert len(workspace.store.members())==2
    assert all(m['status']=='REVIEW_REQUIRED' for m in workspace.store.members())


def test_atomic_publication_failure(workspace):
    rotate(workspace)
    before=workspace.store.members()
    with workspace.store.transaction() as c:
        c.execute("CREATE TRIGGER fail_publish BEFORE INSERT ON ft_versions BEGIN SELECT RAISE(ABORT, 'failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        workspace.store.manage('BEAR','remove')
    assert workspace.store.members()==before


@pytest.mark.parametrize('failure', ['eligibility_audit', 'provider_cleanup'])
def test_late_rotation_failure_preserves_previous_version(workspace,failure):
    rotate(workspace)
    before=workspace.store.members()
    version=workspace.store.versions()[0]['id']
    if failure=='eligibility_audit':
        original=workspace.store.record
        def record(job_id,symbol,kind,payload):
            if kind=='ELIGIBILITY':
                raise RuntimeError('audit unavailable')
            return original(job_id,symbol,kind,payload)
        target=patch.object(workspace.store,'record',side_effect=record)
    else:
        target=patch.object(workspace.adapter,'end',side_effect=RuntimeError('cleanup unavailable'))
    with target, pytest.raises(RuntimeError):
        rotate(workspace,{'BEAR':evaluation('NONE',20),'RECOVER':evaluation(LISTS[1]),'UNSELECTED':evaluation(LISTS[0])})
    assert workspace.store.members()==before
    assert workspace.store.versions()[0]['id']==version
    assert any(job['status']=='FAILED' for job in workspace.store.jobs())
    with workspace.store.job('LEASE_RELEASE_CHECK'):
        pass


def test_weekly_history_missing_internal_session_is_unknown():
    history=frame(np.linspace(220,100,280)).drop(frame(np.linspace(220,100,280)).index[-20])
    assert technical(history,CFG,NOW)['reason_codes']==['HISTORY_MISSING_SESSIONS']


def test_weekly_history_uses_covering_benchmark_exchange_calendar():
    history=frame(np.linspace(220,100,280))
    holiday=history.index[-20]
    benchmark=history.drop(holiday)
    closed=history.drop(holiday)
    result=technical(closed,CFG,NOW,benchmark)
    assert result['classification']!='UNKNOWN_DATA'
    assert result['technical_score'] is not None
    incomplete=closed.drop(closed.index[-10])
    assert technical(incomplete,CFG,NOW,benchmark)['reason_codes']==['HISTORY_MISSING_SESSIONS']


@pytest.mark.parametrize('column,multiplier', [('Open',1.1),('Open',.9),('Close',1.1),('Close',.9)])
def test_weekly_history_ohlc_outside_range_is_unknown(column,multiplier):
    history=frame(np.linspace(220,100,280))
    history.loc[history.index[-1],column]*=multiplier
    assert technical(history,CFG,NOW)['reason_codes']==['HISTORY_INVALID']


def test_market_bias_custom_thresholds_preserve_report_only_adjustments():
    inputs={name:{'status':'PASS','value':.5,'source':'OFFLINE_FIXTURE','as_of':NOW.isoformat()}
            for name in MarketBias.WEIGHTS}
    baseline=MarketBias.evaluate(inputs)
    custom=MarketBias.evaluate(inputs,directional_threshold=30,strong_threshold=45)
    assert baseline['classification']=='BULLISH'
    assert custom['classification']=='STRONG_BULLISH'
    assert custom['score']==baseline['score']==50
    assert custom['mode']=='REPORT_ONLY'
    assert custom['thresholds']=={'directional':30,'strong':45}
    assert custom['long_adjustment']==10 and custom['short_adjustment']==-10
    with pytest.raises(ValueError):
        replace(CFG,bias_directional_threshold=60,bias_strong_threshold=40)
    with pytest.raises(ValueError):
        MarketBias.evaluate(inputs,directional_threshold=0)


def test_market_bias_threshold_environment_configuration(monkeypatch):
    monkeypatch.setenv('FUTURES_WORKSPACE_BIAS_DIRECTIONAL_THRESHOLD','25')
    monkeypatch.setenv('FUTURES_WORKSPACE_BIAS_STRONG_THRESHOLD','70')
    cfg=WorkspaceConfig.from_env()
    assert (cfg.bias_directional_threshold,cfg.bias_strong_threshold)==(25,70)


def test_manual_management_and_canonical_dedup(workspace):
    workspace.manage('BEAR','add',LISTS[0])
    with pytest.raises(ValueError,match='already has'):
        workspace.manage('BEAR','add',LISTS[1])
    workspace.manage('BEAR','disable')
    assert workspace.store.members()[0]['enabled']==0
    workspace.manage('BEAR','enable')
    workspace.manage('BEAR','pin')
    workspace.manage('BEAR','unpin')
    assert not workspace.store.members()[0]['pinned']
    with pytest.raises(ValueError,match='eligible'):
        workspace.manage('INVALID','add',LISTS[0])


def test_daily_single_job_only_selected_both_directions_original_scores(workspace):
    rotate(workspace)
    fake=Mock()
    reviewed=[{'symbol':'BEAR','side':'SHORT','technical_score':83,'final_decision':'APPROVED','reason_codes':[]},
        {'symbol':'RECOVER','side':'LONG','technical_score':78,'final_decision':'APPROVED','reason_codes':[]}]
    fake.scan.return_value={'reviewed':reviewed,'generated_at':NOW.isoformat(),'report_a':[],'report_b':[],'report_c':[],'report_d':{},'report_e':{}}
    factory=Mock(return_value=fake)
    workspace.scanner_factory=factory
    calls=workspace.adapter.counts['instruments']
    report=workspace.scan_daily(NOW)
    fake.scan.assert_called_once()
    assert fake.scan.call_args.kwargs['selected_symbols']==['BEAR','RECOVER']
    assert workspace.adapter.counts['instruments']==calls
    assert len(report['long'])==len(report['short'])==1
    assert report['long'][0]['original_daily_score']==78
    assert report['short'][0]['original_daily_score']==83
    config=factory.call_args.kwargs['config']
    original=FuturesScanConfig.from_env()
    assert config.weights('bullish')==original.weights('bullish')
    assert config.weights('bearish')==original.weights('bearish')
    assert (config.target_fraction,config.stop_fraction)==(original.target_fraction,original.stop_fraction)
    assert (config.entry_cutoff_hour,config.entry_cutoff_minute)==(15,0)
    assert (config.intraday_exit_hour,config.intraday_exit_minute)==(15,10)
    assert len(workspace.store.jobs('DAILY_TRADING'))==1
    assert set(('report_a','report_b','report_c','report_d','report_e'))<=report['report'].keys()

@pytest.mark.parametrize('decision,expected',[('UNKNOWN','UNKNOWN_DATA'),('REJECT','NO_TRADE'),('WAIT','NO_TRADE')])
def test_daily_nontrade_states(workspace,decision,expected):
    rotate(workspace)
    workspace.scanner_factory=Mock(return_value=SimpleNamespace(scan=lambda **kwargs:{'reviewed':[{'symbol':'BEAR','side':'SHORT','final_decision':decision,'reason_codes':['DATA_GAP']}]}))
    report=workspace.scan_daily(NOW)
    assert next(r for r in report['other'] if r['symbol']=='BEAR')['workspace_decision']==expected


def test_daily_conflicts_and_stale_selection(workspace):
    rotate(workspace)
    workspace.scanner_factory=Mock(return_value=SimpleNamespace(scan=lambda **kwargs:{'reviewed':[
        {'symbol':'BEAR','side':'SHORT','final_decision':'APPROVED','reason_codes':[]},
        {'symbol':'BEAR','side':'LONG','final_decision':'APPROVED','reason_codes':[]}]}))
    result=workspace.scan_daily(NOW)
    assert next(r for r in result['other'] if r['symbol']=='BEAR')['workspace_decision']=='CONFLICT'
    assert not result['short']
    workspace.scanner_factory=Mock(return_value=SimpleNamespace(scan=lambda **kwargs:{'reviewed':[{'symbol':'BEAR','side':'SHORT','final_decision':'APPROVED','reason_codes':[]}]}))
    result=workspace.scan_daily(NOW+pd.Timedelta(days=10))
    assert not result['short']
    assert 'WEEKLY_MEMBERSHIP_STALE_RECHECK_REQUIRED' in result['other'][0]['reason_codes']


def test_empty_daily_never_initializes(workspace):
    workspace.adapter.instruments=Mock(side_effect=AssertionError('No discovery allowed'))
    assert workspace.scan_daily(NOW)['selected_count']==0
    workspace.adapter.instruments.assert_not_called()

@pytest.mark.parametrize('score,label',[(-1,'STRONG_BEARISH'),(-.4,'BEARISH'),(0,'NEUTRAL'),(.4,'BULLISH'),(1,'STRONG_BULLISH')])
def test_market_bias_bands_report_only(score,label):
    inputs={k:{'value':score,'status':'PASS','source':'fixture','as_of':NOW.isoformat()} for k in MarketBias.WEIGHTS}
    result=MarketBias.evaluate(inputs)
    assert result['classification']==label and result['mode']=='REPORT_ONLY'
    assert result['long_adjustment']==-result['short_adjustment']
    assert MarketBias.evaluate({})['classification']=='UNKNOWN'


def test_missing_fundamentals_and_sector_specific_interpretation():
    missing=fundamental_context(None,'IT',NOW)
    assert missing['status']=='UNKNOWN' and missing['bullish_score'] is None
    bank=fundamental_context({'profit_growth':10,'debt_to_equity':20,'source':'disclosure','as_of':'2026-09-30',
        'evidence':{'profit_growth':{'period':'2026-06-30'}}},'BANKING',NOW)
    assert bank['bullish_score']==100
    assert 'CAPITAL_ASSET_QUALITY_UNKNOWN' in bank['interpretation']
    item=evaluation(LISTS[1])
    score=discovery_score(item,missing,{'bullish_score':None},CFG)
    assert score['coverage_percent']==70
    assert score['ranking_score']==score['experimental_score']==80
    assert score['components']['news'] is None


def test_feature_disabled_no_data_reads(workspace):
    workspace.config=replace(CFG,enabled=False)
    for method in (workspace.rotate,workspace.scan_daily,workspace.refresh_universe):
        with pytest.raises(ValueError,match='disabled'): method()
    assert workspace.adapter.counts=={}


def test_scheduler_separate_refresh_weekly_idempotency_no_daily(workspace):
    with patch.object(workspace,'rotate',wraps=workspace.rotate) as run:
        with patch('src.futures_workspace.service.technical',return_value=evaluation(LISTS[0])):
            scheduler=WorkspaceScheduler(workspace)
            scheduler.tick(NOW)
            scheduler.tick(NOW)
        assert run.call_count==1
    assert not workspace.store.jobs('DAILY_TRADING')
    assert len(workspace.store.jobs('UNIVERSE_REFRESH'))==1


def test_scheduled_rotation_reports_progress_before_slow_sources(workspace):
    progress=Mock()
    def fundamentals(symbol,now):
        assert any(call.args[2]==symbol+': fundamental data' for call in progress.call_args_list)
        return {'status':'UNKNOWN','bullish_score':None,'bearish_score':None}
    workspace.adapter.fundamentals=fundamentals
    with patch('src.futures_workspace.service.technical',return_value=evaluation(LISTS[0])):
        WorkspaceScheduler(workspace).tick(NOW,progress=progress)
    stages=[call.args[2] for call in progress.call_args_list]
    assert stages[0]=='Refreshing Futures instrument universe'
    assert 'Loading Futures instrument metadata' in stages
    assert any(stage.endswith(': historical and sector data') for stage in stages)
    assert any(stage.endswith(': news and events') for stage in stages)
    assert progress.call_args.args[:2]==(3,3)


def test_recheck_does_not_admit_new_universe_symbols(workspace):
    rotate(workspace)
    rotate(workspace,{'BEAR':evaluation(LISTS[0]),'RECOVER':evaluation(LISTS[1]),'UNSELECTED':evaluation(LISTS[0],100)},selected_only=True)
    assert {m['symbol'] for m in workspace.store.members()}=={'BEAR','RECOVER'}


def test_actual_scanner_selected_override_cannot_discover_full_universe():
    from test_bidirectional_futures import scanner_fixture,NOW as SCAN_NOW
    scanner,provider=scanner_fixture()
    scanner.platform._universe_symbols=Mock(side_effect=AssertionError('Full universe forbidden'))
    report=scanner.scan(5,selected_symbols=['DOWN','DOWN','UP'],now=SCAN_NOW,include_backtest=False)
    assert report['universe_size']==2
    assert {r['symbol'] for r in report['reviewed']}=={'DOWN','UP'}
    scanner.platform._universe_symbols.assert_not_called()
    assert report['approved_count']==0
    provider.begin_live_refresh.assert_called_once_with(['DOWN','UP'])
    assert set(('report_a','report_b','report_c','report_d','report_e'))<=report.keys()
    with pytest.raises(Exception,match='preparation modes'):
        scanner.scan(selected_symbols=['DOWN'],mode='FULL_RESEARCH')


def test_workspace_deadlines_and_ledger_still_bind_final_safety():
    from test_futures_correction_regressions import safety_fixture,budget
    from src.futures.execution_safety import finalize
    from test_bidirectional_futures import NOW as SCAN_NOW
    cfg=replace(FuturesScanConfig(),entry_cutoff_hour=15,entry_cutoff_minute=0,intraday_exit_hour=15,intraday_exit_minute=10)
    item,report,context,frames=safety_fixture(now=SCAN_NOW)
    finalize(report,frames,budget(SCAN_NOW,count=2,status='FAIL'),SCAN_NOW,cfg)
    assert not report['report_c']
    now=SCAN_NOW.replace(hour=15,minute=0)
    item,report,context,frames=safety_fixture(now=now)
    finalize(report,frames,budget(now),now,cfg)
    assert not report['report_c']
    window=report['execution_safety']['entry_window']
    assert window['manual_exit_deadline'].startswith('2026-10-08T15:10')
    assert window['automatic_square_off'] is False


def test_shared_provider_fetches_each_contract_history_once():
    from src.futures_workspace.adapter import SelectedScanProvider
    provider=SimpleNamespace(get_nfo_instruments=Mock(return_value=[contract('BEAR')]),
        get_annual_history=Mock(return_value=frame(np.linspace(200,100,280))),get_data=Mock(return_value=frame(np.linspace(200,100,280))))
    shared=SelectedScanProvider(provider)
    gateway=Mock()
    gateway.history.return_value=[{'date':'2026-10-09 09:15:00+05:30','open':100,'high':101,'low':99,'close':100,'volume':2000,'oi':10000}]
    gateway.quotes.return_value={'NFO:BEAR26OCTFUT':{'last_price':100},'NSE:BEAR':{'last_price':99}}
    shared._gateway=gateway
    for _ in range(2):
        shared.get_annual_history('BEAR')
        shared.get_futures_execution_data('BEAR',contract())
    assert gateway.history.call_count==2  # One daily history and one intraday history.
    assert gateway.quotes.call_count==1
    assert provider.get_annual_history.call_count==1
    assert provider.get_nfo_instruments.call_count==1


def test_weekly_stale_quote_and_low_liquidity_fail_closed():
    adapter=Adapter(); quotes=adapter.quotes({'BEAR':contract()})
    quote=quotes['NFO:BEAR26OCTFUT']
    assert FuturesWorkspace.quote_quality(quote,CFG,NOW)['status']=='PASS'
    quote['timestamp']='2026-09-01 15:20:00+05:30'
    assert FuturesWorkspace.quote_quality(quote,CFG,NOW)['status']=='UNKNOWN'
    quote['timestamp']='2026-10-09 15:20:00+05:30';quote['volume']=1
    assert FuturesWorkspace.quote_quality(quote,CFG,NOW)['status']=='FAIL'
    quote['volume']=2000;quote['depth']['sell'][0]['price']=102
    assert FuturesWorkspace.quote_quality(quote,CFG,NOW)['status']=='FAIL'


def test_additive_schema_preserves_existing_reports_and_ledger(tmp_path):
    from src.ui.database import ReportDatabase
    path=tmp_path/'existing.db'
    original=ReportDatabase(path)
    with original._connect() as c:
        c.execute("INSERT INTO watchlists(name,created_at) VALUES('original','2026-10-01')")
        c.commit()
    WorkspaceStore(path)
    with original._connect() as c:
        assert c.execute("SELECT name FROM watchlists").fetchone()[0]=='original'
        assert c.execute("SELECT name FROM sqlite_master WHERE name='report_runs'").fetchone()


def test_research_replay_unknown_without_archived_universe_and_sample_limits():
    from src.futures_workspace.performance import WorkspaceResearchReplay,summarize
    assert WorkspaceResearchReplay().run({})['status']=='UNKNOWN'
    result=WorkspaceResearchReplay().run({'universe_snapshots':[{'as_of':NOW.isoformat(),'source':'current-master','recorded_at':(NOW+pd.Timedelta(days=1)).isoformat(),'complete_universe':True}]})
    assert result['failures'][0]['reason']=='UNVERIFIED_POINT_IN_TIME_UNIVERSE'
    stats=summarize([{'net_pnl':10,'exit_timestamp':NOW.isoformat()}])
    assert stats['win_rate'] is None and stats['sample_status']=='INSUFFICIENT'
    assert stats['future_probability'] is None


def test_daily_real_clock_is_not_frozen_at_job_start(workspace):
    rotate(workspace)
    scanner=Mock();scanner.scan.return_value={'reviewed':[]}
    workspace.scanner_factory=Mock(return_value=scanner)
    workspace.scan_daily()
    assert scanner.scan.call_args.kwargs['now'] is None


def test_adjusted_history_reuses_existing_provider_without_raw_splicing(tmp_path,monkeypatch):
    from src.futures_workspace.adapter import RepositoryAdapter
    provider=SimpleNamespace(get_data=Mock(side_effect=AssertionError('Raw stock history should not be spliced')))
    platform=SimpleNamespace(provider=provider)
    adapter=RepositoryAdapter(platform,CFG);adapter.now=NOW
    monkeypatch.chdir(tmp_path)
    f=frame(np.linspace(200,100,280))
    with patch('src.providers.yahoo_provider.YahooProvider.get_historical_data',return_value=f) as fetch:
        first=adapter.history('BEAR')
        second=adapter.history('BEAR')
        assert first is second
        fetch.assert_called_once_with('BEAR',period='2y',interval='1d')
    assert first.attrs['price_adjustment']=='ADJUSTED'
    provider.get_data.assert_not_called()


def test_sector_divergence_does_not_override_market_bias():
    from src.futures_workspace.adapter import RepositoryAdapter
    provider=SimpleNamespace(get_session_intraday=Mock())
    dates=pd.date_range('2026-10-09 09:15',periods=10,freq='5min',tz='Asia/Kolkata')
    def index_bars(symbol):
        prices=np.linspace(100,101,10) if symbol.startswith('NIFTY') else np.linspace(100,99,10)
        return pd.DataFrame({'Open':prices,'High':prices+1,'Low':prices-1,'Close':prices,'Volume':0},index=dates)
    provider.get_session_intraday.side_effect=index_bars
    adapter=RepositoryAdapter(SimpleNamespace(provider=provider),CFG)
    result=adapter.market_bias(pd.Timestamp('2026-10-09 10:05',tz='Asia/Kolkata'))
    assert result['classification'] in ('BULLISH','STRONG_BULLISH')
    assert result['mode']=='REPORT_ONLY'
    assert result['inputs']['sector_participation']['sectors']
    assert 'breadth' not in result['inputs']


def test_stopped_worker_recovery_keeps_committed_watchlists(workspace):
    rotate(workspace)
    before=workspace.store.members()
    with workspace.store.transaction() as c:
        c.execute("INSERT INTO ft_jobs(id,kind,status,started_at) VALUES('crashed','WEEKLY_ROTATION','RUNNING',?)",(NOW.isoformat(),))
        c.execute("INSERT INTO ft_lease VALUES('workspace','crashed')")
    with pytest.raises(ValueError,match='Stop the original'):
        workspace.store.recover_job('crashed')
    workspace.store.recover_job('crashed',worker_stopped=True)
    assert workspace.store.members()==before
    rotate(workspace)


def test_daily_batched_quotes_shared_for_both_selected_contracts():
    from src.futures_workspace.adapter import SelectedScanProvider
    provider=SimpleNamespace(get_nfo_instruments=Mock(return_value=[contract('BEAR'),contract('RECOVER',instrument_token=43)]))
    shared=SelectedScanProvider(provider,['BEAR','RECOVER','BEAR'])
    gateway=Mock();gateway.history.return_value=[{'date':'2026-10-09 09:15:00+05:30','open':100,'high':101,'low':99,'close':100,'volume':2000,'oi':10000}]
    gateway.quotes.return_value={};shared._gateway=gateway
    for symbol in ('BEAR','RECOVER'):
        shared.get_futures_execution_data(symbol,contract(symbol))
    assert gateway.history.call_count==4
    gateway.quotes.assert_called_once_with(['NFO:BEAR26OCTFUT','NFO:RECOVER26OCTFUT','NSE:BEAR','NSE:RECOVER'])


def test_workspace_streamlit_all_eight_sections(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from src.futures_workspace.ui import JobController
    monkeypatch.setenv('FUTURES_WORKSPACE_ENABLED','true')
    path=tmp_path/'ui.db'
    jobs=JobController()
    code=f'''from types import SimpleNamespace
from src.futures_workspace.ui import render
from src.application.settings import PlatformSettings
render(SimpleNamespace(settings=PlatformSettings(market_data_source="cache"),provider=object()),SimpleNamespace(path={str(path)!r}))
'''
    with patch('src.futures_workspace.ui.controller',return_value=jobs),patch('src.futures_workspace.scheduler.WorkspaceScheduler.tick',return_value={'status':'ALREADY_COMPLETED'}):
        app=AppTest.from_string(code).run(timeout=15)
        assert not app.exception
        assert app.title[0].value=='Futures Trading'
        for section in ('Daily Trading','Shorting Stocks','Recovering Stocks','Weekly Rotation','Rotation History','Analysis & Performance','Settings'):
            app.radio[0].set_value(section).run(timeout=15)
            assert not app.exception,section
            if section=='Daily Trading':
                assert sum(b.label=='SCAN SELECTED STOCKS' for b in app.button)==1
    jobs.pool.shutdown(wait=True)


def test_workspace_job_failure_reenables_buttons_and_displays_error(tmp_path,monkeypatch):
    from concurrent.futures import Future
    from streamlit.testing.v1 import AppTest
    from src.futures_workspace.ui import JobController
    import time
    monkeypatch.setenv('FUTURES_WORKSPACE_ENABLED','true')
    jobs=JobController()
    jobs.future=Future()
    jobs.label='Scheduled initialization / weekly rotation'
    jobs.started_at=jobs.updated_at=time.monotonic()-130
    jobs.next_schedule=time.monotonic()+300
    jobs.progress=(0,0,'Refreshing Futures instrument universe')
    code=f'''from types import SimpleNamespace
from src.futures_workspace.ui import render
from src.application.settings import PlatformSettings
render(SimpleNamespace(settings=PlatformSettings(market_data_source="cache")),SimpleNamespace(path={str(tmp_path/'ui.db')!r}))
'''
    try:
        with patch('src.futures_workspace.ui.controller',return_value=jobs):
            app=AppTest.from_string(code).run(timeout=15)
            app.radio[0].set_value('Weekly Rotation').run(timeout=15)
            button=next(b for b in app.button if b.label=='Run Full Universe Scan')
            assert button.disabled
            assert any('No progress update' in warning.value for warning in app.warning)
            jobs.future.set_exception(RuntimeError('Instrument feed unavailable'))
            app.run(timeout=15)
            assert not app.exception
            assert not next(b for b in app.button if b.label=='Run Full Universe Scan').disabled
            assert any('Instrument feed unavailable' in error.value for error in app.error)
    finally:
        jobs.pool.shutdown(wait=True)


def test_saved_job_lock_ui_recovery_without_starting_worker(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from src.futures_workspace.ui import JobController
    monkeypatch.setenv('FUTURES_WORKSPACE_ENABLED','true')
    path=tmp_path/'ui.db'
    store=WorkspaceStore(path)
    with store.transaction() as c:
        c.execute("INSERT INTO ft_jobs(id,kind,status,started_at) VALUES('abandoned','WEEKLY_ROTATION','RUNNING',?)",(NOW.isoformat(),))
        c.execute("INSERT INTO ft_lease VALUES('workspace','abandoned')")
    jobs=JobController()
    code=f'''from types import SimpleNamespace
from src.futures_workspace.ui import render
from src.application.settings import PlatformSettings
render(SimpleNamespace(settings=PlatformSettings(market_data_source="cache")),SimpleNamespace(path={str(path)!r}))
'''
    try:
        with patch('src.futures_workspace.ui.controller',return_value=jobs),patch('src.futures_workspace.scheduler.WorkspaceScheduler.tick') as tick:
            app=AppTest.from_string(code).run(timeout=15)
            assert not app.exception
            assert any('saved job lock' in w.value for w in app.warning)
            tick.assert_not_called()
            next(b for b in app.button if b.label=='Recover stopped job').click().run(timeout=15)
            assert not app.exception
            assert store.locked_job() is None
            assert store.jobs()[0]['status']=='FAILED'
            tick.assert_not_called()
            app.radio[0].set_value('Weekly Rotation').run(timeout=15)
            assert not next(b for b in app.button if b.label=='Run Full Universe Scan').disabled
    finally:
        jobs.pool.shutdown(wait=True)


def test_production_universe_maps_actual_nse_equities_and_excludes_unknown_indices():
    from src.futures_workspace.adapter import RepositoryAdapter
    adapter=RepositoryAdapter(SimpleNamespace(provider=object(),settings=SimpleNamespace(market_data_source='kite')),CFG)
    gateway=Mock();adapter.gateway=gateway
    nfo=[contract('BEAR'),contract('NEWINDEX')]
    nse=[{'tradingsymbol':'BEAR','exchange':'NSE','segment':'NSE','instrument_type':'EQ','instrument_token':200},
         {'tradingsymbol':'NEWINDEX','exchange':'NSE','segment':'INDICES','instrument_type':'EQ','instrument_token':201}]
    gateway.kite.instruments.side_effect=[nfo,nse]
    gateway.request.side_effect=lambda kind,operation:operation()
    found=adapter.instruments()
    assert len(found)==1 and found[0]['name']=='BEAR'
    assert found[0]['underlying_instrument_token']==200
    assert gateway.kite.instruments.call_args_list[0].args==('NFO',)
    assert gateway.kite.instruments.call_args_list[1].args==('NSE',)


def test_verified_major_event_marks_weekly_review(workspace):
    rotate(workspace)
    workspace.adapter.news=Mock(return_value={'status':'CONTEXT_ONLY','events':{'hard_block':True,'event_data_availability_state':'COMPLETE'}})
    rotate(workspace)
    assert not workspace.store.members(active=True)
    assert all(m['status']=='REVIEW_REQUIRED' for m in workspace.store.members())


def test_new_api_selected_route_and_disabled_behavior(workspace,monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv('MARKET_DATA_SOURCE','cache')
    from src import api
    with patch.object(api,'TradingPlatform',return_value=workspace.platform),patch('src.futures_workspace.service.FuturesWorkspace',return_value=workspace):
        assert api.futures_trading_scan_selected()['selected_count']==0
        assert any(r.path=='/futures-trading/scan-selected' and 'POST' in r.methods for r in api.app.routes)
        assert workspace.adapter.counts=={}
        assert api.futures_trading_versions()==[]
        workspace.config=replace(CFG,enabled=False)
        with pytest.raises(HTTPException) as rejected:
            api.futures_trading_rotate()
        assert rejected.value.status_code==409
        assert workspace.adapter.counts=={}


def test_replay_never_uses_future_equity_candles_for_classification():
    from src.futures_workspace.performance import WorkspaceResearchReplay
    f=frame(np.linspace(200,100,280))
    now=f.index[-10]+pd.Timedelta(hours=16)
    rows=f.reset_index(names='date').to_dict('records')
    snapshot={'as_of':now.isoformat(),'recorded_at':now.isoformat(),'source':'ARCHIVED_FIXTURE',
        'complete_universe':True,'instruments':[contract('BEAR')]}
    seen=[]
    def classify(prefix,*args):
        seen.append(prefix.index.max())
        return evaluation(LISTS[0])
    with patch('src.futures_workspace.performance.technical',side_effect=classify):
        result=WorkspaceResearchReplay(CFG).run({'universe_snapshots':[snapshot],
            'equity_histories':{'BEAR':{'price_adjustment':'ADJUSTED','rows':rows}},'futures_histories':{}})
    assert seen and max(seen)<now
    assert result['failures'][0]['reason']=='EXACT_CONTRACT_HISTORY_MISSING'
    assert result['futures_metrics']['win_rate'] is None


def test_quarterly_fundamental_context_requires_actual_reporting_period():
    snapshot={'source':'fixture','as_of':NOW.isoformat(),'quarterly_revenue_growth_pct':[4,5,6],
        'quarterly_profit_growth_pct':[2,3,4],'evidence':{
            'quarterly_revenue_growth_pct':{'period':'2026-06-30','url':'fixture'},
            'quarterly_profit_growth_pct':{'period':'2026-06-30','url':'fixture'}}}
    result=fundamental_context(snapshot,'IT',NOW)
    assert result['status']=='AVAILABLE' and result['bullish_score']==100
    assert result['reporting_period']['profit_growth']=='2026-06-30'
    assert result['publication_date_status']=='UNKNOWN'
    snapshot['evidence']['quarterly_profit_growth_pct']['period']='2024-06-30'
    assert fundamental_context(snapshot,'IT',NOW)['status']=='UNKNOWN'


def test_expiry_day_contract_is_inactive_after_market_close():
    expiry_day=pd.Timestamp('2026-10-29 15:29',tz='Asia/Kolkata')
    assert universe([contract()],expiry_day)
    assert not universe([contract()],expiry_day+pd.Timedelta(minutes=1))
