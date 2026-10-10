"""Windows Task Scheduler/cron entry point; never executes broker orders."""
import argparse
import json
import sys
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
from src.application.platform import TradingPlatform
from src.futures_workspace.service import FuturesWorkspace
from src.futures_workspace.scheduler import WorkspaceScheduler

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('schedule','rotate','recheck','refresh-universe','daily','status','recover-job'))
    parser.add_argument('--database',default='data/ui/stock_analyzer.db')
    parser.add_argument('--job-id')
    parser.add_argument('--worker-stopped',action='store_true')
    args=parser.parse_args()
    from src.futures_workspace.store import WorkspaceStore
    workspace=FuturesWorkspace(TradingPlatform(),WorkspaceStore(args.database))
    operations={'schedule':lambda:WorkspaceScheduler(workspace).tick(),'rotate':workspace.rotate,
        'recheck':lambda:workspace.rotate(selected_only=True),'refresh-universe':workspace.refresh_universe,
        'daily':workspace.scan_daily,'recover-job':lambda:workspace.store.recover_job(args.job_id,worker_stopped=args.worker_stopped),'status':lambda:{'memberships':workspace.store.members(),'jobs':workspace.store.jobs()}}
    print(json.dumps(operations[args.command](),indent=2,default=str))

if __name__=='__main__':
    main()
