"""Additive workspace tables in the existing UI SQLite database.

A single writer lease protects rotation, rollback, management and daily snapshots
across processes. SQLite transactions publish membership and version atomically.
"""
from contextlib import contextmanager, closing
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import sqlite3
import uuid
import math
from numbers import Real

LISTS=('SHORTING_STOCKS','RECOVERING_STOCKS')

def timestamp():
    return datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()

def dumps(value):
    def normalize(v):
        if isinstance(v,dict):
            return {str(k):normalize(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):
            return [normalize(x) for x in v]
        if isinstance(v,Real) and not math.isfinite(v):
            return None
        return v
    return json.dumps(normalize(value),default=str,allow_nan=False)

class WorkspaceStore:
    def __init__(self,path='data/ui/stock_analyzer.db'):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS ft_securities(symbol TEXT PRIMARY KEY, security_id TEXT NOT NULL UNIQUE, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ft_memberships(symbol TEXT PRIMARY KEY REFERENCES ft_securities(symbol), category TEXT NOT NULL CHECK(category IN ('SHORTING_STOCKS','RECOVERING_STOCKS')), enabled INTEGER NOT NULL DEFAULT 1, pinned INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, added_at TEXT NOT NULL, evaluated_at TEXT NOT NULL, evidence TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ft_versions(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, kind TEXT NOT NULL, previous_version INTEGER, snapshot TEXT NOT NULL, report TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ft_jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, job_key TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, result TEXT, error TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS ft_job_success_key ON ft_jobs(kind,job_key) WHERE status='COMPLETED' AND job_key IS NOT NULL;
            CREATE TABLE IF NOT EXISTS ft_lease(name TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES ft_jobs(id));
            CREATE TABLE IF NOT EXISTS ft_evidence(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES ft_jobs(id), symbol TEXT NOT NULL, kind TEXT NOT NULL, retrieved_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ft_settings(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ft_evidence_symbol ON ft_evidence(symbol,id);
            ''')
            c.commit()

    def connect(self):
        c=sqlite3.connect(self.path,timeout=30)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA journal_mode=WAL')
        return c

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def members(self,active=False,c=None):
        if c is None:
            with closing(self.connect()) as conn:
                return self.members(active,conn)
        rows=c.execute('SELECT * FROM ft_memberships'+(" WHERE enabled=1 AND status='ACTIVE'" if active else '')+' ORDER BY category,symbol').fetchall()
        return [{**dict(r),'evidence':json.loads(r['evidence'])} for r in rows]

    def versions(self):
        with closing(self.connect()) as c:
            return [{**dict(r),'snapshot':json.loads(r['snapshot']),'report':json.loads(r['report'])} for r in c.execute('SELECT * FROM ft_versions ORDER BY id DESC')]

    def jobs(self,kind=None):
        with closing(self.connect()) as c:
            rows=c.execute('SELECT * FROM ft_jobs'+(' WHERE kind=?' if kind else '')+' ORDER BY started_at DESC', (kind,) if kind else ()).fetchall()
            return [{**dict(r),'result':json.loads(r['result']) if r['result'] else None} for r in rows]

    def settings(self):
        with closing(self.connect()) as c:
            row=c.execute('SELECT payload FROM ft_settings WHERE id=1').fetchone()
            return json.loads(row[0]) if row else {}

    def locked_job(self):
        """Saved lease owner; RUNNING is a persisted status, not a liveness check."""
        with closing(self.connect()) as c:
            row=c.execute("SELECT j.id,j.kind,j.status,j.started_at FROM ft_lease l JOIN ft_jobs j ON j.id=l.owner WHERE l.name='workspace'").fetchone()
            return dict(row) if row else None

    def save_settings(self,payload):
        with self.transaction() as c:
            c.execute('INSERT OR REPLACE INTO ft_settings VALUES(1,?)',(dumps(payload),))

    @contextmanager
    def job(self,kind,key=None):
        job_id=uuid.uuid4().hex
        with self.transaction() as c:
            if c.execute("SELECT 1 FROM ft_jobs WHERE kind=? AND job_key=? AND status='COMPLETED'",(kind,key)).fetchone():
                raise ValueError('Job already completed for this schedule key')
            if c.execute("SELECT 1 FROM ft_lease WHERE name='workspace'").fetchone():
                raise RuntimeError('Another workspace job is running; inspect persisted job status')
            c.execute('INSERT INTO ft_jobs(id,kind,job_key,status,started_at) VALUES(?,?,?,?,?)',(job_id,kind,key,'RUNNING',timestamp()))
            c.execute("INSERT INTO ft_lease VALUES('workspace',?)",(job_id,))
        output={}
        try:
            yield job_id,output
        except BaseException as exc:
            with self.transaction() as c:
                c.execute("UPDATE ft_jobs SET status='FAILED',completed_at=?,error=? WHERE id=?",(timestamp(),str(exc),job_id))
                c.execute('DELETE FROM ft_lease WHERE owner=?',(job_id,))
            raise
        else:
            with self.transaction() as c:
                status='INCOMPLETE' if output.get('status')=='INCOMPLETE' else 'COMPLETED'
                c.execute('UPDATE ft_jobs SET status=?,completed_at=?,result=? WHERE id=?',(status,timestamp(),dumps(output),job_id))
                c.execute('DELETE FROM ft_lease WHERE owner=?',(job_id,))

    def record(self,job_id,symbol,kind,payload):
        with self.transaction() as c:
            c.execute('INSERT INTO ft_evidence(job_id,symbol,kind,retrieved_at,payload) VALUES(?,?,?,?,?)',(job_id,symbol,kind,timestamp(),dumps(payload)))

    def evidence(self,symbol=None):
        with closing(self.connect()) as c:
            rows=c.execute('SELECT * FROM ft_evidence'+(' WHERE symbol=?' if symbol else '')+' ORDER BY id DESC',(symbol,) if symbol else ()).fetchall()
            return [{**dict(r),'payload':json.loads(r['payload'])} for r in rows]

    def publish(self,c,members,kind,report):
        previous=c.execute('SELECT MAX(id) FROM ft_versions').fetchone()[0]
        c.execute('DELETE FROM ft_memberships')
        for m in members:
            c.execute('INSERT INTO ft_securities VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET metadata=excluded.metadata',(m['symbol'],'NSE:'+m['symbol'],dumps(m['evidence'].get('contract',{}))))
            c.execute('INSERT INTO ft_memberships VALUES(?,?,?,?,?,?,?,?)',(m['symbol'],m['category'],int(m['enabled']),int(m['pinned']),m['status'],m['added_at'],m['evaluated_at'],dumps(m['evidence'])))
        cur=c.execute('INSERT INTO ft_versions(created_at,kind,previous_version,snapshot,report) VALUES(?,?,?,?,?)',(timestamp(),kind,previous,dumps(members),dumps(report)))
        version=cur.lastrowid
        report.update({'previous_version':previous,'new_version':version})
        c.execute('UPDATE ft_versions SET report=? WHERE id=?',(dumps(report),version))
        return version

    def rollback(self,version):
        with self.job('ROLLBACK') as (_,output):
            with self.transaction() as c:
                row=c.execute('SELECT snapshot FROM ft_versions WHERE id=?',(version,)).fetchone()
                if not row:
                    raise ValueError('Watchlist version not found')
                members=json.loads(row[0])
                # Restored membership is research only until a new validation.
                for m in members:
                    m['status']='REVIEW_REQUIRED'
                output['version']=self.publish(c,members,'ROLLBACK',{'restored_from':version,'requires_recheck':True})
        return output

    def manage(self,symbol,action,category=None,evidence=None):
        symbol=symbol.strip().upper()
        if category is not None and category not in LISTS:
            raise ValueError('Unknown watchlist')
        with self.job('MANAGE') as (_,output):
            with self.transaction() as c:
                members={m['symbol']:m for m in self.members(c=c)}
                if action=='add':
                    if category is None or evidence is None:
                        raise ValueError('Validated contract evidence is required')
                    if symbol in members:
                        raise ValueError('Security already has a membership; remove it before transferring manually')
                    members[symbol]={'symbol':symbol,'category':category,'enabled':True,'pinned':False,'status':'REVIEW_REQUIRED','added_at':timestamp(),'evaluated_at':timestamp(),'evidence':evidence}
                elif symbol not in members:
                    raise ValueError('Membership not found')
                elif action=='remove':
                    del members[symbol]
                elif action in ('enable','disable','pin','unpin'):
                    m=members[symbol]
                    m['enabled' if action in ('enable','disable') else 'pinned']=action in ('enable','pin')
                else:
                    raise ValueError('Unknown membership action')
                output['version']=self.publish(c,list(members.values()),'MANUAL',{'symbol':symbol,'action':action})
        return output

    def recover_job(self,job_id,*,worker_stopped=False):
        """Explicit operator recovery after stopping a crashed/abandoned worker."""
        if not worker_stopped:
            raise ValueError('Stop the original worker before recovering its lease')
        with self.transaction() as c:
            row=c.execute("SELECT status FROM ft_jobs WHERE id=?",(job_id,)).fetchone()
            if not row or row['status']!='RUNNING':
                raise ValueError('Running job not found')
            c.execute("UPDATE ft_jobs SET status='FAILED',completed_at=?,error='Operator recovered stopped worker' WHERE id=?",(timestamp(),job_id))
            c.execute('DELETE FROM ft_lease WHERE owner=?',(job_id,))
        return {'status':'RECOVERED','job_id':job_id,'watchlist_versions_preserved':True}
