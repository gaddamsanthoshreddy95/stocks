"""Cron-friendly report writer; no orders and no implicit scheduling installation."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.application.platform import TradingPlatform
from src.presenter.futures_opportunities import FuturesOpportunitiesPresenter
from src.futures.rejected_analysis import render_report_d

parser=argparse.ArgumentParser()
parser.add_argument('--mode',choices=['FULL_RESEARCH','DAILY_PREP','LIVE_SCAN','AFTER_MARKET_RESEARCH'],required=True)
parser.add_argument('--limit',type=int,default=5)
args=parser.parse_args()
report=TradingPlatform().scan_futures_opportunities(args.limit,mode=args.mode)
name=report['generated_at'].replace(':','-').replace('+','_')
folder=ROOT/'reports'/'prepared_scans';folder.mkdir(parents=True,exist_ok=True)
(folder/f'{args.mode}_{name}.json').write_text(json.dumps(report,indent=2,default=str))
(folder/f'{args.mode}_{name}.md').write_text(FuturesOpportunitiesPresenter.render(report))
print(json.dumps({'mode':args.mode,'seconds':report['timings']['total_seconds'],'approved':report['approved_count'],'report_directory':str(folder)}))
print(FuturesOpportunitiesPresenter.render(report))
# The complete presenter includes A–E and the manual exit warning in order.
# The compatible scheduled JSON summary precedes all report sections.
