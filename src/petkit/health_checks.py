"""Shared fail-closed readiness evidence for probes and authorized diagnostics."""
import json,math,os,time
from . import control
from .settings import DATA,artifacts

def runtime_value(row):
    try:value=json.loads(row['value']) if row else {}
    except (ValueError,TypeError):return {}
    return value if isinstance(value,dict) else {}

def age(row,now):
    if not row:return None
    try:value=now-float(row['updated'])
    except (ValueError,TypeError):return None
    return round(value,3) if math.isfinite(value) and value>=0 else None

def evaluate(now=None,runtime=None,enabled_users=None):
    now=time.time() if now is None else now;checks={};generation=None
    journal=DATA/'release_journal.json'
    if journal.exists():
        try:checks['publication']=json.loads(journal.read_text(encoding='utf-8'))['state']!='prepared'
        except Exception:checks['publication']=False
    try:
        folder,manifest=artifacts();generation=manifest['generation']
        checks['warehouse']=(folder/'warehouse.duckdb').is_file() and (folder/'catalog.json').is_file()
    except Exception:checks['warehouse']=False
    if os.getenv('PETKIT_DEPLOYMENT')=='simulated':
        try:
            if runtime is None:
                with control.db() as c:
                    runtime={r['name']:dict(r) for r in c.execute("SELECT name,value,updated FROM runtime WHERE name IN ('worker','supervisor')")}
                    enabled_users=c.execute('SELECT COUNT(*) FROM users WHERE enabled=1').fetchone()[0]
            checks['identity_store']=bool(enabled_users)
            worker=runtime.get('worker');worker_age=age(worker,now)
            checks['worker']=worker_age is not None and worker_age<45
            session=os.getenv('PETKIT_LAUNCH_SESSION')
            if session:
                supervisor=runtime.get('supervisor');state=runtime_value(supervisor);pulse=runtime_value(worker);supervisor_age=age(supervisor,now)
                checks['supervisor']=bool(supervisor_age is not None and supervisor_age<15 and state.get('session')==session and state.get('phase')=='running')
                checks['worker']=bool(checks['worker'] and pulse.get('session')==session and pulse.get('pid')==state.get('worker_pid'))
        except Exception:checks.update(identity_store=False,worker=False)
    return dict(ready=all(checks.values()),checks=checks,generation=generation)
