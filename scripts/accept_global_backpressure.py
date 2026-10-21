"""Global 200-job admission check on an isolated supervised instance; no LLM."""
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,secrets,sqlite3,sys,threading,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running
from scripts.backup_simulated import backup,restore_drill
from scripts.accept_durable_stability import free,job_fingerprint,sha
from src.petkit.settings import DATA
from src.petkit.reference import expected

def identity_fingerprint(data):
    with sqlite3.connect(data/'control.sqlite3') as c:
        values=[c.execute('SELECT * FROM users ORDER BY subject').fetchall(),c.execute('SELECT * FROM tokens ORDER BY digest').fetchall()]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()

def main():
    folder=ROOT/'reports/global_backpressure_runs'/uuid.uuid4().hex[:12];folder.mkdir(parents=True)
    generation=json.loads((DATA/'current.json').read_text(encoding='utf-8'))['generation']
    before=dict(jobs=job_fingerprint(DATA),identities=identity_fingerprint(DATA),pointer=sha(DATA/'current.json'),warehouse=sha(DATA/generation/'warehouse.duckdb'))
    report=dict(generation=generation,mode='demo',real_llm_called=False,completed=False,
        http_concurrency=32,offered_requests=320,test_only_identities=16,offers_per_test_identity=20,
        artificial_worker_pause=False,global_pending_limit=200,employee_pending_limit=20)
    def save():(folder/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    children=[];stop=threading.Event();sampler=None;samples=[];sample_errors=[]
    try:
        data=Path(restore_drill(backup())['restored_directory'])
        report['isolated_directory']=str(data)
        keys=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))
        # Explicit test fixture in the copied control store only. No secrets on disk.
        subjects=['load_global_'+str(i).zfill(2) for i in range(16)]
        with sqlite3.connect(data/'control.sqlite3') as c:
            for subject in subjects:
                token=secrets.token_urlsafe(40);keys[subject]=token
                c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(subject,'压测临时账号','analyst','[]',1,1))
                c.execute('INSERT INTO tokens VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),subject,time.time()))
            c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',
                (time.time(),'load-fixture','test_only_identity_fixture',json.dumps(dict(users=16))))
        save()
        import duckdb
        raw={}
        with duckdb.connect(str(data/generation/'warehouse.duckdb'),read_only=True) as c:
            for name in ['dim_product','dim_device','fact_order_item','fact_order','dim_sku','dim_channel','fact_refund']:
                result=c.execute('SELECT * FROM '+name);columns=[r[0] for r in result.description]
                raw[name]=[dict(zip(columns,row)) for row in result.fetchall()]
        reference=expected(dict(metric_id='net_sales',group='model',month=None),raw)
        def check(job):
            answer=job.get('result') or {};rows=(answer.get('result') or {}).get('rows',[])
            found={r[0]:r[1:] for r in rows}
            assert job['state']=='completed' and answer.get('status')=='success'
            assert len(rows)==len(found) and found.keys()==reference.keys()
            assert all(len(found[k])==len(v) and all(abs(float(x)-float(y))<=.011 for x,y in zip(found[k],v)) for k,v in reference.items())
        a,b=free(),free()
        while a==b:b=free()
        with running('demo',a,b,data_dir=data) as services:
            children=services['processes']
            def request(subject,path,body=None,key=None):
                headers={'Authorization':'Bearer '+keys[subject],'Content-Type':'application/json'}
                if key:headers['Idempotency-Key']=key
                req=Request(services['api_url']+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
                try:
                    with urlopen(req,timeout=20) as r:return json.load(r),r.status,{k.lower():v for k,v in r.headers.items()}
                except HTTPError as exc:return json.load(exc),exc.code,{k.lower():v for k,v in exc.headers.items()}
            def wait(subject,task):
                deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    value,status,_=request(subject,'/jobs/'+task);assert status==200
                    if value['state'] not in {'queued','running'}:return value
                    time.sleep(.2)
                raise TimeoutError('Accepted job did not finish within 120 seconds')
            value,status,_=request('global_analyst','/jobs',dict(question='各型号净销售额'),'warm-'+folder.name)
            assert status==202;check(wait('global_analyst',value['id']))
            with sqlite3.connect(data/'control.sqlite3') as c:baseline=c.execute('SELECT count(*) FROM jobs').fetchone()[0]
            def sample():
                while not stop.wait(.05):
                    try:
                        with sqlite3.connect(data/'control.sqlite3',timeout=10) as c:
                            counts=c.execute("SELECT subject,count(*) FROM jobs WHERE state IN ('queued','running') GROUP BY subject").fetchall()
                        samples.append(dict(pending=sum(r[1] for r in counts),max_employee=max((r[1] for r in counts),default=0)))
                    except Exception as exc:sample_errors.append(type(exc).__name__);stop.set()
            sampler=threading.Thread(target=sample,daemon=True);sampler.start()
            def submit(index):
                subject=subjects[index%16];key=f'{folder.name}-{index}'
                value,status,headers=request(subject,'/jobs',dict(question='各型号净销售额'),key)
                return dict(index=index,subject=subject,key=key,status=status,id=value.get('id'),
                    detail=value.get('detail'),retry_after=headers.get('retry-after'))
            start=time.monotonic()
            with ThreadPoolExecutor(max_workers=32) as pool:responses=list(pool.map(submit,range(320)))
            duration=time.monotonic()-start
            accepted=[r for r in responses if r['status']==202];rejected=[r for r in responses if r['status']==429]
            report.update(accepted=len(accepted),rejected_429=len(rejected),burst_seconds=round(duration,3),responses=responses)
            save()
            assert accepted and rejected and len(accepted)+len(rejected)==320
            assert all(r['retry_after']=='5' and r['detail']['scope']=='global' and r['detail']['code']=='queue_full' for r in rejected)
            assert len({r['id'] for r in accepted})==len(accepted)
            with sqlite3.connect(data/'control.sqlite3') as c:
                assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]-baseline==len(accepted)
                for r in rejected:
                    digest=hashlib.sha256((generation+':'+r['key']).encode()).hexdigest()
                    assert c.execute('SELECT count(*) FROM jobs WHERE subject=? AND idempotency=?',(r['subject'],digest)).fetchone()[0]==0
            report.update(all_429_global_scope=True,all_429_retry_after_seconds=5,rejected_jobs_not_persisted=True);save()
            # Cross-account read/cancel must remain unavailable during backlog.
            first=accepted[0]
            other=next(subject for subject in subjects if subject!=first['subject'])
            assert request(other,'/jobs/'+first['id'])[1]==404
            assert request(other,'/jobs/'+first['id']+'/cancel',{})[1]==404
            for index,r in enumerate(accepted,1):
                check(wait(r['subject'],r['id']))
                if index%50==0:
                    report['oracle_verified_so_far']=index;save()
                    print(json.dumps(dict(verified=index,total=len(accepted))),flush=True)
            retry=rejected[0]
            while time.monotonic()-start<5:stop.wait(.1)
            value,status,_=request(retry['subject'],'/jobs',dict(question='各型号净销售额'),retry['key'])
            assert status==202;check(wait(retry['subject'],value['id']))
            again,status,_=request(retry['subject'],'/jobs',dict(question='各型号净销售额'),retry['key'])
            assert status==202 and again['id']==value['id']
            value,status,_=request('global_analyst','/ops/health')
            assert status==200 and value['ready'] and value['queue']['pending']==0 and value['supervisor']['restarts']==0
            stop.set();sampler.join(timeout=15)
            assert samples and not sample_errors
            assert max(s['pending'] for s in samples)<=200 and max(s['max_employee'] for s in samples)<=20
            report.update(oracle_verified=len(accepted),cross_account_read_and_cancel_denied=True,
                rejected_key_retry_succeeded=True,retry_same_key_same_job=True,final_queue_pending=0,worker_restarts=0,
                peak_sampled_global_pending=max(s['pending'] for s in samples),
                peak_sampled_employee_pending=max(s['max_employee'] for s in samples),sample_count=len(samples))
        report['restored_warehouse_unchanged']=sha(data/generation/'warehouse.duckdb')==before['warehouse']
        assert report['restored_warehouse_unchanged'];report['completed']=True
    except Exception as exc:
        report['failure']=dict(type=type(exc).__name__,message=str(exc)[:200]);raise
    finally:
        stop.set()
        if sampler:sampler.join(timeout=15)
        report.update(original_jobs_unchanged=job_fingerprint(DATA)==before['jobs'],
            original_identities_unchanged=identity_fingerprint(DATA)==before['identities'],
            original_pointer_unchanged=sha(DATA/'current.json')==before['pointer'],
            original_warehouse_unchanged=sha(DATA/generation/'warehouse.duckdb')==before['warehouse'],
            children_stopped=all(p.poll() is not None for p in children))
        save()
    assert all(report[k] for k in ['original_jobs_unchanged','original_identities_unchanged','original_pointer_unchanged','original_warehouse_unchanged','children_stopped'])
    print(json.dumps(dict(report=str(folder/'results.json'),completed=True,accepted=report['accepted'],
        rejected_429=report['rejected_429'],peak_pending=report['peak_sampled_global_pending'],children_stopped=True)),flush=True)

if __name__=='__main__':main()
