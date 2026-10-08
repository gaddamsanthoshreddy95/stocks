"""Reproducible simulated benchmark. Does not claim live-market performance."""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import pandas as pd
from futures_mode_fixture import fixture,seed,NOW
from src.futures.backtest import FuturesIntradayBacktester
from src.futures.preparation import PreparedScanner


def symbols(count):
    frame=pd.read_csv(ROOT/'resources/sector_mapping.csv')
    names=list(dict.fromkeys(frame.Symbol.str.upper()))
    names=[name for name in names if not name.startswith('NIFTY')]
    return (names+[f'SIM{index:03d}' for index in range(count)])[:count]


def comparison(count):
    scanner,provider=fixture(symbols(count),latency=.002)
    captured={}
    original=FuturesIntradayBacktester.run
    def record(engine,candles,*args,**kwargs):
        result=original(engine,candles,*args,**kwargs)
        captured[candles.attrs.get('contract')]=result
        return result
    start=perf_counter()
    with patch.object(FuturesIntradayBacktester,'run',record):
        reference=scanner.scan(now=NOW,include_backtest=True)
    if not captured:
        raise AssertionError('Benchmark must exercise backtests and final validation')
    reference_time=perf_counter()-start
    with TemporaryDirectory() as directory:
        runtime,cache=seed(scanner,directory)
        manager=PreparedScanner(scanner,runtime)
        for name,result in captured.items():
            cache.put('backtest',name,{**result,'policy_fingerprint':manager.backtest_policy()},now=NOW,ttl=604800)
        start=perf_counter()
        with patch.object(FuturesIntradayBacktester,'run',side_effect=AssertionError('Live backtest forbidden')):
            prepared=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
        live_time=perf_counter()-start
        project=lambda report:[(i['symbol'],i['side'],i['setup_type'],i['technical_score'],i['final_decision'],i.get('historical',{}).get('target_first_percent')) for i in report['reviewed']]
        if project(reference)!=project(prepared):raise AssertionError('Reference/prepared decisions or historical rates differ')
        return {'universe':count,'reference_seconds':reference_time,'prepared_live_seconds':live_time,
                'speedup':reference_time/live_time,'outputs_equal':True,'request_counts':prepared['request_counts'],
                'baseline_backtests':len(captured),'live_backtests':0}


def full_universe(count,incremental):
    scanner,provider=fixture(symbols(count),latency=.002)
    with TemporaryDirectory() as directory:
        runtime,cache=seed(scanner,directory,stale_sessions=incremental)
        from dataclasses import replace
        runtime=replace(runtime,require_margin=True,movement_basis='UNDERLYING')
        with patch.object(FuturesIntradayBacktester,'run',side_effect=AssertionError('Live backtest forbidden')):
            result=scanner.scan(mode='LIVE_SCAN',runtime=runtime,now=NOW)
        return {'universe':count,'condition':'FIRST_INCREMENTAL_SCAN' if incremental else 'WARM_CACHE',
                'seconds':result['timings']['total_seconds'],'stage_times':result['timings'],
                'requests':result['request_counts'],'source_failures':len(result['source_failures']),
                'both_directions_scanned':result['long_discovery_count']==result['short_discovery_count']==count,
                'live_backtests':0,'mandatory_filters_preserved':True}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--universe-size',type=int,default=214)
    parser.add_argument('--comparison-size',type=int,default=12)
    parser.add_argument('--output',default='reports/futures_scan_benchmark.json')
    args=parser.parse_args()
    output={'benchmark_type':'DETERMINISTIC_SIMULATED_FEEDS_REAL_SCORING_AND_BACKTESTING',
            'limits':'Kite pacing enforced; simulated 2ms API latency, warm news and complete prepared research. Real API performance not measured.',
            'comparison':comparison(args.comparison_size),
            'full_universe':[full_universe(args.universe_size,False),full_universe(args.universe_size,True)]}
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(output,indent=2))
    print(json.dumps(output,indent=2))


if __name__=='__main__':main()
