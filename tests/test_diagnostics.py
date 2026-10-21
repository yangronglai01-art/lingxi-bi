import json,os,sqlite3,time
import pytest
from test_simulated_rollout import org
from src.petkit import control,diagnostics,health_checks

@pytest.fixture
def healthy(org,monkeypatch):
    principals,keys,regions=org;monkeypatch.setenv('PETKIT_LAUNCH_SESSION','DIAG-SESSION-PRIVATE')
    control.tick('DIAG-OWNER-PRIVATE')
    with control.db() as c:c.execute("INSERT INTO runtime VALUES('supervisor',?,?)",(json.dumps(dict(session='DIAG-SESSION-PRIVATE',phase='running',worker_pid=os.getpid(),restarts=0,max_restarts=3)),time.time()))
    return principals,keys,regions

def seed(identifier,state='completed',answer='success',duration=2,offset=1,subject='global',lease=None):
    now=time.time();result=json.dumps(dict(status=answer,question='PRIVATE-QUESTION',sql='PRIVATE-SQL',result={'rows':[['PRIVATE-RESULT']]})) if answer is not None else 'PRIVATE-INVALID-JSON'
    with control.db() as c:c.execute('INSERT INTO jobs(id,subject,policy_version,generation,question,state,created,updated,idempotency,result,error,lease) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,subject,1,'test','PRIVATE-QUESTION',state,now-offset-duration,now-offset,identifier,result,'PRIVATE-ERROR',lease))

def test_empty_snapshot_is_healthy_and_null_not_zero_latency(healthy):
    principals,_,_=healthy;value=diagnostics.snapshot(principals['global'])
    assert value['status']=='healthy' and value['ready']
    assert value['recent']['execution_success_rate'] is None
    assert value['recent']['successful_end_to_end_seconds']==dict(count=0,p50=None,p95=None)

def test_runtime_outcomes_do_not_treat_denial_or_clarification_as_failure(healthy):
    principals,_,_=healthy
    seed('s1',duration=2);seed('s2',duration=6)
    for state,answer in [('completed','denied'),('completed','clarification'),('completed','unsupported'),('failed',None),('completed','error'),('completed','timeout'),('completed','not_ready'),('completed',None),('interrupted',None),('cancelled',None),('superseded',None)]:seed(state+'-'+str(answer),state,answer)
    value=diagnostics.snapshot(principals['global']);recent=value['recent']
    assert recent['execution_successes']==2 and recent['execution_failures']==5 and recent['execution_evaluated']==7
    assert recent['execution_success_rate']==round(2/7,4)
    assert recent['outcomes']['denied']==1 and recent['outcomes']['interrupted']==1
    assert recent['successful_end_to_end_seconds']==dict(count=2,p50=2,p95=6)
    assert value['status']=='degraded'
    serialized=json.dumps(value)
    for sensitive in ['PRIVATE-QUESTION','PRIVATE-SQL','PRIVATE-RESULT','PRIVATE-ERROR','PRIVATE-INVALID-JSON','DIAG-SESSION-PRIVATE','DIAG-OWNER-PRIVATE']:assert sensitive not in serialized

def test_sample_limit_and_window_are_explicit(healthy):
    principals,_,_=healthy
    for i in range(205):seed(str(i),offset=i+1)
    seed('old',offset=1000)
    recent=diagnostics.snapshot(principals['global'])['recent']
    assert recent['terminal_count']==205 and recent['sample_count']==recent['sample_limit']==200 and recent['truncated']
    assert recent['execution_successes']==200

def test_queue_wait_quota_and_expired_lease_alerts(healthy):
    principals,_,_=healthy
    for i in range(160):seed('q'+str(i),state='queued',offset=120,subject='worker-'+str(i//20))
    seed('r1',state='running',lease=time.time()-1);seed('r2',state='running',lease=None)
    value=diagnostics.snapshot(principals['global']);codes={a['code'] for a in value['alerts']}
    assert {'queue_wait_high','employee_queue_high','global_queue_high','expired_running_leases'}<=codes
    assert value['queue']['expired_running_leases']==2 and value['queue']['pending']==162
    assert value['queue']['oldest_wait_seconds']>=120

def test_unhealthy_heartbeat_matches_ready_probe(healthy):
    from fastapi.testclient import TestClient
    from src.petkit.api import app
    principals,keys,_=healthy
    with control.db() as c:c.execute("UPDATE runtime SET value=? WHERE name='worker'",(json.dumps(dict(session='old',pid=os.getpid())),))
    with TestClient(app) as client:
        diagnostic=client.get('/ops/health',headers={'Authorization':'Bearer '+keys['global']}).json()
        probe=client.get('/ready');assert probe.status_code==503
        assert diagnostic['checks']==probe.json()['detail'] and not diagnostic['ready']
        assert diagnostic['status']=='degraded' and 'worker_not_ready' in {a['code'] for a in diagnostic['alerts']}

def test_warehouse_failure_is_unavailable(healthy,monkeypatch):
    principals,_,_=healthy
    def missing():raise FileNotFoundError('PRIVATE-PATH')
    monkeypatch.setattr(health_checks,'artifacts',missing)
    value=diagnostics.snapshot(principals['global']);assert value['status']=='unavailable' and not value['ready']
    assert 'PRIVATE-PATH' not in json.dumps(value)

@pytest.mark.parametrize('role',['analyst','sales','service','supply','viewer'])
def test_regional_and_other_roles_cannot_read_ops_health(healthy,role):
    from fastapi.testclient import TestClient
    from src.petkit.api import app
    _,_,regions=healthy
    control.upsert_user('denied','测试',role,[regions[0]]);key=control.issue_token('denied')
    with TestClient(app) as client:
        assert client.get('/ops/health').status_code==401
        response=client.get('/ops/health',headers={'Authorization':'Bearer '+key});assert response.status_code==403
        assert 'queue' not in response.json()

def test_invalid_window_and_auth_store_failure_are_safe(healthy,monkeypatch):
    from fastapi.testclient import TestClient
    from src.petkit import api
    _,keys,_=healthy
    with TestClient(api.app) as client:
        assert client.get('/ops/health?window_minutes=0',headers={'Authorization':'Bearer '+keys['global']}).status_code==422
        def fail(token):raise sqlite3.OperationalError('PRIVATE-TOKEN-OR-PATH')
        monkeypatch.setattr(api,'authenticate',fail)
        response=client.get('/ops/health',headers={'Authorization':'Bearer '+keys['global']})
        assert response.status_code==503 and 'PRIVATE-TOKEN-OR-PATH' not in response.text

def test_workbench_panel_is_on_demand_and_global_only(healthy,monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    _,keys,_=healthy
    monkeypatch.setenv('PETKIT_LOCAL_UI_TOKEN',keys['global'])
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'src/app.py')).run(timeout=30)
    assert not app.exception
    button=next(b for b in app.button if b.label=='读取运维快照');button.click();app.run(timeout=30)
    assert not app.exception and any(e.label=='运维诊断' for e in app.expander)
    assert any(m.label=='排队任务' for m in app.metric)
    monkeypatch.setenv('PETKIT_LOCAL_UI_TOKEN',keys['east'])
    regional=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'src/app.py')).run(timeout=30)
    assert not regional.exception and all(b.label!='读取运维快照' for b in regional.button)

def test_recent_window_excludes_future_timestamps(healthy):
    principals,_,_=healthy
    seed('present',offset=1);seed('future',offset=-60);seed('past',offset=1000)
    recent=diagnostics.snapshot(principals['global'])['recent']
    assert recent['terminal_count']==1 and recent['sample_count']==1
