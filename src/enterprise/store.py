"""Authoritative grants, immutable releases, idempotent queries and leased work."""
from contextlib import contextmanager
import hashlib
import json
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from .identity import Principal,Denied
from .settings import name


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


DDL='''
CREATE TABLE IF NOT EXISTS analytics_schema(version INTEGER PRIMARY KEY);
INSERT INTO analytics_schema VALUES(1) ON CONFLICT DO NOTHING;
INSERT INTO analytics_schema VALUES(2) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS analytics_histograms(tenant TEXT NOT NULL,metric TEXT NOT NULL,bucket TEXT NOT NULL,observations BIGINT NOT NULL,total DOUBLE PRECISION NOT NULL,created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant,metric,bucket));
CREATE TABLE IF NOT EXISTS analytics_grants(tenant TEXT NOT NULL,subject TEXT NOT NULL,role TEXT NOT NULL,
 regions JSONB NOT NULL,scope TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),enabled BOOLEAN NOT NULL,
 PRIMARY KEY(tenant,subject));
CREATE TABLE IF NOT EXISTS analytics_releases(tenant TEXT NOT NULL,id TEXT NOT NULL,catalog JSONB NOT NULL,
 views JSONB NOT NULL,watermark JSONB NOT NULL,state TEXT NOT NULL,created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS analytics_active(tenant TEXT PRIMARY KEY,release TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analytics_jobs(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,subject TEXT NOT NULL,
 policy_version INTEGER NOT NULL,scope TEXT NOT NULL,release TEXT NOT NULL,question TEXT NOT NULL,
 idempotency TEXT NOT NULL,fingerprint TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',
 attempt INTEGER NOT NULL DEFAULT 0,lease_owner TEXT,lease_until TIMESTAMPTZ,connection_id BIGINT,
 result JSONB,error_code TEXT,created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),updated TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant,subject,idempotency));
CREATE INDEX IF NOT EXISTS analytics_jobs_due ON analytics_jobs(state,created);
CREATE TABLE IF NOT EXISTS analytics_events(id BIGSERIAL PRIMARY KEY,tenant TEXT NOT NULL,subject TEXT NOT NULL,
 resource TEXT NOT NULL,event TEXT NOT NULL,details JSONB NOT NULL,created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
'''


class Conflict(ValueError):
    pass


