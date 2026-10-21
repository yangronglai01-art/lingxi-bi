import json,time
import pytest
from src.petkit.supervision import WorkerSupervisor
from src.petkit import control
from test_simulated_rollout import org

class Process:
    def __init__(self,pid):self.pid=pid;self.code=None
    def poll(self):return self.code

@pytest.fixture
def guard():
    clock=[0.0];current={n:Process(i+1) for i,n in enumerate(['worker','api','ui'])};created=[];states=[]
    def launch():
        p=Process(10+len(created));created.append(p);return p
    def stop(p):p.code=0
    def pulse():return dict(session='session',pid=current['worker'].pid,age=0)
    supervisor=WorkerSupervisor(current,launch,lambda state,event:states.append((dict(state),event)),pulse,stop,'session',now=lambda:clock[0])
    return supervisor,clock,created,states

def test_exit_restart_waits_for_exponential_backoff(guard):
    g,clock,created,_=guard;g.tick();assert g.phase=='running'
    g.current['worker'].code=1;g.tick();assert g.phase=='backoff' and g.restarts==1
    clock[0]=.49;g.tick();assert created==[]
    clock[0]=.5;g.tick();assert len(created)==1 and g.phase=='running'
    g.current['worker'].code=1;g.tick();assert g.due==1.5

def test_restart_budget_exhaustion_stops_all_owned_services(guard):
    g,clock,created,_=guard
    for i in range(3):
        g.current['worker'].code=1;g.tick();clock[0]=g.due;g.tick()
    assert len(created)==3
    g.current['worker'].code=1;g.tick()
    assert g.failed.is_set() and g.reason=='worker_restart_budget_exhausted'
    assert all(p.poll() is not None for p in g.current.values())

@pytest.mark.parametrize('value',[dict(session='old',pid=1,age=0),dict(session='session',pid=999,age=0),dict(session='session',pid=1,age=46)])
def test_old_session_pid_or_heartbeat_cannot_make_worker_ready(guard,value):
    g,_,_,_=guard;g.read_heartbeat=lambda:value;g.tick();assert g.phase=='starting'

def test_worker_start_timeout_fails_closed(guard):
    g,clock,_,_=guard;g.read_heartbeat=lambda:None;clock[0]=46;g.tick()
    assert g.failed.is_set() and g.reason=='worker_start_timeout'

def test_normal_close_during_backoff_never_restarts(guard):
    g,clock,created,_=guard;g.current['worker'].code=1;g.tick();g.close();clock[0]=10;g.tick()
    assert created==[] and g.phase=='stopped' and not g.failed.is_set()

@pytest.mark.parametrize('name',['api','ui'])
def test_request_interface_exit_stops_other_services(guard,name):
    g,_,_,_=guard;g.current[name].code=1;g.tick()
    assert g.reason==name+'_exited' and all(p.poll() is not None for p in g.current.values())

def test_spawn_failure_consumes_existing_budget(guard):
    g,clock,_,_=guard;g.max_restarts=1
    def fail():raise OSError('spawn failed')
    g.launch=fail;g.current['worker'].code=1;g.tick();clock[0]=g.due;g.tick()
    assert g.failed.is_set() and g.reason=='worker_restart_budget_exhausted'

def test_supervisor_state_write_failure_stops_services(guard):
    g,_,_,_=guard
    def fail(*args):raise OSError('disk failed')
    g.publish=fail;g.run()
    assert g.failed.is_set() and g.reason=='supervisor_state_unavailable'
    assert all(p.poll() is not None for p in g.current.values())

def test_ready_requires_current_session_pid_and_running_supervisor(org,monkeypatch):
    from fastapi.testclient import TestClient
    from src.petkit.api import app
    monkeypatch.setenv('PETKIT_LAUNCH_SESSION','current-session')
    def state(pid=12,phase='running',session='current-session'):
        with control.db() as c:
            c.execute("INSERT OR REPLACE INTO runtime VALUES('worker',?,?)",(json.dumps(dict(pid=pid,session=session)),time.time()))
            c.execute("INSERT OR REPLACE INTO runtime VALUES('supervisor',?,?)",(json.dumps(dict(worker_pid=12,session='current-session',phase=phase)),time.time()))
    with TestClient(app) as client:
        state(pid=11);assert client.get('/ready').status_code==503
        state(session='old');assert client.get('/ready').status_code==503
        state(phase='backoff');assert client.get('/ready').status_code==503
        state();response=client.get('/ready');assert response.status_code==200 and response.json()['launch_session']=='current-session'

def test_child_interpreter_pid_matches_popen_and_preserves_venv():
    import os,subprocess,sys
    from scripts.start_simulated import child_python
    original=dict(os.environ);env=os.environ.copy()
    command=child_python(env)
    code="import os,sys,json; import fastapi; print(json.dumps(dict(pid=os.getpid(),prefix=sys.prefix,package=fastapi.__file__)))"
    process=subprocess.Popen([command,'-c',code],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    output,error=process.communicate(timeout=15)
    assert process.returncode==0,error
    result=json.loads(output)
    assert result['pid']==process.pid and result['prefix']==sys.prefix
    assert 'site-packages' in result['package']
    assert dict(os.environ)==original
