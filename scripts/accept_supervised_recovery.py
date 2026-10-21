"""Real automatic worker restart, lease expiry and restart-budget drill; no paid LLM."""
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
def fingerprint(root):
    with sqlite3.connect(root/'control.sqlite3') as c:rows=c.execute('SELECT id,state,updated FROM jobs ORDER BY id').fetchall()
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()
def file_hash(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    original=fingerprint(DATA);data=Path(restore_drill(backup())['restored_directory'])
    generation=json.loads((data/'current.json').read_text())['generation'];warehouse=data/generation/'warehouse.duckdb';before=file_hash(warehouse)
    keys=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))
    report=dict(generation=generation,isolated_directory=str(data),mode='demo',real_llm_called=False,completed=False)
    out=ROOT/'reports/petkit500_supervised_recovery_acceptance.json'
    def save():out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    save();groups=[]
    def until(predicate,timeout=20):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            result=predicate()
            if result:return result
            time.sleep(.03)
        raise TimeoutError('守护验收未在限定时间内完成')
    try:
        a,b=free(),free()
        while a==b:b=free()
        with running('demo',a,b,data_dir=data) as services:
            groups.append(services['processes']);guard=services['supervisor'];assert guard and guard.phase=='running'
            def call(subject,path,body=None,key=None):
                headers={'Authorization':'Bearer '+keys[subject],'Content-Type':'application/json'}
                if key:headers['Idempotency-Key']=key
                req=Request(services['api_url']+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
                try:
                    with urlopen(req,timeout=5) as response:return json.load(response),response.status
                except HTTPError as exc:return json.load(exc),exc.code
            def job(subject,task):
                value,status=call(subject,'/jobs/'+task);assert status==200;return value
            def submit(subject,question,key):
                value,status=call(subject,'/jobs',dict(question=question),key);assert status==202,(status,value);return value['id']
            def done(subject,task):
                value=job(subject,task);return value if value['state'] not in {'queued','running'} else None
            # An instance lock rejects duplicate launchers before they create children.
            try:
                with running('demo',free(),free(),data_dir=data):raise AssertionError('重复启动器未被拒绝')
            except RuntimeError as exc:assert '已有启动器' in str(exc)
            report['duplicate_launcher_rejected']=True;save()
            task=submit('global_analyst','各型号净销售额','supervised-crash')
            until(lambda:job('global_analyst',task)['state']=='running',15)
            old_worker=services['current_processes']['worker'];old_pid=old_worker.pid
            interfaces={n:services['current_processes'][n].pid for n in ['api','ui']}
            killed=time.monotonic();stop_process(old_worker)
            readiness=[]
            def replacement_ready():
                _,status=call('global_analyst','/ready');readiness.append(status)
                return status==200 and services['current_processes']['worker'].pid!=old_pid and guard.phase=='running'
            until(replacement_ready,20)
            restart_seconds=time.monotonic()-killed
            assert 503 in readiness and guard.restarts==1
            assert all(services['current_processes'][n].pid==pid for n,pid in interfaces.items())
            second=submit('regional_supply','2026年9月各仓库采购收货数量','during-auto-recovery')
            result=until(lambda:done('regional_supply',second),120)
            assert result['state']=='completed' and result['result']['status']=='success'
            rows=result['result']['result'];index=rows['columns'].index('received_units');assert len(rows['rows'])==1 and rows['rows'][0][index]==10
            report['automatic_restart']=dict(old_pid=old_pid,new_pid=services['current_processes']['worker'].pid,ready_seconds=round(restart_seconds,3),readiness_503_observed=True,interfaces_kept_alive=True,next_regional_task_succeeded=True);save()
            interrupted=until(lambda:done('global_analyst',task),90)
            assert interrupted['state']=='interrupted' and interrupted['result'] is None
            elapsed=time.monotonic()-killed
            assert submit('global_analyst','各型号净销售额','supervised-crash')==task
            fresh=submit('global_analyst','各型号净销售额','supervised-new-key')
            assert until(lambda:done('global_analyst',fresh),60)['result']['status']=='success'
            report['lease_recovery']=dict(state='interrupted',seconds_after_kill=round(elapsed,3),real_time_wait=True,automatic_task_replay=False,new_key_resubmit_succeeded=True);save()
            restarts=[]
            for expected in [2,3]:
                previous=services['current_processes']['worker'];previous_pid=previous.pid;stop_process(previous)
                until(lambda:guard.restarts==expected and guard.phase=='running' and services['current_processes']['worker'].pid!=previous_pid,20)
                restarts.append(dict(restart=expected,pid=services['current_processes']['worker'].pid))
            stop_process(services['current_processes']['worker'])
            until(lambda:guard.failed.is_set(),10)
            until(lambda:all(p.poll() is not None for p in services['processes']),15)
            assert guard.reason=='worker_restart_budget_exhausted' and guard.restarts==3
            report['budget_exhaustion']=dict(restarts=3,extra_worker_deaths=3,reason=guard.reason,all_owned_processes_stopped=True,restarts_observed=restarts);save()
        # A new explicit launch resets the per-launch budget; normal exit must not respawn.
        a,b=free(),free()
        while a==b:b=free()
        with running('demo',a,b,data_dir=data) as normal:
            groups.append(normal['processes']);assert normal['supervisor'].restarts==0
            normal_count=len(normal['processes']);assert normal_count==3
        time.sleep(.6)
        assert len(normal['processes'])==normal_count and all(p.poll() is not None for p in normal['processes'])
        assert normal['supervisor'].phase=='stopped'
        report['normal_shutdown']=dict(no_restart_during_cleanup=True,all_owned_processes_stopped=True,new_launch_budget_reset=True)
        report['original_jobs_unchanged']=fingerprint(DATA)==original
        report['business_warehouse_unchanged']=file_hash(warehouse)==before
        assert report['original_jobs_unchanged'] and report['business_warehouse_unchanged']
        report['completed']=True
    except Exception as exc:report['failure']=dict(type=type(exc).__name__,message=str(exc)[:300]);raise
    finally:
        for processes in groups:
            for process in processes:stop_process(process)
        report['children_stopped']=all(p.poll() is not None for group in groups for p in group);save()
    print(json.dumps(dict(completed=True,restart=report['automatic_restart'],lease=report['lease_recovery'],budget_exhausted_safely=True,children_stopped=report['children_stopped'])),flush=True)

if __name__=='__main__':main()
