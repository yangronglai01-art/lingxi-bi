"""Bounded real-model client concurrency and separately measured adapter overlap."""
import argparse,hashlib,json,os,sqlite3,subprocess,sys,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def jobs(data):
    with sqlite3.connect(data/'control.sqlite3') as c:rows=c.execute('SELECT id,state,updated FROM jobs ORDER BY id').fetchall()
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

def child(args):
    data=Path(args.data_dir);output=Path(args.report_file)
    os.environ.update(PROJECT_PROFILE='petkit',PETKIT_MODE='llm',PETKIT_DEPLOYMENT='simulated',PETKIT_DATA_DIR=str(data))
    embedding=json.loads((data/'embedding_runtime.json').read_text(encoding='utf-8'))
    os.environ['PETKIT_EMBEDDING_MODEL']=embedding['model']
    import duckdb
    from scripts.start_simulated import running
    from scripts.accept_durable_stability import free
    from scripts.local_load_transport import LocalLoadClient
    from scripts.expanded_eval_support import regional_rows,grade
    from src.petkit.reference import expected
    from src.petkit.control import principal_for
    from src.petkit.service import ask
    from src.engine import llm_client
    from src import config
    pointer=json.loads((data/'current.json').read_text(encoding='utf-8'))
    definition=ROOT/'evals/petkit_expanded_v1.json'
    cases={c['id']:c for c in json.loads(definition.read_text(encoding='utf-8'))['cases']}
    queue_ids=['N01','N03','N05','N06'];direct_ids=['N02','N04','N07','N08']
    (output.parent/'frozen_cases.json').write_bytes(definition.read_bytes())
    catalog=json.loads((data/pointer['generation']/'catalog.json').read_text(encoding='utf-8'));raw={}
    with duckdb.connect(str(data/pointer['generation']/'warehouse.duckdb'),read_only=True) as c:
        for name,table in catalog['tables'].items():
            if table['core']:
                result=c.execute('SELECT * FROM "'+name+'"');columns=[r[0] for r in result.description]
                raw[name]=[dict(zip(columns,row)) for row in result.fetchall()]
    references={};scopes={}
    for identity in queue_ids+direct_ids:
        case=cases[identity];principal=principal_for(case['subject']);scopes[identity]=regional_rows(raw,principal.regions)
        references[identity]=expected(case,scopes[identity])
    keys=json.loads(Path(args.credentials).read_text(encoding='utf-8'))
    report=dict(generation=pointer['generation'],model=config.MODEL_NAME,completed=False,load_test=True,
        external_independent_benchmark=False,production_capacity_measured=False,maximum_total_generation_attempts=16,
        queue_maximum_generation_attempts=8,queue_generation_attempts_instrumented=False,direct_generation_budget=8,
        case_sha256=sha(definition),queue_ids=queue_ids,direct_ids=direct_ids,rows=[],
        code_sha256={name:sha(ROOT/name) for name in ['scripts/accept_real_model_concurrency.py',
        'scripts/expanded_eval_support.py','src/petkit/service.py','src/engine/llm_client.py']})
    def save():
        serialized=json.dumps(report,ensure_ascii=False,indent=2)
        if config.API_KEY:serialized=serialized.replace(config.API_KEY,'[REDACTED]')
        output.write_text(serialized,encoding='utf-8')
    save();owned=[]
    try:
        a,b=free(),free()
        while a==b:b=free()
        with running('llm',a,b,data_dir=data) as services:
            owned=services['processes'];client=LocalLoadClient(services['api_url'])
            try:
                def request(subject,path,body=None,key=None):
                    headers={'Authorization':'Bearer '+keys[subject],'Content-Type':'application/json'}
                    if key:headers['Idempotency-Key']=key
                    return client.request(path,headers,body)
                def run_job(identity):
                    case=cases[identity];subject=case['subject'];begin=time.monotonic()
                    task,status,_,_=request(subject,'/jobs',{'question':case['question']},'concurrency-'+output.parent.name+'-'+identity)
                    assert status==202
                    deadline=time.monotonic()+180
                    while time.monotonic()<deadline:
                        job,status,_,_=request(subject,'/jobs/'+task['id']);assert status==200
                        if job['state'] not in {'queued','running'}:break
                        time.sleep(.25)
                    else:raise TimeoutError('Queued model task exceeded 180 seconds')
                    from types import SimpleNamespace
                    value=job.get('result') or {};result=value.get('result')
                    answer=SimpleNamespace(status=value.get('status'),mock_used=value.get('mock_used'),
                        truncated=value.get('truncated',False),context=value.get('context') or {},
                        result=SimpleNamespace(columns=result['columns'],rows=result['rows']) if result is not None else None)
                    assessment=grade(case,answer,references[identity],scopes[identity])
                    return dict(id=identity,phase='durable_queue',job_id=task['id'],state=job['state'],status=answer.status,
                        passed=job['state']=='completed' and assessment['passed'],assessment=assessment,
                        elapsed_seconds=round(time.monotonic()-begin,3),answer=value)
                start=time.monotonic()
                with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(run_job,queue_ids))
                report['rows'].extend(rows);report['queue_elapsed_seconds']=round(time.monotonic()-start,3);save()
                assert all(r['passed'] for r in rows),'Queued real-model result failed'
                assert len({r['job_id'] for r in rows})==4
                health,status,_,_=request('global_analyst','/ops/health')
                assert status==200 and health['ready'] and health['queue']['pending']==0 and health['supervisor']['restarts']==0
                report.update(queue_clients=4,queue_worker_count=1,queue_provider_parallelism_measured=False,
                    queue_pending_after_phase=0,queue_worker_restarts=0)
            finally:client.close()
        assert all(p.poll() is not None for p in owned)
        report['queue_children_stopped_before_direct']=True;save()
        # Direct probe is a service-adapter experiment, NOT a change to deployed worker count.
        from src.petkit.retrieval import retrieve
        from src.petkit.planning import plan
        for identity in direct_ids:
            case=cases[identity];retrieve(case['question'],principal_for(case['subject']),plan(case['question'])['metric'])
        lock=threading.Lock();local=threading.local();meter=dict(active=0,peak=0,attempts=0);events=[]
        original=llm_client.chat_completion
        def measured(*a,**kw):
            with lock:
                if meter['attempts']>=8:raise RuntimeError('Direct generation budget exhausted before provider call')
                meter['attempts']+=1;meter['active']+=1;meter['peak']=max(meter['peak'],meter['active'])
                event=dict(id=local.case_id,start_monotonic=time.monotonic());events.append(event)
            try:return original(*a,**kw)
            finally:
                with lock:meter['active']-=1;event['end_monotonic']=time.monotonic()
        llm_client.chat_completion=measured
        try:
            for offset in [0,2]:
                barrier=threading.Barrier(2)
                def direct(identity):
                    local.case_id=identity;case=cases[identity];principal=principal_for(case['subject'])
                    barrier.wait(timeout=10);begin=time.monotonic();answer=ask(case['question'],principal)
                    assessment=grade(case,answer,references[identity],scopes[identity])
                    return dict(id=identity,phase='direct_adapter',status=answer.status,passed=assessment['passed'],
                        assessment=assessment,elapsed_seconds=round(time.monotonic()-begin,3),
                        answer=json.loads(answer.model_dump_json()))
                with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(direct,direct_ids[offset:offset+2]))
                report['rows'].extend(rows);save()
                assert all(r['passed'] for r in rows),'Direct concurrent result failed'
        finally:
            llm_client.chat_completion=original
            report.update(direct_peak_inflight_generations=meter['peak'],
                direct_generation_attempts=meter['attempts'],direct_generation_events=events)
            save()
        assert meter['peak']==2 and meter['active']==0
        report.update(direct_concurrency=2,direct_peak_inflight_generations=meter['peak'],
            direct_generation_attempts=meter['attempts'],direct_generation_events=events,completed=True)
    except Exception as exc:
        report['failure']=dict(type=type(exc).__name__,message=str(exc)[:200]);raise
    finally:
        report['children_stopped']=all(p.poll() is not None for p in owned);save()
    print(json.dumps(dict(completed=True,queued_cases=4,direct_cases=4,direct_peak=report['direct_peak_inflight_generations'],
        direct_attempts=report['direct_generation_attempts']),ensure_ascii=False),flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir');parser.add_argument('--credentials');parser.add_argument('--report-file')
    args=parser.parse_args()
    if args.data_dir:return child(args)
    from scripts.backup_simulated import backup,restore_drill
    from scripts.start_simulated import child_python
    from src.petkit.settings import DATA
    generation=json.loads((DATA/'current.json').read_text(encoding='utf-8'))['generation']
    before=dict(jobs=jobs(DATA),pointer=sha(DATA/'current.json'),warehouse=sha(DATA/generation/'warehouse.duckdb'))
    folder=ROOT/'reports/real_llm_concurrency_runs'/uuid.uuid4().hex[:12];folder.mkdir(parents=True)
    data=Path(restore_drill(backup())['restored_directory'])
    env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';interpreter=child_python(env)
    result=subprocess.run([interpreter,str(Path(__file__).resolve()),'--data-dir',str(data),
        '--credentials',str(DATA/'simulated_credentials.json'),'--report-file',str(folder/'results.json')],cwd=ROOT,env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    report=json.loads((folder/'results.json').read_text(encoding='utf-8'))
    report.update(original_jobs_unchanged=jobs(DATA)==before['jobs'],original_pointer_unchanged=sha(DATA/'current.json')==before['pointer'],
        original_warehouse_unchanged=sha(DATA/generation/'warehouse.duckdb')==before['warehouse'],isolated_directory=str(data))
    (folder/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result.returncode==0 and report['completed']
    assert all(report[k] for k in ['original_jobs_unchanged','original_pointer_unchanged','original_warehouse_unchanged','children_stopped'])
    print(json.dumps(dict(report=str(folder/'results.json'),completed=True),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
