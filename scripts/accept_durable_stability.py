"""Bounded demo-mode multi-user load and real worker process failure drill."""
import concurrent.futures,hashlib,json,math,os,socket,sqlite3,statistics,subprocess,sys,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running,child_python
from scripts.backup_simulated import backup,restore_drill
from src.petkit.settings import DATA,artifacts
from src.petkit.reference import expected

def free():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def job_fingerprint(root):
    with sqlite3.connect(root/'control.sqlite3') as c:rows=c.execute('SELECT id,state,updated FROM jobs ORDER BY id').fetchall()
    return dict(count=len(rows),sha=hashlib.sha256(json.dumps(rows).encode()).hexdigest())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def latency(values):
    values=sorted(values)
    return dict(p50_seconds=round(statistics.median(values),4),p95_seconds=round(values[math.ceil(.95*len(values))-1],4),max_seconds=round(max(values),4))

def main():
    original_jobs=job_fingerprint(DATA);drill=restore_drill(backup());data=Path(drill['restored_directory'])
    credentials=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))
    generation=json.loads((data/'current.json').read_text())['generation'];warehouse=data/generation/'warehouse.duckdb';warehouse_before=sha(warehouse)
    report=dict(generation=generation,isolated_directory=str(data),kind='bounded single-machine durable-worker acceptance',mode='demo',real_llm_called=False,production_capacity_measured=False,completed=False)
    output=ROOT/'reports/petkit500_durable_stability_acceptance.json'
    def save():output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    save();owned=[];handles=[];children=[]
    env=os.environ.copy();env.update(PROJECT_PROFILE='petkit',PETKIT_DEPLOYMENT='simulated',PETKIT_MODE='demo',PETKIT_DATA_DIR=str(data))
    env.pop('PETKIT_LOCAL_UI_TOKEN',None);env.pop('PETKIT_API_TOKENS',None)
    config=data/'embedding_runtime.json'
    if config.exists():env['PETKIT_EMBEDDING_MODEL']=json.loads(config.read_text(encoding='utf-8'))['model']
    interpreter=child_python(env)
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    def spawn(fault=False):
        log=(data/'simulated_logs'/('stability-'+uuid.uuid4().hex[:8]+'.log')).open('w',encoding='utf-8');handles.append(log)
        command=[interpreter,str(ROOT/'scripts/stability_fault_worker.py'),'--marker',str(data/'fault.marker')] if fault else [interpreter,'-m','src.petkit.worker']
        p=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=flags);owned.append(p);return p
    def stop(p):
        if p.poll() is None:p.terminate()
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
    try:
        a,b=free(),free()
        while a==b:b=free()
        with running('demo',a,b,data_dir=data,supervise=False) as services:
            children=services['processes']
            def request(subject,path,body=None,key=None):
                headers={'Authorization':'Bearer '+credentials[subject],'Content-Type':'application/json'}
                if key:headers['Idempotency-Key']=key
                req=Request(services['api_url']+path,headers=headers,data=json.dumps(body).encode() if body is not None else None);start=time.monotonic()
                try:
                    with urlopen(req,timeout=20) as response:return json.load(response),response.status,time.monotonic()-start
                except HTTPError as exc:return json.load(exc),exc.code,time.monotonic()-start
            def get(subject,task):
                value,status,_=request(subject,'/jobs/'+task);assert status==200,(status,value);return value
            def submit(subject,question,key=None):
                value,status,seconds=request(subject,'/jobs',dict(question=question),key);assert status==202,(status,value);return value['id'],seconds
            def wait(subject,task,timeout=120):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    value=get(subject,task)
                    if value['state'] not in {'queued','running'}:return value
                    time.sleep(.15)
                raise TimeoutError('验收任务未在边界时间内完成')
            warm,_=submit('global_analyst','各型号净销售额');assert wait('global_analyst',warm)['result']['status']=='success'
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                duplicate=list(pool.map(lambda _:submit('global_analyst','各型号净销售额','stability-shared'),range(24)))
            assert len({v[0] for v in duplicate})==1
            assert wait('global_analyst',duplicate[0][0])['result']['status']=='success'
            report['concurrent_idempotency']=dict(requests=24,unique_jobs=1,submission_latency=latency([v[1] for v in duplicate]));save()
            import duckdb
            raw={}
            catalog=json.loads((data/generation/'catalog.json').read_text(encoding='utf-8'))
            with duckdb.connect(str(warehouse),read_only=True) as c:
                for name,t in catalog['tables'].items():
                    if t['core']:
                        cur=c.execute('SELECT * FROM "'+name+'"');columns=[d[0] for d in cur.description];raw[name]=[dict(zip(columns,row)) for row in cur.fetchall()]
            questions={'global_analyst':('各型号净销售额','net_sales'),'regional_sales':('各型号净销售额','net_sales'),'regional_service':('各型号售出30天售后设备率','service30'),'regional_supply':('2026年9月各仓库采购收货数量','received_units'),'restricted_viewer':('各型号净销售额',None)}
            references={}
            for subject,(_,metric) in questions.items():
                if not metric:continue
                me,status,_=request(subject,'/me');assert status==200
                regions=me['regions'];records=dict(raw)
                orders={r['order_id'] for r in raw['fact_order'] if not regions or r['region_id'] in regions}
                items={r['item_id'] for r in raw['fact_order_item'] if r['order_id'] in orders}
                records['fact_order']=[r for r in raw['fact_order'] if r['order_id'] in orders]
                records['fact_order_item']=[r for r in raw['fact_order_item'] if r['item_id'] in items]
                records['fact_refund']=[r for r in raw['fact_refund'] if r['item_id'] in items]
                records['bridge_order_device']=[r for r in raw['bridge_order_device'] if r['item_id'] in items]
                warehouses={r['warehouse_id'] for r in raw['dim_warehouse'] if not regions or r['region_id'] in regions}
                records['fact_receipt']=[r for r in raw['fact_receipt'] if r['warehouse_id'] in warehouses]
                references[subject]=expected(dict(metric_id=metric,group='warehouse' if metric=='received_units' else 'model',month='2026-09' if metric=='received_units' else None),records)
            cases=[(subject,i) for i in range(6) for subject in questions];started=time.monotonic()
            def send(case):
                subject,index=case;task,seconds=submit(subject,questions[subject][0],f'load-{subject}-{index}');return subject,task,seconds
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:submitted=list(pool.map(send,cases))
            details=[]
            for subject,task,seconds in submitted:
                value=wait(subject,task);answer=value.get('result') or {};metric=questions[subject][1]
                passed=value['state']=='completed'
                if metric:
                    observed={row[0]:tuple(row[1:]) for row in (answer.get('result') or {}).get('rows',[])};wanted=references[subject]
                    passed=passed and answer.get('status')=='success' and observed.keys()==wanted.keys() and all(len(observed[k])==len(v) and all(abs(float(a)-float(b))<.011 for a,b in zip(observed[k],v)) for k,v in wanted.items())
                else:passed=passed and answer.get('status')=='denied' and not answer.get('result')
                details.append(dict(subject=subject,id=task,state=value['state'],answer_status=answer.get('status'),oracle_passed=passed,submission_seconds=seconds,end_to_end_seconds=value['updated']-value['created']))
            elapsed=time.monotonic()-started
            report['load']=dict(http_concurrency=10,tasks=30,expected_success=24,expected_denied=6,success=sum(d['answer_status']=='success' for d in details),denied=sum(d['answer_status']=='denied' for d in details),passed=sum(d['oracle_passed'] for d in details),elapsed_seconds=round(elapsed,3),tasks_per_second=round(30/elapsed,3),submission_latency=latency([d['submission_seconds'] for d in details]),end_to_end_latency=latency([d['end_to_end_seconds'] for d in details]),details=details)
            save();assert all(d['oracle_passed'] for d in details)
            stop(children[0])
            queued=[submit('global_analyst','各型号净销售额','quota-'+str(i))[0] for i in range(20)]
            value,status,_=request('global_analyst','/jobs',dict(question='各型号净销售额'),'quota-over');assert status==429 and value['detail']['scope']=='employee'
            retry,_=submit('global_analyst','各型号净销售额','quota-0');assert retry==queued[0]
            for task in queued:
                value,status,_=request('global_analyst','/jobs/'+task+'/cancel',{});assert status==200 and value['state']=='cancelled'
            report['queue_backpressure']=dict(queued=20,overflow_status=429,idempotency_retry_allowed=True,cancelled=20);save()
            # Cold worker ensures enough time to observe a claimed task being cancelled.
            cancelled,_=submit('global_analyst','各型号净销售额','running-cancel');active=spawn()
            deadline=time.monotonic()+15
            while get('global_analyst',cancelled)['state']=='queued' and time.monotonic()<deadline:time.sleep(.02)
            assert get('global_analyst',cancelled)['state']=='running'
            value,status,_=request('global_analyst','/jobs/'+cancelled+'/cancel',{});assert status==200 and value['state']=='cancelled'
            sentinel,_=submit('regional_supply','2026年9月各仓库采购收货数量','after-cancel');assert wait('regional_supply',sentinel)['result']['status']=='success'
            value=get('global_analyst',cancelled);assert value['state']=='cancelled' and value['result'] is None
            second=spawn();second.wait(timeout=10);assert second.returncode!=0
            report['running_cancel']=dict(late_result_suppressed=True,next_task_succeeded=True);report['second_worker_rejected']=True;save();stop(active)
            interrupted,_=submit('global_analyst','各型号净销售额','crash-key');fault=spawn(True)
            deadline=time.monotonic()+20
            while not (data/'fault.marker').exists() and time.monotonic()<deadline:time.sleep(.05)
            assert (data/'fault.marker').exists() and get('global_analyst',interrupted)['state']=='running'
            stop(fault);killed=time.monotonic();replacement=spawn()
            report['recovery_started']=dict(job_id=interrupted,lease_seconds=60,real_time_wait=True);save()
            value=wait('global_analyst',interrupted,timeout=90)
            recovery_seconds=time.monotonic()-killed
            assert value['state']=='interrupted' and value['result'] is None
            same,_=submit('global_analyst','各型号净销售额','crash-key');assert same==interrupted
            fresh,_=submit('global_analyst','各型号净销售额','crash-new-key');assert wait('global_analyst',fresh)['result']['status']=='success'
            report['process_recovery']=dict(original_state='interrupted',seconds_after_kill=round(recovery_seconds,3),real_lease_expiry=True,automatic_paid_replay=False,same_key_keeps_old_task=True,new_key_resubmit_succeeded=True);save()
            stop(replacement)
        report['business_warehouse_unchanged']=sha(warehouse)==warehouse_before
        report['original_instance_jobs_unchanged']=job_fingerprint(DATA)==original_jobs
        assert report['business_warehouse_unchanged'] and report['original_instance_jobs_unchanged']
        report['completed']=True
    except Exception as exc:
        report['failure']=dict(type=type(exc).__name__,message=str(exc)[:500]);raise
    finally:
        for p in reversed(owned):stop(p)
        for handle in handles:handle.close()
        report['children_stopped']=all(p.poll() is not None for p in children+owned);save()
    print(json.dumps(dict(completed=report['completed'],tasks=report['load']['tasks'],passed=report['load']['passed'],recovery=report['process_recovery'],children_stopped=report['children_stopped']),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
