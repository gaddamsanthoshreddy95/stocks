"""Additive A-D invariants, readiness safety, cached historical evidence and surfaces."""
import ast
import copy
import json
from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pandas as pd
import pytest
from futures_mode_fixture import fixture,seed,NOW
from src.futures.config import FuturesScanConfig
from src.futures.costs import FuturesCosts
from src.futures.report_e import build_report_e,append_report_e,render_report_e,TITLE,entry_analysis,quality
from src.futures.report_e_config import ReportEConfig
from src.futures.report_e_history import ReportEBacktester,build_history_evidence,summarise_trades,candidate_history,policy_key
from src.futures.scanner import REQUIRED_EXECUTION
from src.futures.execution_safety import REQUIRED_SCAN_GATES
from src.futures.preparation import PreparedScanner
from src.presenter.futures_opportunities import FuturesOpportunitiesPresenter
from src.quality.futures_selection import REQUIRED_CHECKS

BASE=Path('tests/fixtures/report_e_before.json')


def baseline():return json.loads(BASE.read_text())


def approved(side='LONG'):
    now=NOW
    e={'price':100,'signal_timestamp':(now-pd.Timedelta(minutes=5)).isoformat(),'rsi_14':60 if side=='LONG' else 40,
       'adx_14':35,'relative_volume':2,'vwap':99 if side=='LONG' else 101,'macd_histogram':1 if side=='LONG' else -1,
       'ema_values':{'EMA9':100,'EMA21':99 if side=='LONG' else 101,'EMA50':98 if side=='LONG' else 102,'EMA200':97 if side=='LONG' else 103},
       'trigger':99.5,'short_trigger_price':100.5,'support':98,'resistance':102,'daily_atr':1,'selling_or_buying_exhaustion':False}
    sign=1 if side=='LONG' else -1
    item={'symbol':'TEST','side':side,'technical_score':80,'setup_type':'TREND_CONTINUATION','final_decision':'APPROVED','execution_reviewed':True,
          'futures_setup':{'confirmed':True,'timing':'READY','evidence':e},'reason_codes':[], 'missing_execution_checks':[], 'failed_execution_checks':[],
          'company_research':{'status':'PASS','policy_review_required':False,'checks':{k:{'status':'PASS','score':90,'factors':{}} for k in REQUIRED_CHECKS},'fundamental_assessment':{'score':90}},
          'scan_gates':{k:{'status':'PASS'} for k in REQUIRED_SCAN_GATES},
          'execution_checks':{k:{'status':'PASS','factors':{},'reason_codes':[]} for k in (*REQUIRED_EXECUTION,'listed_futures_contract','futures_target_space_quality','futures_sector_strength_quality')},
          'contract':{'tradingsymbol':'TEST26OCTFUT','lot_size':100},'futures_quote':{'last_price':100,'timestamp':now.isoformat()},'news_alignment':{'approved':True},
          'news':{'news_state':'ANALYZED','checked_at':now.isoformat()}, 'plan':{'entry':100,'target':100*(1+sign*.003),'stop_loss':100*(1-sign*.002),'movement_basis':'FUTURES','net_risk_reward':1}}
    context={'completed_candles':[{'timestamp':(now-pd.Timedelta(minutes=10)).isoformat(),'RSI':59 if side=='LONG' else 41},
                                  {'timestamp':(now-pd.Timedelta(minutes=5)).isoformat(),'RSI':60 if side=='LONG' else 40,'Open':99 if side=='LONG' else 101,'Close':100,'Low':99,'High':101}], 'missing_candles':[]}
    report={'generated_at':now.isoformat(),'config':asdict(FuturesScanConfig()),'reviewed':[item],'report_a':[item] if side=='LONG' else [],'report_b':[item] if side=='SHORT' else [],'report_c':[item],'report_d':{'candidates':[]}}
    return item,report,context


