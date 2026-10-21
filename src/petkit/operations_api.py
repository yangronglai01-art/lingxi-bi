"""Persistent job routes, registered on the existing API."""
import time
from fastapi import Depends,Header,HTTPException
from . import control

def register(app,identity,AskRequest):
    @app.get('/me')
    def me(principal=Depends(identity)):
        return dict(subject=principal.subject,role=principal.role,department=principal.department,
                    regions=principal.regions,policy_version=principal.policy_version,managed=principal.managed)

    def managed(principal):
        try: control.verify(principal)
        except PermissionError as exc: raise HTTPException(403,str(exc)) from exc

    @app.post('/jobs',status_code=202)
    def submit(req:AskRequest,idempotency_key:str|None=Header(default=None),principal=Depends(identity)):
        managed(principal)
        try: job_id=control.submit(principal,req.question,idempotency_key)
        except control.QueueFull as exc: raise HTTPException(429,dict(code='queue_full',scope=exc.scope,message=str(exc)),headers={'Retry-After':'5'}) from exc
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        return dict(id=job_id,status_url='/jobs/'+job_id)

    @app.get('/jobs')
    def list_jobs(limit:int=20,principal=Depends(identity)):
        managed(principal)
        if not 1<=limit<=100: raise HTTPException(422,'limit应在1到100之间')
        with control.db() as c:
            rows=c.execute('SELECT id,state,created,updated,question FROM jobs WHERE subject=? AND policy_version=? ORDER BY created DESC LIMIT ?',
                (principal.subject,principal.policy_version,limit)).fetchall()
        return dict(items=[dict(r) for r in rows])

    @app.get('/jobs/{job_id}')
    def get_job(job_id:str,principal=Depends(identity)):
        managed(principal)
        try: return control.get_job(principal,job_id)
        except KeyError as exc: raise HTTPException(404,'任务不存在或当前授权不可访问') from exc

    @app.post('/jobs/{job_id}/cancel')
    def cancel(job_id:str,principal=Depends(identity)):
        managed(principal)
        try: return control.cancel(principal,job_id)
        except KeyError as exc: raise HTTPException(404,'任务不存在或当前授权不可访问') from exc

    @app.get('/ready')
    def ready():
        import os
        from .health_checks import evaluate
        evidence=evaluate()
        if not evidence['ready']:raise HTTPException(503,evidence['checks'])
        result=dict(ready=True,checks=evidence['checks'])
        if os.getenv('PETKIT_LAUNCH_SESSION'):result['launch_session']=os.environ['PETKIT_LAUNCH_SESSION']
        return result

    @app.get('/ops/health')
    def health_snapshot(window_minutes:int=15,principal=Depends(identity)):
        import sqlite3
        from .diagnostics import snapshot
        try:return snapshot(principal,window_minutes)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        except sqlite3.Error as exc:raise HTTPException(503,'运维存储暂不可用') from exc

    @app.get('/ops/summary')
    def summary(principal=Depends(identity)):
        managed(principal)
        if principal.role!='analyst' or principal.regions: raise HTTPException(403,'仅全局分析账号可查看运维概况')
        with control.db() as c:
            states=c.execute('SELECT state,COUNT(*) AS count FROM jobs GROUP BY state').fetchall()
            audit=c.execute('SELECT id,created,subject,event,payload FROM audit ORDER BY id DESC LIMIT 50').fetchall()
        return dict(jobs={r['state']:r['count'] for r in states},audit=[dict(r) for r in audit])


    @app.get('/ops/ingestion')
    def ingestion_status(limit:int=20,principal=Depends(identity)):
        managed(principal)
        if principal.role!='analyst' or principal.regions: raise HTTPException(403,'仅全局分析账号可查看接入状态')
        if not 1<=limit<=100: raise HTTPException(422,'limit应在1到100之间')
        with control.db() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ingestion_attempts'").fetchone(): return dict(items=[])
            rows=c.execute('SELECT id,source,batch_id,parent,candidate,state,error,created,updated FROM ingestion_attempts ORDER BY created DESC LIMIT ?',(limit,)).fetchall()
        return dict(items=[dict(r) for r in rows])
