"""Adaptive bounded batches with additive evidence, retries and multiplicative growth."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import sleep
from threading import Lock


def transient(exc):
    from requests.exceptions import Timeout, ConnectionError
    from kiteconnect.exceptions import NetworkException
    status=getattr(getattr(exc,'response',None),'status_code',None) or getattr(exc,'code',None)
    return (isinstance(exc,(TimeoutError,Timeout,ConnectionError,NetworkException))
            or status in (429,502,503,504)
            or any(word in str(exc).lower() for word in ('rate limit','too many requests','429','timed out','timeout')))


class AdaptiveRunner:
    def __init__(self,maximum=32,healthy_batches=3,retries=2,backoff=1):
        self.maximum=maximum; self.healthy_batches=healthy_batches
        self.retries=retries; self.backoff=backoff
        self.workers=min(2,maximum); self.events=[]
        self._levels=[self.workers]
        self._lock=Lock()
        self._progress=None; self._completed=0; self._total=0

    def stage(self,label):
        with self._lock:
            if self._progress:
                self._progress(self._completed,self._total,label)

    def run(self,items,operation,fallback,progress=None):
        pending=list(items); results={}; attempts={}; healthy=0
        self.workers=min(2,self.maximum); self._levels=[self.workers]
        self._progress=progress; self._completed=0; self._total=len(items)
        with ThreadPoolExecutor(max_workers=self.maximum,thread_name_prefix='weekly-research') as pool:
            while pending:
                batch=pending[:self.workers]; del pending[:len(batch)]
                futures={pool.submit(operation,item):item for item in batch}
                failures=[]; pressured=False; batch_failed=False
                for future in as_completed(futures):
                    item=futures[future]
                    try:
                        results[item]=future.result()
                    except Exception as exc:
                        batch_failed=True
                        retryable=transient(exc)
                        pressured=pressured or retryable
                        attempts[item]=attempts.get(item,0)+1
                        if retryable and attempts[item]<=self.retries:
                            failures.append(item)
                        else:
                            results[item]=fallback(item,exc)
                        self.events.append({'action':'REQUEST_FAILED','item':item,'retryable':retryable,'error':type(exc).__name__})
                    if progress:
                        with self._lock:
                            self._completed=len(results)
                            progress(len(results),len(items),f'{item}; {self.workers} workers')
                if pressured:
                    previous=self.workers
                    if len(self._levels)>1:
                        self._levels.pop()
                    self.workers=self._levels[-1]; healthy=0
                    self.events.append({'action':'BACK_OFF','from':previous,'to':self.workers})
                    sleep(self.backoff)
                elif batch_failed:
                    healthy=0
                elif any(isinstance(results.get(item),dict) and
                         (results[item].get('classification')=='UNKNOWN_DATA' or results[item].get('eligible_for_admission') is False)
                         for item in batch):
                    healthy=0
                elif len(batch)==self.workers:
                    healthy+=1
                    if healthy>=self.healthy_batches and self.workers<self.maximum:
                        previous=self.workers; self.workers=min(self.maximum,self.workers*2); healthy=0
                        self._levels.append(self.workers)
                        self.events.append({'action':'SCALE_UP','from':previous,'to':self.workers})
                pending=failures+pending
        return {item:results[item] for item in items}