def test_frozen_a_to_d_all_values_and_rendering_unchanged():
    r=baseline();before=copy.deepcopy(r)
    append_report_e(r)
    assert {k:v for k,v in r.items() if k!='report_e'}==before
    for key in ('report_a','report_b','report_c','report_d'):assert r[key]==before[key]
    # Frozen legacy inputs retain their rendering; approved calculation corrections
    # on new executions are compared separately in correction regressions.
    text=FuturesOpportunitiesPresenter.render(r)
    # JSON round-trips tuples to lists; normalize only this fixture's growth tuples.
    legacy_text=Path('tests/fixtures/report_e_before.md').read_text().replace('(4, 5, 6)', '[4, 5, 6]')
    assert text.split('\n\n## '+TITLE)[0]==legacy_text
    assert text.index('Report A')<text.index('Report B')<text.index('Report C')<text.index('ADDITIONAL ANALYSIS')<text.index(TITLE)
    assert list(r)[-2:]==['report_d','report_e']


def test_scanner_identical_inputs_no_second_scan_or_kite_calls():
    scanner,provider=fixture()
    with patch('src.futures.report_e.append_report_e',side_effect=lambda r,**kw:r):
        before=scanner.scan(now=NOW,include_backtest=False)
    current,other=fixture()
    with patch.object(current,'_scan',wraps=current._scan) as scan:
        after=current.scan(now=NOW,include_backtest=False)
    scan.assert_called_once()
    for key in ('report_a','report_b','report_c','report_d','reviewed','approved_count','config','cost_assumptions'):
        assert before[key]==after[key]
    for name in ('quote','historical_data','order_margins','margins'):
        assert getattr(provider.provider.kite,name).call_args_list==getattr(other.provider.kite,name).call_args_list


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_ready_needs_original_approval_and_valid_fixed_futures_levels(side):
    item,report,context=approved(side)
    result=entry_analysis(item,report,context)
    assert result['entry_status']=='READY'
    assert result['entry']==100
    assert result['target']==pytest.approx(100*(1.003 if side=='LONG' else .997))
    assert result['stop_loss']==pytest.approx(100*(.998 if side=='LONG' else 1.002))
    original=copy.deepcopy(item)
    item['final_decision']='WAIT'
    assert entry_analysis(item,report,context)['entry_status']!='READY'
    assert item['plan']==original['plan']


@pytest.mark.parametrize('name',REQUIRED_EXECUTION)
@pytest.mark.parametrize('status',['FAIL','UNKNOWN'])
def test_every_existing_mandatory_gate_remains_binding(name,status):
    item,report,context=approved()
    item['execution_checks'][name]['status']=status
    result=entry_analysis(item,report,context)
    assert result['entry_status']!='READY'
    assert result['entry'] is None and result['target'] is None and result['stop_loss'] is None


@pytest.mark.parametrize('problem',['stale_quote','missing_bars','pending_news','stale_news','missing_context','after_market','exhausted','underlying_policy'])
def test_readiness_unknowns_extensions_and_policy_conflicts(problem):
    item,report,context=approved()
    if problem=='stale_quote':item['futures_quote']['timestamp']=(NOW-pd.Timedelta(hours=2)).isoformat()
    if problem=='missing_bars':context['missing_candles']=[NOW.isoformat()]
    if problem=='pending_news':item['news']['news_state']='UNANALYSED_NEW_HEADLINES'
    if problem=='stale_news':item['news']['checked_at']=(NOW-pd.Timedelta(hours=2)).isoformat()
    if problem=='missing_context':context={}
    if problem=='after_market':report['generated_at']=NOW.replace(hour=22).isoformat();report['research_only']=True
    if problem=='exhausted':item['futures_setup']['timing']='TOO LATE'
    if problem=='underlying_policy':item['plan']['movement_basis']='UNDERLYING'
    result=entry_analysis(item,report,context)
    assert result['entry_status']!='READY'
    if problem in ('stale_quote','missing_bars','after_market','underlying_policy'):
        assert result['target'] is None and result['stop_loss'] is None and result['execution_rr'] is None
    if problem=='underlying_policy':assert result['policy_review_required']


