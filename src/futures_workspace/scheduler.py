"""Persistent schedule keys with explicit process lifecycle, Asia/Kolkata."""
from datetime import timedelta
from src.futures_workspace.service import FuturesWorkspace

class WorkspaceScheduler:
    def __init__(self,workspace):
        self.workspace=workspace

    def tick(self,now=None):
        w=self.workspace; now=w.now(now)
        w.check_enabled()
        # Universe metadata refresh has its own schedule, never triggered by daily scan.
        key='UNIVERSE:'+now.date().isoformat()
        jobs=w.store.jobs('UNIVERSE_REFRESH')
        if not any(j['job_key']==key and j['status']=='COMPLETED' for j in jobs):
            # Refresh records through a separate idempotent job.
            with w.store.job('UNIVERSE_REFRESH',key) as (job_id,output):
                from src.futures_workspace.discovery import universe
                w.adapter.begin(now)
                try:
                    contracts=universe(w.adapter.instruments(),now)
                    if not contracts:
                        raise ValueError('Eligible universe unavailable')
                    output.update({'contracts':contracts,'eligible_universe_size':len(contracts),'as_of':now.isoformat()})
                    w.store.record(job_id,'*','ELIGIBILITY',output)
                finally:
                    w.adapter.end()
        cfg=w.config
        start=now.normalize()-timedelta(days=now.weekday())
        due=start+timedelta(days=cfg.weekly_day,hours=cfg.weekly_hour,minutes=cfg.weekly_minute)
        # Catch up the latest due cycle, including first launch with empty lists.
        if now<due:
            due-=timedelta(days=7)
        key='WEEKLY:'+due.date().isoformat()
        if any(j['job_key']==key and j['status']=='COMPLETED' for j in w.store.jobs('WEEKLY_ROTATION')):
            return {'status':'ALREADY_COMPLETED','key':key}
        return w.rotate(now,key=key)
