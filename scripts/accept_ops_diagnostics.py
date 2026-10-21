"""Independent real API/worker diagnostics acceptance; no external LLM calls."""
import hashlib,json,socket,sqlite3,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running,stop_process
from scripts.backup_simulated import backup,restore_drill
from src.petkit.settings import DATA

def free():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def jobs_hash(root):
    with sqlite3.connect(root/'control.sqlite3') as c:values=c.execute('SELECT id,state,updated,result,error FROM jobs ORDER BY id').fetchall()
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()
def file_hash(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    original=jobs_hash(DATA);data=Path(restore_drill(backup())['restored_directory']);generation=json.loads((data/'current.json').read_text())['generation']
    warehouse=data/generation/'warehouse.duckdb';before_warehouse=file_hash(warehouse)
    keys=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'));children=[]
    report=dict(generation=generation,isolated_directory=str(data),mode='demo',real_llm_called=False,completed=False)
    output=ROOT/'reports/petkit500_ops_diagnostics_acceptance.json'
    def save():output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def until(predicate,timeout=120):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.03)
        raise TimeoutError('运维诊断验收超时')
    save()
    try:
        a,b=free(),free()
        while a==b:b=free()
        with running('demo',a,b,data_dir=data) as services:
            children=services['processes']
            def call(subject,path,body=None):
                headers={'Content-Type':'application/json'}
                if subject:headers['Authorization']='Bearer '+keys[subject]
                req=Request(services['api_url']+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
                try:
                    with urlopen(req,timeout=10) as response:return json.load(response),response.status
                except HTTPError as exc:return json.load(exc),exc.code
            def health():
                value,status=call('global_analyst','/ops/health?window_minutes=1440');assert status==200;return value
            def get(subject,task):
                value,status=call(subject,'/jobs/'+task);assert status==200;return value
            def submit(subject,question):
                value,status=call(subject,'/jobs',dict(question=question));assert status==202;return value['id']
            def done(subject,task):
                value=get(subject,task);return value if value['state'] not in {'queued','running'} else None
            baseline=health();assert baseline['ready']
            assert call(None,'/ops/health')[1]==401
            denied={}
            for subject in ['regional_sales','regional_service','regional_supply','restricted_viewer']:
                value,status=call(subject,'/ops/health');denied[subject]=status==403 and 'queue' not in value
            assert all(denied.values());report['permission_denials']=denied
            tests=[('global_analyst','各型号净销售额','success'),('restricted_viewer','各型号净销售额','denied'),('global_analyst','各型号激活率','clarification'),('global_analyst','未批准的诊断样例','unsupported')]
            tasks=[]
            for index,(subject,question,expected) in enumerate(tests):
                task=submit(subject,question)
                if index==0:
                    until(lambda:get(subject,task)['state']=='running',10)
                    report['running_task_visible']=health()['queue']['running']>=1
                value=until(lambda:done(subject,task));assert value['state']=='completed' and value['result']['status']==expected
                tasks.append(dict(id=task,subject=subject,outcome=expected))
            after=health()
            for outcome in ['success','denied','clarification','unsupported']:
                assert after['recent']['outcomes'].get(outcome,0)-baseline['recent']['outcomes'].get(outcome,0)==1
            assert after['recent']['execution_evaluated']-baseline['recent']['execution_evaluated']==1
            serialized=json.dumps(after,ensure_ascii=False)
            assert services['session'] not in serialized and all(key not in serialized for key in keys.values())
            assert all(question not in serialized for _,question,_ in tests)
            assert all(field not in after['worker'] and field not in after['supervisor'] for field in ['pid','session','owner'])
            before_read=jobs_hash(data);health();call('global_analyst','/ready');assert jobs_hash(data)==before_read
            report.update(running_task_visible=report['running_task_visible'],tasks=tasks,outcomes_delta=dict(success=1,denied=1,clarification=1,unsupported=1),execution_evaluated_delta=1,raw_content_redacted=True,read_only_jobs_verified=True);save()
            stop_process(services['current_processes']['worker'])
            def degraded():
                value=health();_,code=call('global_analyst','/ready')
                return value if not value['ready'] and code==503 else None
            unavailable=until(degraded,20)
            assert unavailable['status']=='degraded' and 'supervisor_not_ready' in {a['code'] for a in unavailable['alerts']}
            def healthy_again():
                value=health();return value if value['ready'] and value['supervisor']['restarts']==1 else None
            recovered=until(healthy_again,20)
            task=submit('regional_supply','2026年9月各仓库采购收货数量')
            value=until(lambda:done('regional_supply',task));assert value['result']['status']=='success'
            result=value['result']['result'];assert len(result['rows'])==1 and result['rows'][0][result['columns'].index('received_units')]==10
            report.update(restart_degraded=unavailable,restart_recovered=recovered,diagnostics_matches_ready=True,recovered_regional_units=10)
        report['original_jobs_unchanged']=jobs_hash(DATA)==original;report['business_warehouse_unchanged']=file_hash(warehouse)==before_warehouse
        assert report['original_jobs_unchanged'] and report['business_warehouse_unchanged'] and report['running_task_visible']
        report['completed']=True
    except Exception as exc:report['failure']=dict(type=type(exc).__name__,message=str(exc)[:300]);raise
    finally:
        for process in children:stop_process(process)
        report['children_stopped']=all(p.poll() is not None for p in children);save()
    print(json.dumps(dict(completed=True,permission_denials=denied,outcomes_delta=report['outcomes_delta'],restart_diagnostics=True,children_stopped=report['children_stopped'])),flush=True)

if __name__=='__main__':main()