def test_no_equity_substitution_for_futures_entry():
    item,report,context=approved()
    item.pop('futures_quote');item.pop('plan');item.pop('futures_setup')
    item['evidence']={'price':555,'support':500,'resistance':600}
    t=entry_analysis(item,report,context)
    assert t['entry'] is None and t['target'] is None and t['stop_loss'] is None
    assert t['futures_reference_price'] is None and t['support'] is None


def test_quality_scores_separate_missing_data_not_favorable_long_short_independent():
    item,r,context=approved();item['sector']='BANKING'
    complete=quality(item,ReportEConfig(),r['config'])
    assert complete['score']!=item['technical_score']
    assert item['company_research']['checks']['debt_free_quality']['status']=='PASS'
    assert complete['components']['fundamentals']['features']['debt_free_quality']['score'] is None
    weaker=copy.deepcopy(item);weaker['futures_setup']['evidence'].pop('rsi_14')
    missing=quality(weaker,ReportEConfig(),r['config'])
    assert missing['score']<complete['score'] and missing['coverage_percent']<complete['coverage_percent']
    short=copy.deepcopy(item);short['side']='SHORT';short['company_research']['short_interpretation']=[{'condition':'valuation','short_thesis':'SUPPORTS_SHORT','values':{'pe':50,'sector_pe':20}}]
    scored=quality(short,ReportEConfig(),r['config'])
    assert scored['components']['fundamentals']['features']['valuation']['score']==100
    assert short['company_research']['checks']==item['company_research']['checks']


def test_report_d_records_all_preserved_in_e3_and_never_ready():
    r=baseline();before=copy.deepcopy(r['report_d'])
    e=build_report_e(r)
    assert len(e['section_e3']['candidates'])==len(before['candidates'])
    for row in e['section_e3']['candidates']:
        original=next(d for d in before['candidates'] if (d['symbol'],d['side'])==(row['symbol'],row['side']))
        assert row['original_rejection_classification']==original['classification']
        assert row['original_rejection_reasons']==original['exact_rejection_reasons']
        assert row['label']=='REJECTED / RESEARCH ONLY' and row['timing']['entry_status']=='REJECT'
    assert r['report_d']==before


def test_missing_scores_zero_candidates_and_missing_history_render():
    r=baseline()
    for item in r['reviewed']:item['technical_score']=None
    e=build_report_e(r)
    assert not e['section_e1']['candidates'] and not e['section_e2']['candidates']
    assert 'No valid scored candidates' in render_report_e(e)
    for item in e['section_e3']['candidates']:assert item['historical']['target_first_percent'] is None
    assert len(e['unranked_missing_technical_records'])==len(r['reviewed'])


def realised_trades(count=30):
    # Synthetic paths exercise the existing real simulator and cost model.
    index=pd.date_range('2026-10-08 09:15',periods=75,freq='5min',tz='Asia/Kolkata')
    source=pd.DataFrame({'Open':100.,'High':100.05,'Low':99.95,'Close':100.,'Volume':100},index=index)
    engine=ReportEBacktester()
    trades=[]
    for n in range(count):
        f=source.copy(); outcome=n%3
        day=NOW.normalize()-pd.offsets.BDay(29-n)
        f.index=pd.date_range(day+pd.Timedelta(hours=9,minutes=15),periods=75,freq='5min')
        if outcome==0:f.loc[f.index[2],'High']=100.3
        elif outcome==1:f.loc[f.index[2],'Low']=99.8
        t=engine.simulate(f,1,'LONG','TEST',500)
        assert t
        t.pop('_exit_index');t['partition']='OUT_OF_SAMPLE';t['signal_timestamp']=f.index[0].isoformat()
        trades.append(t)
    return trades


