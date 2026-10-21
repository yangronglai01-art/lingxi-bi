"""Real HTTP burst and recovery on an isolated default-supervised demo instance."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
import uuid
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running
from scripts.backup_simulated import backup,restore_drill
from scripts.accept_durable_stability import free,job_fingerprint,sha
from src.petkit.settings import DATA
from src.petkit.reference import expected

def main():
    output=ROOT/'reports/burst_backpressure_runs'/uuid.uuid4().hex[:12]
    output.mkdir(parents=True)
    report=dict(mode='demo',real_llm_called=False,completed=False,http_concurrency=16,
        offered_burst_requests=64,artificial_worker_pause=False,original_jobs_before=job_fingerprint(DATA))
    def save(): (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    pointer=sha(DATA/'current.json')
    generation=json.loads((DATA/'current.json').read_text(encoding='utf-8'))['generation']
    warehouse=sha(DATA/generation/'warehouse.duckdb')
    children=[]
    sampler=None
    stop=threading.Event()
    try:
        restored=restore_drill(backup())
        data=Path(restored['restored_directory'])
        report.update(generation=generation,isolated_directory=str(data))
        save()
        keys=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))
        # Compute the full sales oracle directly from raw rows before starting load.
        import duckdb
        raw={}
        with duckdb.connect(str(data/generation/'warehouse.duckdb'),read_only=True) as c:
            for table in ['dim_product','dim_device','fact_order_item','fact_order','dim_sku','dim_channel','fact_refund']:
                result=c.execute('SELECT * FROM '+table)
                columns=[v[0] for v in result.description]
                raw[table]=[dict(zip(columns,row)) for row in result.fetchall()]
        wanted=expected(dict(metric_id='net_sales',group='model',month=None),raw)
        def correct(job):
            answer=job.get('result') or {}
            rows=(answer.get('result') or {}).get('rows',[])
            observed={r[0]:r[1:] for r in rows}
            return (job['state']=='completed' and answer.get('status')=='success' and len(rows)==len(observed)
                and observed.keys()==wanted.keys() and all(len(observed[k])==len(v) and
                    all(abs(float(a)-float(b))<=.011 for a,b in zip(observed[k],v)) for k,v in wanted.items()))
        a,b=free(),free()
        while a==b:b=free()
        samples=[]
        with running('demo',a,b,data_dir=data) as services:
            children=services['processes']
            def request(subject,path,body=None,key=None):
                headers={'Authorization':'Bearer '+keys[subject],'Content-Type':'application/json'}
                if key:headers['Idempotency-Key']=key
                req=Request(services['api_url']+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
                try:
                    with urlopen(req,timeout=15) as response:return json.load(response),response.status,{k.lower():v for k,v in response.headers.items()}
                except HTTPError as exc:return json.load(exc),exc.code,{k.lower():v for k,v in exc.headers.items()}
            def wait(subject,task):
                deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    job,status,_=request(subject,'/jobs/'+task)
                    assert status==200
                    if job['state'] not in {'queued','running'}:return job
                    time.sleep(.15)
                raise TimeoutError('Accepted task did not drain within 90 seconds')
            def submit(index):
                key=f'{output.name}-burst-{index}'
                value,status,headers=request('global_analyst','/jobs',dict(question='各型号净销售额'),key)
                return dict(index=index,key=key,status=status,id=value.get('id'),detail=value.get('detail'),retry_after=headers.get('retry-after'))
            value,status,_=request('global_analyst','/jobs',dict(question='各型号净销售额'),'warm-'+output.name)
            assert status==202 and correct(wait('global_analyst',value['id']))
            with sqlite3.connect(data/'control.sqlite3') as c:baseline=c.execute('SELECT count(*) FROM jobs').fetchone()[0]
            def sample():
                while not stop.wait(.05):
                    with sqlite3.connect(data/'control.sqlite3',timeout=10) as c:
                        count=c.execute("SELECT count(*) FROM jobs WHERE subject='global_analyst' AND state IN ('queued','running')").fetchone()[0]
                    samples.append(count)
            sampler=threading.Thread(target=sample,daemon=True)
            sampler.start()
            start=time.monotonic()
            with ThreadPoolExecutor(max_workers=16) as pool:responses=list(pool.map(submit,range(64)))
            burst_seconds=time.monotonic()-start
            accepted=[r for r in responses if r['status']==202]
            rejected=[r for r in responses if r['status']==429]
            assert accepted and rejected and len(accepted)+len(rejected)==64
            assert all(r['retry_after']=='5' and r['detail']['code']=='queue_full' and r['detail']['scope']=='employee' for r in rejected)
            assert len({r['id'] for r in accepted})==len(accepted)
            with sqlite3.connect(data/'control.sqlite3') as c:
                assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]-baseline==len(accepted)
                for r in rejected:
                    digest=hashlib.sha256((generation+':'+r['key']).encode()).hexdigest()
                    assert c.execute('SELECT count(*) FROM jobs WHERE idempotency=?',(digest,)).fetchone()[0]==0
            chosen=accepted[0]
            def duplicate(_):return request('global_analyst','/jobs',dict(question='各型号净销售额'),chosen['key'])
            with ThreadPoolExecutor(max_workers=8) as pool:duplicates=list(pool.map(duplicate,range(16)))
            assert all(code==202 and value['id']==chosen['id'] for value,code,_ in duplicates)
            _,conflict,_=request('global_analyst','/jobs',dict(question='各型号期末库存'),chosen['key'])
            assert conflict==409
            report.update(burst_seconds=round(burst_seconds,3),accepted=len(accepted),rejected_429=len(rejected),
                rejected_jobs_not_persisted=True,all_429_retry_after_seconds=5,quota_scope='employee',
                duplicate_requests=16,duplicate_unique_jobs=1,idempotency_conflict_http=conflict,responses=responses)
            save()
            verified=0
            for r in accepted:
                assert correct(wait('global_analyst',r['id']))
                verified+=1
            # Respect the server's Retry-After before retrying the same rejected key.
            while time.monotonic()-start<5:stop.wait(.1)
            retry=rejected[0]
            value,status,_=request('global_analyst','/jobs',dict(question='各型号净销售额'),retry['key'])
            assert status==202 and correct(wait('global_analyst',value['id']))
            again,status,_=request('global_analyst','/jobs',dict(question='各型号净销售额'),retry['key'])
            assert status==202 and again['id']==value['id']
            # Independent account remains available after the backlog has drained.
            value,status,_=request('regional_supply','/jobs',dict(question='2026年9月各仓库采购收货数量'),'after-burst-'+output.name)
            assert status==202
            sentinel=wait('regional_supply',value['id'])
            answer=sentinel.get('result') or {}
            assert sentinel['state']=='completed' and answer.get('status')=='success'
            result=answer['result']
            assert len(result['rows'])==1 and result['rows'][0][result['columns'].index('received_units')]==10
            health,status,_=request('global_analyst','/ops/health')
            assert status==200 and health['ready'] and health['queue']['pending']==0 and health['supervisor']['restarts']==0
            stop.set();sampler.join(timeout=15)
            assert samples and max(samples)<=20
            report.update(accepted_jobs_oracle_verified=verified,peak_sampled_employee_pending=max(samples),
                rejected_key_retry_succeeded=True,retried_key_duplicate_same_job=True,recovery_regional_received_units=10,
                final_queue_pending=0,worker_restarts=0,post_burst_ready=True)
        report['restored_warehouse_unchanged']=sha(data/generation/'warehouse.duckdb')==warehouse
        assert report['restored_warehouse_unchanged']
        report['completed']=True
    except Exception as exc:
        report['failure']=dict(type=type(exc).__name__,message=str(exc)[:200])
        raise
    finally:
        stop.set()
        if sampler:sampler.join(timeout=15)
        report.update(original_jobs_unchanged=job_fingerprint(DATA)==report['original_jobs_before'],
            original_pointer_unchanged=sha(DATA/'current.json')==pointer,
            original_warehouse_unchanged=sha(DATA/generation/'warehouse.duckdb')==warehouse,
            children_stopped=all(p.poll() is not None for p in children))
        save()
    assert all(report[k] for k in ['original_jobs_unchanged','original_pointer_unchanged','original_warehouse_unchanged','children_stopped'])
    print(json.dumps(dict(report=str(output/'results.json'),completed=True,accepted=report['accepted'],
        rejected_429=report['rejected_429'],peak_pending=report['peak_sampled_employee_pending'],children_stopped=report['children_stopped'])),flush=True)

if __name__=='__main__':main()
