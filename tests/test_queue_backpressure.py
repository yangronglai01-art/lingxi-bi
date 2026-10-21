from concurrent.futures import ThreadPoolExecutor
import pytest
from test_simulated_rollout import org
from src.petkit import control

def test_employee_backpressure_is_429_and_retry_preserves_idempotency(org):
    from fastapi.testclient import TestClient
    from src.petkit.api import app
    principals,keys,_=org;p=principals['east'];first=control.submit(p,'各型号净销售额','existing')
    for i in range(19):control.submit(p,'各型号净销售额','fill-'+str(i))
    with TestClient(app) as client:
        headers={'Authorization':'Bearer '+keys['east']}
        r=client.post('/jobs',headers=headers,json=dict(question='各型号净销售额'))
        assert r.status_code==429 and r.headers['retry-after']=='5' and r.json()['detail']['scope']=='employee'
        duplicate=client.post('/jobs',headers=dict(headers,**{'Idempotency-Key':'existing'}),json=dict(question='各型号净销售额'))
        assert duplicate.status_code==202 and duplicate.json()['id']==first
        conflict=client.post('/jobs',headers=dict(headers,**{'Idempotency-Key':'existing'}),json=dict(question='各型号期末库存'))
        assert conflict.status_code==409
        assert client.post('/jobs/'+first+'/cancel',headers=headers).json()['state']=='cancelled'
        assert client.post('/jobs',headers=headers,json=dict(question='各型号净销售额')).status_code==202

def test_global_admission_limit_is_distinct_from_employee_limit(org):
    principals,_,_=org
    control.upsert_user('other','测试','analyst')
    with control.db() as c:
        c.executemany('INSERT INTO jobs(id,subject,policy_version,generation,question,state,created,updated,idempotency) VALUES(?,?,?,?,?,?,?,?,?)',[(str(i),'other',1,'test','q','queued',0,0,str(i)) for i in range(200)])
    with pytest.raises(control.QueueFull) as exc:control.submit(principals['east'],'各型号净销售额')
    assert exc.value.scope=='global'

def test_concurrent_same_key_creates_one_job_and_one_submission_audit(org):
    principals,_,_=org
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids=list(pool.map(lambda _:control.submit(principals['east'],'各型号净销售额','shared-key'),range(24)))
    assert len(set(ids))==1
    with control.db() as c:
        assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM audit WHERE event='job_submitted'").fetchone()[0]==1

def test_concurrent_distinct_submissions_cannot_race_past_user_quota(org):
    principals,_,_=org
    def submit(i):
        try:control.submit(principals['east'],'各型号净销售额','distinct-'+str(i));return 'accepted'
        except control.QueueFull:return 'limited'
    with ThreadPoolExecutor(max_workers=8) as pool:statuses=list(pool.map(submit,range(32)))
    assert statuses.count('accepted')==20 and statuses.count('limited')==12
