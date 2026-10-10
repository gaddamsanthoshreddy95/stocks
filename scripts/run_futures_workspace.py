"""Windows Task Scheduler/cron entry point; never executes broker orders."""
import argparse
import json
import sys
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('schedule','rotate','recheck','refresh-universe','daily','status','recover-job'))
    parser.add_argument('--database',default='data/ui/stock_analyzer.db')
    parser.add_argument('--job-id')
    parser.add_argument('--worker-stopped',action='store_true')
    args=parser.parse_args()
    from src.futures_workspace.store import WorkspaceStore
    store=WorkspaceStore(args.database)
    if args.command in ('status','recover-job'):
        saved=store.locked_job()
        job_id=args.job_id or (saved['id'] if saved else None)
        result=({'memberships':store.members(),'jobs':store.jobs(),'saved_lock':store.locked_job()}
                if args.command=='status' else store.recover_job(job_id,worker_stopped=args.worker_stopped)
                if job_id else {'status':'NO_LOCK','watchlist_versions_preserved':True})
        print(json.dumps(result,indent=2,default=str))
        return
    from src.application.platform import TradingPlatform
    from src.futures_workspace.service import FuturesWorkspace
    from src.futures_workspace.scheduler import WorkspaceScheduler
    workspace=FuturesWorkspace(TradingPlatform(),store)
    operations={'schedule':lambda:WorkspaceScheduler(workspace).tick(),'rotate':workspace.rotate,
        'recheck':lambda:workspace.rotate(selected_only=True),'refresh-universe':workspace.refresh_universe,
        'daily':workspace.scan_daily,'recover-job':lambda:workspace.store.recover_job(args.job_id,worker_stopped=args.worker_stopped),'status':lambda:{'memberships':workspace.store.members(),'jobs':workspace.store.jobs()}}
    print(json.dumps(operations[args.command](),indent=2,default=str))

if __name__=='__main__':
    main()
