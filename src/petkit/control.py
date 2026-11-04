"""Persistent simulated organization, token digests, job state and audit."""
import hashlib
import os
import json
import secrets
import time
import uuid
from .settings import DATA
from src.storage import db

USER_QUEUE_LIMIT=20
GLOBAL_QUEUE_LIMIT=200

class QueueFull(ValueError):
    """Admission backpressure, distinct from idempotency conflicts."""
    def __init__(self,scope):
        self.scope=scope
        super().__init__('待处理队列已达配额：'+scope)

# 提交路径串行化的顾问锁键。等价于旧 SQLite 的 BEGIN IMMEDIATE：让「幂等检查 + 配额
# 检查 + 插入」成为原子步骤，避免并发提交突破配额或竞争同一幂等键。
_SUBMIT_LOCK_KEY = 5150

def initialized():
    """控制面是否已初始化（users 表已建且至少开通一个账号）。"""
    try:
        with db() as c:
            c.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='users'")
            if c.fetchone() is None:
                return False
            c.execute('SELECT count(*) FROM users')
            return c.fetchone()[0] > 0
    except Exception:
        return False

def initialize():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS runtime(name TEXT PRIMARY KEY,value TEXT NOT NULL,updated DOUBLE PRECISION NOT NULL);
        CREATE TABLE IF NOT EXISTS users(subject TEXT PRIMARY KEY,department TEXT NOT NULL,role TEXT NOT NULL,
          regions TEXT NOT NULL,enabled INTEGER NOT NULL,version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS tokens(digest TEXT PRIMARY KEY,subject TEXT NOT NULL REFERENCES users(subject),created DOUBLE PRECISION NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id BIGSERIAL PRIMARY KEY,created DOUBLE PRECISION NOT NULL,subject TEXT NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,subject TEXT NOT NULL,policy_version INTEGER NOT NULL,
          generation TEXT NOT NULL,question TEXT NOT NULL,state TEXT NOT NULL,created DOUBLE PRECISION NOT NULL,updated DOUBLE PRECISION NOT NULL,
          idempotency TEXT NOT NULL,result TEXT,error TEXT NOT NULL DEFAULT '',lease DOUBLE PRECISION,owner TEXT,
          UNIQUE(subject,policy_version,idempotency));
        CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs(state,created);
        CREATE INDEX IF NOT EXISTS jobs_updated ON jobs(updated);
        CREATE OR REPLACE FUNCTION json_status(payload text) RETURNS text AS $$
        BEGIN
            RETURN payload::jsonb->>'status';
        EXCEPTION WHEN OTHERS THEN
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        ''')

def reset():
    """清空控制面（含历史与接入台账）并重建结构。仅供测试隔离使用。"""
    from src import config
    from src.storage import current_database
    database=current_database()
    if (config.PG_HOST not in {"localhost","127.0.0.1"} or not database.endswith("_qa")
        or os.getenv("LINGXI_TEST_ALLOW_RESET")!=database):
        raise RuntimeError("Destructive reset requires an explicitly approved local *_qa database")
    with db() as c:
        c.execute("DROP TABLE IF EXISTS jobs,audit,tokens,users,runtime,ingestion_attempts,ingestion_batches,ingestion_events,history CASCADE")
        c.execute("DROP FUNCTION IF EXISTS json_status(text)")
    initialize()

def audit(record,event='query'):
    with db() as c:
        c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',
            (time.time(),record.get('subject','system'),event,json.dumps(record,ensure_ascii=False)))

def _upsert_user(subject,department,role,regions=(),enabled=True):
    from .access import ROLES
    import re
    if role not in ROLES or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',subject): raise ValueError('无效账号或角色')
    regions=tuple(sorted(set(regions)))
    if not all(re.fullmatch(r'[A-Za-z0-9_-]{1,40}',r) for r in regions): raise ValueError('无效地区编码')
    from .retrieval import current_catalog
    from .settings import artifacts
    import duckdb
    folder,_=artifacts()
    with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as warehouse:
        known={r[0] for r in warehouse.execute('SELECT region_id FROM dim_region').fetchall()}
    if set(regions)-known: raise ValueError('未知地区编码')
    with db() as c:
        previous=c.execute('SELECT * FROM users WHERE subject=?',(subject,)).fetchone()
        values=(department,role,json.dumps(regions),int(enabled))
        if previous and tuple(previous[k] for k in ['department','role','regions','enabled'])==values: return
        version=previous['version']+1 if previous else 1
        c.execute('INSERT INTO users VALUES(?,?,?,?,?,?) ON CONFLICT(subject) DO UPDATE SET department=excluded.department,role=excluded.role,regions=excluded.regions,enabled=excluded.enabled,version=excluded.version',(subject,*values,version))
        c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',(time.time(),subject,'policy_changed',json.dumps(dict(version=version,department=department,role=role,regions=regions,enabled=enabled))))

def issue_token(subject):
    token=secrets.token_urlsafe(40)
    with db() as c:
        user=c.execute('SELECT enabled FROM users WHERE subject=?',(subject,)).fetchone()
        if not user or not user['enabled']: raise PermissionError('账号不存在或已禁用')
        c.execute('DELETE FROM tokens WHERE subject=?',(subject,))
        c.execute('INSERT INTO tokens VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),subject,time.time()))
        c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',(time.time(),subject,'token_rotated','{}'))
    return token

def principal_for(subject):
    from .access import Principal
    with db() as c: row=c.execute('SELECT * FROM users WHERE subject=?',(subject,)).fetchone()
    if not row or not row['enabled']: raise PermissionError('账号不存在或已禁用')
    return Principal(subject=row['subject'],role=row['role'],regions=tuple(json.loads(row['regions'])),
        department=row['department'],policy_version=row['version'],managed=True)

def authenticate(token):
    if not token: raise PermissionError('模拟落地实例必须登录')
    with db() as c: row=c.execute('SELECT subject FROM tokens WHERE digest=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row: raise PermissionError('无效凭据')
    return principal_for(row['subject'])

def verify(principal):
    if not principal.managed: raise PermissionError('必须使用持久身份库中的账号')
    current=principal_for(principal.subject)
    if current!=principal: raise PermissionError('授权已改变，请重新提交请求')
    return current

def _submit(principal,question,key=None):
    verify(principal)
    if not question.strip() or len(question)>500: raise ValueError('问题长度必须在1到500之间')
    key=key or uuid.uuid4().hex
    if not 1<=len(key)<=128: raise ValueError('幂等键长度必须在1到128之间')
    from .retrieval import current_catalog
    generation=current_catalog()['generation']; now=time.time()
    key=hashlib.sha256((generation+':'+key).encode()).hexdigest()
    with db() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)', (_SUBMIT_LOCK_KEY,))
        prior=c.execute('SELECT * FROM jobs WHERE subject=? AND policy_version=? AND idempotency=?',(principal.subject,principal.policy_version,key)).fetchone()
        if prior:
            if prior['question']!=question: raise ValueError('同一幂等键不能用于不同问题')
            return prior['id']
        count=c.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
        own=c.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running') AND subject=?",(principal.subject,)).fetchone()[0]
        if count>=GLOBAL_QUEUE_LIMIT: raise QueueFull('global')
        if own>=USER_QUEUE_LIMIT: raise QueueFull('employee')
        job_id=uuid.uuid4().hex
        c.execute('INSERT INTO jobs(id,subject,policy_version,generation,question,state,created,updated,idempotency) VALUES(?,?,?,?,?,?,?,?,?)',
            (job_id,principal.subject,principal.policy_version,generation,question,'queued',now,now,key))
        c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',(now,principal.subject,'job_submitted',json.dumps(dict(job_id=job_id,policy_version=principal.policy_version))))
    return job_id

def get_job(principal,job_id):
    verify(principal)
    with db() as c: row=c.execute('SELECT * FROM jobs WHERE id=? AND subject=? AND policy_version=?',(job_id,principal.subject,principal.policy_version)).fetchone()
    if not row: raise KeyError('任务不存在或不属于当前授权版本')
    result=dict(row); result.pop('owner',None); result.pop('idempotency',None)
    result['result']=json.loads(result['result']) if result['result'] else None
    return result

def cancel(principal,job_id):
    get_job(principal,job_id)
    with db() as c:
        c.execute("UPDATE jobs SET state='cancelled',result=NULL,updated=? WHERE id=? AND state IN ('queued','running')",(time.time(),job_id))
        c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',(time.time(),principal.subject,'job_cancel_requested',json.dumps(dict(job_id=job_id))))
    return get_job(principal,job_id)

def recover():
    with db() as c:
        c.execute("UPDATE jobs SET state='interrupted',error='工作进程中断，请重新提交',updated=? WHERE state='running' AND lease<?",(time.time(),time.time()))

def _claim(owner):
    now=time.time()
    with db() as c:
        row=c.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
        if not row: return None
        c.execute("UPDATE jobs SET state='running',owner=?,lease=?,updated=? WHERE id=?",(owner,now+60,now,row['id']))
        return dict(row)

def heartbeat(job_id,owner):
    tick(owner)
    with db() as c: c.execute("UPDATE jobs SET lease=? WHERE id=? AND owner=? AND state='running'",(time.time()+60,job_id,owner))

def finish(job_id,owner,result=None,error=''):
    with db() as c:
        c.execute("UPDATE jobs SET state=?,result=?,error=?,updated=?,lease=NULL WHERE id=? AND owner=? AND state='running'",
          ('failed' if error else 'completed',json.dumps(result,ensure_ascii=False) if result else None,error,time.time(),job_id,owner))

def tick(owner):
    with db() as c:
        c.execute("INSERT INTO runtime VALUES('worker',?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated=excluded.updated",(json.dumps(dict(owner=owner,pid=os.getpid(),session=os.getenv("PETKIT_LAUNCH_SESSION",""))),time.time()))

def _release_serialized(function,*args,**kwargs):
    from .releases import guard,reconcile_locked
    with guard(str(DATA)):
        reconcile_locked(DATA)
        return function(*args,**kwargs)

def submit(*args,**kwargs): return _release_serialized(_submit,*args,**kwargs)
def claim(*args,**kwargs): return _release_serialized(_claim,*args,**kwargs)
def upsert_user(*args,**kwargs): return _release_serialized(_upsert_user,*args,**kwargs)
