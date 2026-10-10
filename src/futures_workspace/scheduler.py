"""Persistent schedule keys with explicit process lifecycle, Asia/Kolkata."""
from datetime import timedelta
from src.futures_workspace.service import FuturesWorkspace

class WorkspaceScheduler:
    def __init__(self,workspace):
        self.workspace=workspace

    def tick(self,now=None,progress=None):
        w=self.workspace; now=w.now(now)
        w.check_enabled()
        # Universe metadata refresh has its own schedule, never triggered by daily scan.
        key='UNIVERSE:'+now.date().isoformat()
        jobs=w.store.jobs('UNIVERSE_REFRESH')
        failed=next((j for j in jobs if j['job_key']==key and j['status'] in ('FAILED','INCOMPLETE')),None)
        if failed and not any(j['job_key']==key and j['status']=='COMPLETED' for j in jobs):
            return {'status':'AUTO_RETRY_PAUSED','job_id':failed['id'],'reason':failed.get('error') or 'Universe refresh incomplete; retry manually'}
        if not any(j['job_key']==key and j['status']=='COMPLETED' for j in jobs):
            if progress:
                progress(0,0,'Refreshing Futures instrument universe')
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
        for job in w.store.jobs('WEEKLY_ROTATION'):
            # A manual full rotation also satisfies this cycle. Do not launch a
            # second universe scan immediately after a user's completed attempt.
            same_cycle=job['job_key']==key or (job['job_key'] is None and due<=w.now(job['started_at'])<=now)
            if same_cycle and job['status'] in ('COMPLETED','INCOMPLETE','FAILED'):
                return {'status':'ALREADY_COMPLETED' if job['status']=='COMPLETED' else 'AUTO_RETRY_PAUSED',
                        'key':key,'job_id':job['id'],'previous_status':job['status'],
                        'reason':job.get('error') or (job.get('result') or {}).get('reason')}
        return w.rotate(now,key=key,progress=progress)