@pytest.mark.parametrize('n',[0,1,29])
def test_tiny_samples_never_publish_probability(n):
    h=summarise_trades(realised_trades(n),30,spread_bps=1)
    assert h['status']=='UNKNOWN' and h['target_first_percent'] is None and h['net_expectancy'] is None
    assert h['sample_count']==n


def test_costs_once_signed_expectancy_confidence_interval_and_time_exit_break_even():
    trades=realised_trades();before=copy.deepcopy(trades)
    h=summarise_trades(trades,30,spread_bps=1)
    assert trades==before
    expected=sum(t['net_pnl']-(t['entry']+t['exit'])*t['quantity']/20000 for t in trades)/30
    costs=sum(t['total_costs']+(t['entry']+t['exit'])*t['quantity']/20000 for t in trades)
    assert h['net_expectancy']==pytest.approx(expected)
    assert h['total_transaction_cost_estimate']==pytest.approx(costs)
    assert h['target_first_count']==h['stop_first_count']==h['time_exit_count']==10
    assert h['net_stop_loss']<0 and h['average_net_time_exit_pnl']<0
    assert h['confidence_interval_95_percent'][0]<h['target_first_percent']<h['confidence_interval_95_percent'][1]
    assert h['cost_adjusted_expectancy_status']=='PROVISIONAL'
    p=h['break_even_target_first_percent']/100;q=1/3
    assert p*h['net_target_profit']+(1-q-p)*h['net_stop_loss']+q*h['average_net_time_exit_pnl']==pytest.approx(0)


def test_point_in_time_groups_and_legacy_policy_never_claim_timing_probability():
    cfg=FuturesScanConfig();ecfg=ReportEConfig()
    result={'movement_basis':'FUTURES','ambiguous_candle_policy':'STOP_FIRST','holdout_start':'2026-08-28','trades':realised_trades()}
    evidence=build_history_evidence(result,cfg,ecfg,instrument='TEST26OCTFUT')
    assert candidate_history(evidence,'LONG','TEST',30,'TEST26OCTFUT',asdict(cfg))['status']=='UNKNOWN'
    result['report_e_timing_validated']=True
    evidence=build_history_evidence(result,cfg,ecfg,instrument='TEST26OCTFUT')
    assert candidate_history(evidence,'LONG','TEST',30,'TEST26OCTFUT',asdict(cfg))['sample_count']==30
    assert candidate_history(evidence,'SHORT','TEST',30,'TEST26OCTFUT',asdict(cfg))['target_first_percent'] is None
    assert candidate_history(evidence,'LONG','TEST',30,'OTHER',asdict(cfg))['target_first_percent'] is None
    result['holdout_start']='2026-10-09'
    assert not build_history_evidence(result,cfg,ecfg,instrument='TEST26OCTFUT')['groups']
    result['movement_basis']='UNDERLYING'
    assert build_history_evidence(result,cfg,ecfg)['status']=='UNKNOWN'


def test_history_preparation_cache_invalidates_only_relevant_inputs_and_live_never_runs_it(tmp_path):
    scanner,provider=fixture(['SBIN']);runtime,cache=seed(scanner,tmp_path)
    manager=PreparedScanner(scanner,runtime);manager.now=NOW;manager.clock=lambda:NOW
    contract={k:provider.master[0][k] for k in ('tradingsymbol','expiry','lot_size')}
    bars=provider.intraday.loc[provider.intraday.index+pd.Timedelta(minutes=5)<=NOW]
    result={'movement_basis':'FUTURES','ambiguous_candle_policy':'STOP_FIRST','trades':[]}
    with patch('src.futures.report_e_history.ReportEBacktester.run',return_value=result) as run:
        manager.prepare_report_e_history(contract,bars,provider.daily,provider.daily)
        manager.prepare_report_e_history(contract,bars,provider.daily,provider.daily)
        assert run.call_count==1
        altered=bars.copy();altered.iloc[-1,altered.columns.get_loc('Close')]+=.01
        manager.prepare_report_e_history(contract,altered,provider.daily,provider.daily)
        assert run.call_count==2
    with patch('src.futures.report_e_history.ReportEBacktester.run',side_effect=AssertionError('Live backtest forbidden')):
        r=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
    assert 'report_e' in r
    assert not provider.provider.kite.place_order.called


