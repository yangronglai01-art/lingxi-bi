"""Canonical asynchronous API. No demo identity or synchronous query bypass."""
from pathlib import Path
from fastapi import FastAPI,Depends,Header,HTTPException
from fastapi.responses import JSONResponse,Response,FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,ConfigDict,Field
from .identity import Identity,Denied
from .settings import Settings
from .store import Store,Conflict


class Submission(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question:str=Field(min_length=1,max_length=10000)
    previous_job_id:str|None=Field(default=None,min_length=1,max_length=64)


class Feedback(BaseModel):
    model_config=ConfigDict(extra='forbid')
    vote:str
    category:str
    comment:str=Field(default='',max_length=2000)


def public(row):
    return {key:row[key] for key in ('id','question','state','release','result','error_code','created','updated') if key in row}


def create_app(settings=None,store=None,identity=None):
    settings=settings or Settings.from_env();store=store or Store(settings.dsn);identity=identity or Identity(settings,store)
    if settings.environment in {'staging','production'}:store.assert_runtime_role()
    app=FastAPI(title='Lingxi Enterprise Analytics',version='1.0.0')
    def principal(authorization:str=Header(default='')):
        if not authorization.startswith('Bearer '):raise HTTPException(401,'Enterprise bearer token required')
        import secrets
        if settings.metrics_token and secrets.compare_digest(authorization[7:].encode(),settings.metrics_token.encode()):raise HTTPException(403,'Monitoring identity cannot access business resources')
        try:return identity.resolve(authorization[7:])
        except Denied:raise HTTPException(403,'Enterprise authorization denied') from None
    from .intent import Clarify
    @app.exception_handler(Clarify)
    async def clarification(_request,exc):
        return JSONResponse(status_code=422,content={'detail':str(exc),'clarification_reason':exc.reason,'clarification_details':exc.details})
    @app.exception_handler(Denied)
    async def denied(_request,_exc):return JSONResponse(status_code=403,content={'detail':'Resource unavailable or grant changed'})
    @app.exception_handler(Conflict)
    async def conflict(_request,_exc):return JSONResponse(status_code=409,content={'detail':'Query or release state conflict'})
    @app.exception_handler(Exception)
    async def unavailable(_request,_exc):return JSONResponse(status_code=503,content={'detail':'Analytics dependency unavailable'})
    @app.get('/health/live')
    def live():return {'status':'alive'}
    @app.get('/health/ready')
    def ready():
        if not store.ready():raise HTTPException(503,'Migration required')
        published=store.release(settings.tenant)
        if settings.embedding_revision:
            with store.db() as c:
                c.execute('SELECT count(*) AS n FROM analytics_schema_vectors WHERE tenant=%s AND release=%s AND revision=%s',(settings.tenant,published['id'],settings.embedding_revision))
                if c.fetchone()['n']!=len(published['catalog']['tables']):raise HTTPException(503,'Published schema index is not ready')
        from .execution import Executor
        for scope in settings.scopes:
            with Executor(settings).connect(scope) as connection:
                with connection.cursor() as c:c.execute('SELECT 1');c.fetchone()
        return {'status':'ready'}
    prefix='/api/v1/analytics'
    @app.get(prefix+'/me')
    def me(p=Depends(principal)):
        return {'subject':p.subject,'role':p.role,'regions':p.regions,'department':p.department,'policy_version':p.policy_version,'managed':True}
    @app.post(prefix+'/queries',status_code=202)
    def submit(body:Submission,idempotency_key:str=Header(min_length=1,max_length=128),p=Depends(principal)):
        job=store.submit(p,body.question,idempotency_key,body.previous_job_id)
        return {'id':job,'status_url':prefix+'/queries/'+job}
    @app.get(prefix+'/queries')
    def history(p=Depends(principal)):return [public(r) for r in store.list(p)]
    @app.get(prefix+'/queries/{job}')
    def get(job:str,p=Depends(principal)):return public(store.get(p,job))
    @app.post(prefix+'/queries/{job}/cancel')
    def cancel(job:str,p=Depends(principal)):return public(store.cancel(p,job))
    @app.get(prefix+'/queries/{job}/result')
    def result(job:str,p=Depends(principal)):
        row=store.get(p,job)
        if row['state']!='completed':raise HTTPException(409,'Query is not completed')
        return row['result']
    @app.post(prefix+'/queries/{job}/feedback',status_code=201)
    def feedback(job:str,body:Feedback,p=Depends(principal)):
        from .features import feedback as record_feedback
        try:return record_feedback(store,p,job,body.vote,body.category,body.comment)
        except ValueError as exc:
            if isinstance(exc,Conflict):raise
            raise HTTPException(422,'Invalid feedback') from None
    @app.get(prefix+'/examples')
    def examples(p=Depends(principal)):
        from src.petkit.planning import examples
        from src.petkit.catalog import METRICS
        allowed={m['id'] for m in METRICS if m['id'] in settings.enabled_metrics and all(p.allowed(store.release(p.tenant)['catalog']['tables'][t]) for t in m['dependencies'])}
        return {'questions':[r['question'] for r in examples() if r['metric_id'] in allowed]}
    @app.get(prefix+'/metrics')
    def metrics(p=Depends(principal)):
        release=store.release(p.tenant)
        return {'metrics':[m for m in release['catalog']['metrics'] if m['id'] in settings.enabled_metrics and all(t in release['views'].get(p.scope,{}) and p.allowed(release['catalog']['tables'][t]) for t in m['dependencies'])]}
    @app.get(prefix+'/schema')
    def schema(q:str='',core_only:bool=False,offset:int=0,limit:int=50,p=Depends(principal)):
        r=store.release(p.tenant)
        tables=[t for n,t in r['catalog']['tables'].items() if n in r['views'].get(p.scope,{}) and p.allowed(t) and (not core_only or t['core']) and q.lower() in (n+' '+t['description']).lower()]
        return {'generation':r['id'],'version':r['catalog']['version'],'synthetic':r['catalog'].get('synthetic',False),'watermark':r['watermark'],'total':len(tables),'tables':tables[max(0,offset):max(0,offset)+min(100,max(1,limit))]}
    @app.get(prefix+'/history')
    def old_history(p=Depends(principal)):
        return {'items':[dict(id=r['id'],question=r['question'],created_at=r['created'],favorited=False,state=r['state'],sql=None,summary=None,columns=[],sample_rows=[],chart_type='table',insights=[],error=r['error_code'],mock_used=False) for r in store.list(p)]}
    @app.get('/internal/metrics')
    def telemetry(authorization:str=Header(default='')):
        import secrets
        from types import SimpleNamespace
        if settings.metrics_token:
            if not authorization.startswith('Bearer ') or not secrets.compare_digest(authorization[7:].encode(),settings.metrics_token.encode()):raise HTTPException(403,'Dedicated monitoring identity required')
            p=SimpleNamespace(tenant=settings.tenant)
        else:
            p=principal(authorization)
            if p.role!='analyst' or p.scope!='global':raise HTTPException(403,'Monitoring grant required')
        with store.db() as c:
            c.execute("SELECT state,count(*) AS n FROM analytics_jobs WHERE tenant=%s GROUP BY state",(p.tenant,))
            counts={r['state']:r['n'] for r in c.fetchall()}
        with store.db() as c:
            c.execute("SELECT COALESCE(MAX(EXTRACT(epoch FROM clock_timestamp()-created)),0) AS age FROM analytics_jobs WHERE tenant=%s AND state='queued'",(p.tenant,));queue_age=float(c.fetchone()['age'])
            c.execute("SELECT count(*) AS n FROM analytics_jobs WHERE tenant=%s AND state='interrupted' AND updated>clock_timestamp()-interval '10 minutes'",(p.tenant,));recent=c.fetchone()['n']
            c.execute('SELECT metric,bucket,observations,total FROM analytics_histograms WHERE tenant=%s',(p.tenant,));buckets=c.fetchall()
        lines=['lingxi_'+state+'_jobs '+str(counts.get(state,0)) for state in ('queued','running','completed','failed','cancelled','interrupted')]
        lines+=['lingxi_oldest_queued_seconds '+str(queue_age),'lingxi_recent_interrupted_jobs '+str(recent)]
        for metric in ('sql','model','retrieval'):
            key='lingxi_'+metric+'_successful_seconds'
            lines.append('# TYPE '+key+' histogram')
            records={r['bucket']:r for r in buckets if r['metric']==metric}
            for bound in ('.1','.5','1','5','15','30','60','+Inf'):
                bucket=str(float(bound)) if bound in {'.1','.5'} else bound
                lines.append(key+'_bucket{le="'+bucket+'"} '+str(records.get(bucket,{}).get('observations',0)))
            final=records.get('+Inf',{})
            lines.extend([key+'_sum '+str(final.get('total',0)),key+'_count '+str(final.get('observations',0))])
        from .features import STAGES
        with store.db() as c:
            c.execute('SELECT stage,outcome,bucket,observations,total FROM analytics_stage_observations WHERE tenant=%s',(p.tenant,));stage_rows=c.fetchall()
        lines.append('# TYPE lingxi_stage_seconds histogram')
        for row in stage_rows:
            if row['stage'] not in STAGES or row['outcome'] not in {'ok','error','clarification'} or row['bucket'] not in {'0.1','0.5','1','5','15','30','60','+Inf'}:continue
            labels='stage="'+row['stage']+'",outcome="'+row['outcome']+'"'
            lines.append('lingxi_stage_seconds_bucket{'+labels+',le="'+row['bucket']+'"} '+str(row['observations']))
            if row['bucket']=='+Inf':
                lines.extend(['lingxi_stage_seconds_count{'+labels+'} '+str(row['observations']),'lingxi_stage_seconds_sum{'+labels+'} '+str(row['total'])])
        published=store.release(p.tenant)
        enabled=[m for m in published['catalog']['metrics'] if m['id'] in settings.enabled_metrics]
        slas=[m.get('freshness_sla_seconds') for m in enabled]
        approved=bool(enabled) and all(type(v)==int and v>0 for v in slas) and all(m.get('approval_status')=='approved' for m in enabled)
        sla=min(slas) if approved else 86400
        lines.extend(['lingxi_watermark_sla_seconds '+str(sla),'lingxi_watermark_sla_business_approved '+str(int(approved))])
        from datetime import datetime,timezone
        valid=1;age=-1
        try:
            when=datetime.fromisoformat(store.release(p.tenant)['watermark']['event_time'].replace('Z','+00:00'))
            if when.tzinfo is None:raise ValueError()
            age=max(0,(datetime.now(timezone.utc)-when).total_seconds())
        except (KeyError,ValueError,AttributeError):valid=0
        lines+=['lingxi_watermark_valid '+str(valid),'lingxi_watermark_age_seconds '+str(age)]
        return Response('\n'.join(lines)+'\n',media_type='text/plain; version=0.0.4')
    @app.get('/config.js')
    def browser_config():
        import json
        import os
        config=dict(apiBase=prefix,oidcIssuer=settings.issuer,oidcClientId=os.environ.get('OIDC_BROWSER_CLIENT_ID','analytics-portal'),oidcRole='',oidcLoginHint='',oidcAuthorizationEndpoint=os.environ.get('OIDC_AUTHORIZATION_ENDPOINT'),oidcTokenEndpoint=os.environ.get('OIDC_TOKEN_ENDPOINT'))
        return Response('window.__LINGXI_CONFIG__='+json.dumps(config)+';',media_type='application/javascript',headers={'Cache-Control':'no-store'})
    assets=Path(__file__).resolve().parents[2]/'web/dist'
    if (assets/'assets').is_dir():app.mount('/assets',StaticFiles(directory=assets/'assets'),name='assets')
    @app.get('/')
    @app.get('/callback')
    def portal():
        if not (assets/'index.html').is_file():raise HTTPException(503,'Portal build required')
        return FileResponse(assets/'index.html',headers={'Cache-Control':'no-store'})
    return app
