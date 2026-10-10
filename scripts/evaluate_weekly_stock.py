"""Manual, read-only history audit/evaluation. Never populates production watchlists."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pandas as pd
from src.futures_workspace.config import WorkspaceConfig
from src.futures_workspace.discovery import technical
from src.futures_workspace.validation import ohlcv_diagnostics
from src.futures.sessions import normalise_candles,ist
from src.futures_workspace.store import dumps,LISTS


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('symbol')
    parser.add_argument('--history',type=Path)
    parser.add_argument('--benchmark',type=Path)
    parser.add_argument('--sector',type=Path)
    parser.add_argument('--as-of')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    symbol=args.symbol.upper()
    path=args.history or ROOT/'.cache/futures_workspace_adjusted'/(hashlib.sha256(symbol.encode()).hexdigest()+'.parquet')
    now=ist(args.as_of or pd.Timestamp.now(tz='Asia/Kolkata'))
    history=pd.read_parquet(path)
    benchmark_path=args.benchmark or ROOT/'.cache/futures_workspace_adjusted'/(hashlib.sha256('^NSEI'.encode()).hexdigest()+'.parquet')
    benchmark=pd.read_parquet(benchmark_path) if benchmark_path.exists() else None
    sector=pd.read_parquet(args.sector) if args.sector else None
    try:
        completed=normalise_candles(history,'day',now)
        validation=ohlcv_diagnostics(completed)
    except (ValueError,TypeError) as exc:
        validation={'error_type':type(exc).__name__,'error':str(exc)}
    result=technical(history,WorkspaceConfig.from_env(),now,benchmark,sector)
    report={'symbol':symbol,'as_of':now.isoformat(),'history_path':str(path),'history_rows':len(history),
            'price_adjustment':history.attrs.get('price_adjustment','UNKNOWN'),'ohlcv_validation':validation,
            'evaluation':result,'directional_shortlist_candidate':result['classification'] in LISTS,
            'full_universe_shortlist_rank':'NOT_EVALUATED','production_watchlists_modified':False}
    encoded=dumps(report)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(encoded+'\n')
    print(encoded)

if __name__=='__main__':main()