def test_report_e_history_policy_changes_for_costs_contract_or_strategy():
    cfg=FuturesScanConfig();cost=FuturesCosts();e=ReportEConfig();contract={'tradingsymbol':'TEST26OCTFUT','lot_size':500}
    key=policy_key(cfg,cost,e,contract)
    assert key!=policy_key(replace(cfg,minimum_score=61),cost,e,contract)
    assert key!=policy_key(cfg,replace(cost,slippage_bps=3),e,contract)
    assert key!=policy_key(cfg,cost,replace(e,historical_spread_bps=1),contract)
    assert key!=policy_key(cfg,cost,e,{**contract,'tradingsymbol':'TEST26NOVFUT'})


def test_cli_api_streamlit_and_scheduled_output_backward_compatible(capsys,tmp_path):
    import main
    from src import api
    r=append_report_e(baseline());fake=Mock();fake.scan_futures_opportunities.return_value=r
    with patch.object(main,'TradingPlatform',return_value=fake),patch('sys.argv',['main.py','futures-scan']):main.main()
    output=capsys.readouterr().out
    assert output.index('ADDITIONAL ANALYSIS')<output.index(TITLE) and output.count(TITLE)==1
    with patch.object(api,'platform',fake):assert api.futures_opportunities()==r
    assert list(r)[-1]=='report_e'
    tree=ast.parse(Path('ui_app.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='futures_opportunities_page')
    context=Mock();context.__enter__=Mock(return_value=context);context.__exit__=Mock(return_value=False)
    st=Mock();st.session_state={'futures_scan_report':r};st.number_input.return_value=5;st.selectbox.return_value='LIVE_SCAN';st.button.return_value=False
    st.tabs.return_value=[context]*3;st.expander.return_value=context;jobs=Mock();jobs.future.return_value=None
    env={'st':st,'TradingPlatform':Mock,'daily_report_jobs':lambda:jobs,'feature_is_running':lambda _:False,'data_age':lambda _:'fresh',
         'FuturesOpportunitiesPresenter':FuturesOpportunitiesPresenter,'json':json}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'ui_app.py','exec'),env);env['futures_opportunities_page'](Mock())
    assert st.markdown.call_args_list[-2].args[0].startswith('## ADDITIONAL ANALYSIS')
    assert st.markdown.call_args_list[-1].args[0].startswith('## '+TITLE)


def test_report_e_historical_unknown_never_blocks_existing_reports(tmp_path,monkeypatch):
    from src.application.platform import TradingPlatform
    r=baseline();before=copy.deepcopy(r);fake=Mock();fake.scan.return_value=r
    monkeypatch.setenv('FUTURES_REPORT_DIRECTORY',str(tmp_path))
    platform=object.__new__(TradingPlatform)
    with patch('src.futures.scanner.FuturesOpportunityScanner',return_value=fake):result=platform.scan_futures_opportunities()
    for key in ('report_a','report_b','report_c','report_d'):assert result[key]==before[key] or key=='report_d' and {k:v for k,v in result[key].items() if k!='saved_files'}==before[key]
    assert result['report_e']['summary']['historically_sufficient_candidates']==0
    assert Path(result['report_e']['saved_files']['json']).exists()


def test_cached_historical_period_accepts_append_but_rejects_revisions():
    from src.futures.report_e_history import input_windows,inputs_match
    from src.futures.cache import candle_fingerprint
    scanner,provider=fixture(['SBIN'])
    f=provider.intraday.iloc[:100]
    evidence={'input_windows':input_windows({'candles':f}),'input_fingerprints':{'candles':candle_fingerprint(f)}}
    assert inputs_match(evidence,{'candles':provider.intraday})
    revised=provider.intraday.copy();revised.iloc[50,revised.columns.get_loc('Close')]+=.01
    assert not inputs_match(evidence,{'candles':revised})