class Store:
    def __init__(self,dsn):self.dsn=dsn

    @contextmanager
    def db(self):
        connection=psycopg2.connect(self.dsn,connect_timeout=5,
            options='-c statement_timeout=10000 -c lock_timeout=5000',cursor_factory=RealDictCursor)
        try:
            with connection:
                with connection.cursor() as cursor:yield cursor
        finally:connection.close()

    def migrate(self):
        with self.db() as c:
            c.execute('SELECT pg_advisory_xact_lock(6310923)');c.execute(DDL)
            from .vectors import DDL as VECTOR_DDL
            c.execute(VECTOR_DDL)
            from .features import DDL as FEATURE_DDL
            c.execute(FEATURE_DDL)
            from .loader import DDL as LOADER_DDL
            c.execute(LOADER_DDL)

    def ready(self):
        with self.db() as c:
            c.execute('SELECT version FROM analytics_schema WHERE version=3')
            return bool(c.fetchone())

    def assert_runtime_role(self):
        with self.db() as c:
            c.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")
            if any(c.fetchone().values()):raise PermissionError('Dedicated DML runtime role required')
            c.execute("SELECT has_table_privilege(current_user,'analytics_grants','UPDATE') AS grants, has_table_privilege(current_user,'analytics_releases','INSERT') AS releases")
            if any(c.fetchone().values()):raise PermissionError('Runtime role cannot administer grants or releases')

    def principal(self,tenant,subject,*,cursor=None,lock=False):
        def load(c):
            if lock:c.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))',('grant:'+tenant+':'+subject,))
            c.execute('SELECT * FROM analytics_grants WHERE tenant=%s AND subject=%s',(tenant,subject))
            g=c.fetchone()
            if not g or not g['enabled']:raise Denied('Workforce grant disabled or missing')
            return Principal(subject,tenant,g['role'],tuple(g['regions']),g['scope'],g['version'])
        if cursor:return load(cursor)
        with self.db() as c:return load(c)

    def verify(self,principal,*,cursor=None):
        if self.principal(principal.tenant,principal.subject,cursor=cursor,lock=bool(cursor))!=principal:
            raise Denied('Workforce permissions changed')

    def grant(self,tenant,subject,role,regions,scope,enabled=True):
        from src.petkit.access import ROLES
        if role not in ROLES or (not regions and scope!='global') or (regions and scope=='global'):
            raise ValueError('Explicit global scope or regional scope required')
        name(scope)
        import re
        if not 1<=len(subject)<=128 or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',r) for r in regions):raise ValueError('Invalid workforce grant')
        with self.db() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('grant:'+tenant+':'+subject,))
            c.execute('''INSERT INTO analytics_grants VALUES(%s,%s,%s,%s,%s,1,%s)
              ON CONFLICT(tenant,subject) DO UPDATE SET role=excluded.role,regions=excluded.regions,
              scope=excluded.scope,enabled=excluded.enabled,version=analytics_grants.version+1''',
              (tenant,subject,role,Json(list(regions)),scope,enabled))
            self.event(c,tenant,subject,subject,'grant_changed',{'scope':scope,'enabled':enabled})

    def stage_release(self,tenant,release,catalog,views,watermark):
        name(release)
        for scope,mapping in views.items():
            name(scope)
            for logical,physical in mapping.items():name(logical);name(physical)
        with self.db() as c:
            c.execute('INSERT INTO analytics_releases(tenant,id,catalog,views,watermark,state) VALUES(%s,%s,%s,%s,%s,\'staged\')',
                (tenant,release,Json(catalog),Json(views),Json(watermark)))

    def publish(self,tenant,release,expected,approval):
        if not approval:raise ValueError('Release approval and reconciliation reference required')
        with self.db() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('analytics-release:'+tenant,))
            c.execute('SELECT release FROM analytics_active WHERE tenant=%s FOR UPDATE',(tenant,));old=c.fetchone()
            if (old['release'] if old else None)!=expected:raise Conflict('Active release changed')
            c.execute('SELECT state FROM analytics_releases WHERE tenant=%s AND id=%s FOR UPDATE',(tenant,release));row=c.fetchone()
            if not row or row['state'] not in {'staged','published'}:raise Conflict('Release is not publishable')
            c.execute('SELECT state FROM analytics_loads WHERE tenant=%s AND release=%s',(tenant,release));loaded=c.fetchone()
            if loaded and loaded['state']!='applied':raise Conflict('Snapshot load is not reconciled/applied')
            c.execute("UPDATE analytics_releases SET state='published' WHERE tenant=%s AND id=%s",(tenant,release))
            c.execute('INSERT INTO analytics_active VALUES(%s,%s) ON CONFLICT(tenant) DO UPDATE SET release=excluded.release',(tenant,release))
            self.event(c,tenant,'release_operator',release,'release_published',{'previous':expected,'approval':approval})

    def release(self,tenant,release=None):
        with self.db() as c:
            if not release:
                c.execute('SELECT release FROM analytics_active WHERE tenant=%s',(tenant,));row=c.fetchone()
                if not row:raise Conflict('No approved data release')
                release=row['release']
            c.execute("SELECT * FROM analytics_releases WHERE tenant=%s AND id=%s AND state='published'",(tenant,release))
            row=c.fetchone()
            if not row:raise Conflict('Release unavailable')
            return dict(row)

    @staticmethod
    def event(c,tenant,subject,resource,event,details):
        c.execute('INSERT INTO analytics_events(tenant,subject,resource,event,details) VALUES(%s,%s,%s,%s,%s)',
            (tenant,subject,resource,event,Json(details)))

    def submit(self,p,question,key,parent_job=None):
        if not question.strip() or len(question)>10000 or not key or len(key)>128:raise ValueError('Invalid query request')
        identity={'question':question,'policy_version':p.policy_version,'scope':p.scope}
        if parent_job:identity['parent_job']=parent_job
        fingerprint=digest(identity)
        with self.db() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('analytics-submit:'+p.tenant,))
            self.verify(p,cursor=c)
            c.execute('SELECT * FROM analytics_jobs WHERE tenant=%s AND subject=%s AND idempotency=%s',(p.tenant,p.subject,key));old=c.fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise Conflict('Idempotency key input mismatch')
                return old['id']
            c.execute("SELECT count(*) AS n FROM analytics_jobs WHERE tenant=%s AND state IN ('queued','running')",(p.tenant,))
            if c.fetchone()['n']>=200:raise Conflict('Global queue full')
            c.execute("SELECT count(*) AS n FROM analytics_jobs WHERE tenant=%s AND subject=%s AND state IN ('queued','running')",(p.tenant,p.subject))
            if c.fetchone()['n']>=20:raise Conflict('Employee queue full')
            c.execute('SELECT release FROM analytics_active WHERE tenant=%s',(p.tenant,));active=c.fetchone()
            if not active:raise Conflict('No approved data release')
            if parent_job:
                from .features import followup_question
                question=followup_question(self,c,p,parent_job,question,active['release'])
            job=uuid.uuid4().hex
            c.execute('''INSERT INTO analytics_jobs(id,tenant,subject,policy_version,scope,release,question,idempotency,fingerprint)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
              (job,p.tenant,p.subject,p.policy_version,p.scope,active['release'],question,key,fingerprint))
            self.event(c,p.tenant,p.subject,job,'query_submitted',{'release':active['release'],'parent_job':parent_job})
            return job

    def get(self,p,job):
        with self.db() as c:
            self.verify(p,cursor=c)
            c.execute('SELECT * FROM analytics_jobs WHERE id=%s AND tenant=%s AND subject=%s AND policy_version=%s',
                (job,p.tenant,p.subject,p.policy_version));row=c.fetchone()
            if not row:raise Denied('Query unavailable')
            return dict(row)

    def list(self,p,limit=50):
        with self.db() as c:
            self.verify(p,cursor=c)
            c.execute('''SELECT id,question,state,error_code,created,updated FROM analytics_jobs
              WHERE tenant=%s AND subject=%s AND policy_version=%s ORDER BY created DESC LIMIT %s''',
              (p.tenant,p.subject,p.policy_version,limit))
            return [dict(r) for r in c.fetchall()]

    def claim(self,owner,tenant=None):
        with self.db() as c:
            c.execute('SELECT pg_advisory_xact_lock(6310924)')
            c.execute("SELECT count(*) AS n FROM analytics_jobs WHERE state='running' AND lease_until>clock_timestamp()")
            if c.fetchone()['n']>=3:return None
            # Never replay a possibly billed model request or a running SQL after a crash.
            c.execute("UPDATE analytics_jobs SET state='interrupted',error_code='LEASE_EXPIRED',updated=clock_timestamp() WHERE state='running' AND lease_until<clock_timestamp()")
            c.execute("SELECT * FROM analytics_jobs WHERE state='queued' AND (%s IS NULL OR tenant=%s) ORDER BY created FOR UPDATE SKIP LOCKED LIMIT 1",(tenant,tenant));row=c.fetchone()
            if not row:return None
            c.execute("UPDATE analytics_jobs SET state='running',attempt=attempt+1,lease_owner=%s,lease_until=clock_timestamp()+interval '30 seconds',updated=clock_timestamp() WHERE id=%s RETURNING *",(owner,row['id']))
            return dict(c.fetchone())

    def heartbeat(self,job):
        with self.db() as c:
            c.execute("UPDATE analytics_jobs SET lease_until=clock_timestamp()+interval '30 seconds' WHERE id=%s AND state='running' AND lease_owner=%s AND attempt=%s AND lease_until>clock_timestamp()",(job['id'],job['lease_owner'],job['attempt']))
            return c.rowcount==1

    def executing(self,job,connection_id):
        with self.db() as c:
            c.execute("UPDATE analytics_jobs SET connection_id=%s WHERE id=%s AND state='running' AND lease_owner=%s AND attempt=%s AND lease_until>clock_timestamp()",(connection_id,job['id'],job['lease_owner'],job['attempt']))
            if c.rowcount!=1:raise Conflict('Query lease lost or cancelled')

    def running(self,job):
        with self.db() as c:
            c.execute("SELECT 1 FROM analytics_jobs WHERE id=%s AND state='running' AND lease_owner=%s AND attempt=%s AND lease_until>clock_timestamp()",(job['id'],job['lease_owner'],job['attempt']))
            return bool(c.fetchone())

    def finish(self,job,p,result=None,error=None):
        with self.db() as c:
            self.verify(p,cursor=c)
            c.execute("UPDATE analytics_jobs SET state=%s,result=%s,error_code=%s,connection_id=NULL,lease_until=NULL,updated=clock_timestamp() WHERE id=%s AND state='running' AND lease_owner=%s AND attempt=%s AND lease_until>clock_timestamp()",
                ('failed' if error else 'completed',Json(result) if result is not None else None,error,job['id'],job['lease_owner'],job['attempt']))
            if c.rowcount!=1:raise Conflict('Query lease lost or cancelled')
            if not error and result and result.get('status')=='ok':
                values={'sql':(result.get('result') or {}).get('elapsed_seconds'),'retrieval':result.get('retrieval_seconds')}
                if result.get('generation_mode')=='local-chat':values['model']=result.get('model_elapsed_seconds')
                import math
                for metric,value in values.items():
                    if value is None:continue
                    value=float(value)
                    if not math.isfinite(value) or value<0:raise ValueError('Invalid measured duration')
                    for bound in (.1,.5,1,5,15,30,60,float('inf')):
                        bucket='+Inf' if bound==float('inf') else str(bound)
                        c.execute('''INSERT INTO analytics_histograms(tenant,metric,bucket,observations,total) VALUES(%s,%s,%s,%s,%s)
                         ON CONFLICT(tenant,metric,bucket) DO UPDATE SET observations=analytics_histograms.observations+excluded.observations,total=analytics_histograms.total+excluded.total''',
                         (p.tenant,metric,bucket,int(value<=bound),value if bucket=='+Inf' else 0))
            self.event(c,p.tenant,p.subject,job['id'],'query_finished' ,{'status':'failed' if error else 'completed','error_code':error})

    def fail(self,job,error):
        with self.db() as c:
            c.execute("UPDATE analytics_jobs SET state='failed',result=NULL,error_code=%s,connection_id=NULL,lease_until=NULL,updated=clock_timestamp() WHERE id=%s AND state='running' AND lease_owner=%s AND attempt=%s AND lease_until>clock_timestamp()",(error,job['id'],job['lease_owner'],job['attempt']))
            if c.rowcount!=1:return
            self.event(c,job['tenant'],job['subject'],job['id'],'query_failed',{'error_code':error})

    def cancel(self,p,job):
        self.get(p,job)
        with self.db() as c:
            self.verify(p,cursor=c)
            c.execute("UPDATE analytics_jobs SET state='cancelled',result=NULL,updated=clock_timestamp() WHERE id=%s AND tenant=%s AND subject=%s AND policy_version=%s AND state IN ('queued','running')",(job,p.tenant,p.subject,p.policy_version))
            self.event(c,p.tenant,p.subject,job,'query_cancel_requested',{})
        return self.get(p,job)