def test_duplicate_or_wrong_partition_trades_do_not_inflate_samples():
    trades=realised_trades()
    r={'movement_basis':'FUTURES','ambiguous_candle_policy':'STOP_FIRST','holdout_start':'2026-08-28','trades':trades+trades,'report_e_timing_validated':True}
    e=build_history_evidence(r,FuturesScanConfig(),ReportEConfig(),instrument='TEST26OCTFUT')
    assert e['groups']['LONG:TEST:OUT_OF_SAMPLE']['sample_count']==30


def test_historical_prefix_uses_matched_volume_atr_and_completed_trigger():
    scanner,provider=fixture(['SBIN'])
    prefix=provider.intraday.loc[provider.intraday.index+pd.Timedelta(minutes=5)<=NOW].copy()
    for col,value in [('Open',100.),('High',100.1),('Low',99.9),('Close',100.),('RSI',59.),('ADX',35.),('RVOL',2.),('MACD_HISTOGRAM',.2),('EMA9',100.1),('EMA21',100.),('ATR',.1)]:prefix[col]=value
    prefix.loc[prefix.index[-1],['Open','High','Low','Close','RSI']]=[100.1,100.3,100.1,100.2,60.]
    setup={'technical_score':90,'timing':'READY','confirmed':True,'setup_type':'RESISTANCE_BREAKOUT','evidence':{}}
    engine=ReportEBacktester()
    with patch('src.futures.backtest.directional_setup',return_value=setup):
        signals=engine._signals(prefix,daily_atr=.8)
        assert [s['side'] for s in signals]==['LONG']
        assert engine._signals(prefix,daily_atr=None)==[]
        bad=prefix.copy();bad.loc[bad.index.date==NOW.date(),'Volume']=1
        assert engine._signals(bad,daily_atr=.8)==[]
        gap=prefix.drop(prefix.index[-3])
        assert engine._signals(gap,daily_atr=.8)==[]


def test_report_e_backtesting_no_lookahead_next_bar_and_stop_first():
    engine=ReportEBacktester()
    index=pd.date_range('2026-10-08 09:15',periods=75,freq='5min',tz='Asia/Kolkata')
    f=pd.DataFrame({'Open':100.,'High':100.05,'Low':99.95,'Close':100.,'Volume':100},index=index)
    f.loc[index[1],['High','Low']]=[101,99]
    assert engine.simulate(f,1,'LONG','TEST',500)['outcome']=='STOP_FIRST'
    assert engine.simulate(f,1,'SHORT','TEST',500)['outcome']=='STOP_FIRST'
    frames=[]
    for day in pd.date_range('2026-10-01',periods=8,freq='B'):
        part=f.copy();part.index=pd.date_range(day+pd.Timedelta(hours=9,minutes=15),periods=75,freq='5min',tz='Asia/Kolkata');frames.append(part)
    candles=pd.concat(frames);seen=[]
    def factory(prefix):
        seen.append(prefix.index[-1])
        return [{'side':'LONG','setup_type':'TEST','confirmation_timestamp':prefix.index[-1]}] if prefix.index[-1].hour==10 else []
    before=engine.run(candles,500,signal_factory=factory)
    changed=candles.copy();day=changed.index[-1].date();changed.loc[changed.index.date==day,['Open','High','Low','Close']]*=2
    after=engine.run(changed,500,signal_factory=factory)
    historical=lambda result:[t for t in result['trades'] if pd.Timestamp(t['entry_timestamp']).date()<day]
    assert historical(before)==historical(after)
    assert seen
    assert all(pd.Timestamp(t['entry_timestamp'])==pd.Timestamp(t['signal_timestamp'])+pd.Timedelta(minutes=5) for t in before['trades'])
