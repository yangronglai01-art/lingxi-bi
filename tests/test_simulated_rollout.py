import json
import time
import pytest
from src.petkit import control,service,execution,worker
from src.petkit.access import authenticate
from src.petkit.retrieval import current_catalog
from src.petkit.guard import check
from src.petkit.settings import artifacts

@pytest.fixture
def org(tmp_path,monkeypatch):
    monkeypatch.setattr(control,'DATA',tmp_path)
    monkeypatch.setenv('PETKIT_DEPLOYMENT','simulated')
    monkeypatch.delenv('PETKIT_EMBEDDING_MODEL',raising=False)
    monkeypatch.setattr(service,'MODE','demo')
    control.initialize()
    import duckdb
    folder,_=artifacts()
    with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
        regions=[r[0] for r in c.execute('SELECT region_id FROM dim_region ORDER BY region_id').fetchall()]
    control.upsert_user('east','销售一部','analyst',[regions[0]])
    control.upsert_user('west','销售二部','analyst',[regions[1]])
    control.upsert_user('global','经营分析','analyst')
    keys={s:control.issue_token(s) for s in ['east','west','global']}
    return {s:authenticate(k) for s,k in keys.items()},keys,regions

def test_persistent_auth_rotation_disable_and_revision(org):
    principals,keys,regions=org
    with pytest.raises(PermissionError): authenticate('')
    token=control.issue_token('east')
    with pytest.raises(PermissionError): authenticate(keys['east'])
    assert authenticate(token).subject=='east'
    control.upsert_user('east','销售一部','analyst',[regions[1]])
    with pytest.raises(PermissionError): control.verify(principals['east'])
    assert authenticate(token).regions==(regions[1],)
    control.upsert_user('east','销售一部','analyst',[regions[1]],False)
    with pytest.raises(PermissionError): authenticate(token)

def test_regional_net_sales_matches_independent_raw_records(org):
    principals,_,regions=org
    import duckdb
    folder,_=artifacts()
    with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
        orders={r[0] for r in c.execute('SELECT order_id FROM fact_order WHERE region_id=?',[regions[0]]).fetchall()}
        items=[r for r in c.execute('SELECT item_id,order_id,amount FROM fact_order_item').fetchall() if r[1] in orders]
        ids={r[0] for r in items}
        refunds=sum(r[1] for r in c.execute('SELECT item_id,amount FROM fact_refund').fetchall() if r[0] in ids)
    answer=service.ask('各型号净销售额',principals['east'])
    assert answer.status=='success',answer.error
    assert abs(sum(r[-1] for r in answer.result.rows)-(sum(r[2] for r in items)-refunds))<.02
    global_answer=service.ask('各型号净销售额',principals['global'])
    assert sum(r[-1] for r in global_answer.result.rows)>sum(r[-1] for r in answer.result.rows)

@pytest.mark.parametrize('table,key,parent',[
 ('fact_order','order_id','orders'),('fact_order_item','order_id','orders'),('fact_refund','item_id','items'),
 ('bridge_order_device','item_id','items'),('fact_device_activation','device_id','devices'),
 ('fact_service_ticket','device_id','devices'),('fact_ticket_status','ticket_id','tickets')])
def test_direct_fact_queries_cannot_bypass_region(org,table,key,parent):
    principals,_,regions=org; cat=current_catalog()
    context={'tables':cat['tables'],'relationships':cat['relationships']}
    result,_=execution.execute(check(f'SELECT {key} FROM {table}',context),cat['generation'],principals['east'])
    import duckdb
    folder,_=artifacts()
    with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
        orders={r[0] for r in c.execute('SELECT order_id,region_id FROM fact_order').fetchall() if r[1]==regions[0]}
        items={r[0] for r in c.execute('SELECT item_id,order_id FROM fact_order_item').fetchall() if r[1] in orders}
        devices={r[0] for r in c.execute('SELECT device_id,item_id FROM bridge_order_device').fetchall() if r[1] in items}
        tickets={r[0] for r in c.execute('SELECT ticket_id,device_id FROM fact_service_ticket').fetchall() if r[1] in devices}
    assert {r[0] for r in result.rows}<=locals()[parent]

def test_unclassified_tables_and_qualified_raw_access_denied(org):
    principals,_,_=org; cat=current_catalog()
    unknown=next(t for t in cat['tables'].values() if not t['core'])
    assert not principals['east'].allowed(unknown)
    context={'tables':cat['tables'],'relationships':cat['relationships']}
    with pytest.raises(PermissionError): execution.execute(check(f'SELECT * FROM {unknown["name"]}',context),cat['generation'],principals['east'])
    with pytest.raises(PermissionError): check('SELECT * FROM warehouse.main.fact_order',context)

def test_job_isolation_idempotency_policy_change_and_disable(org):
    principals,_,regions=org
    job=control.submit(principals['east'],'各型号净销售额','same')
    assert control.submit(principals['east'],'各型号净销售额','same')==job
    with pytest.raises(ValueError): control.submit(principals['east'],'各型号期末库存','same')
    with pytest.raises(KeyError): control.get_job(principals['west'],job)
    assert worker.run_once('test-worker')
    result=control.get_job(principals['east'],job)
    assert result['state']=='completed' and result['result']['status']=='success',result
    control.upsert_user('east','销售一部','analyst',[regions[1]])
    fresh=control.principal_for('east')
    with pytest.raises(KeyError): control.get_job(fresh,job)
    with pytest.raises(PermissionError): control.get_job(principals['east'],job)

def test_cancel_and_crash_recovery(org):
    principals,_,_=org
    job=control.submit(principals['east'],'各型号净销售额')
    claimed=control.claim('crashed')
    assert claimed['id']==job
    with control.db() as c: c.execute('UPDATE jobs SET lease=? WHERE id=?',(time.time()-1,job))
    control.recover()
    assert control.get_job(principals['east'],job)['state']=='interrupted'
    second=control.submit(principals['east'],'各型号净销售额')
    control.claim('active')
    control.cancel(principals['east'],second)
    control.finish(second,'active',{'status':'success'})
    assert control.get_job(principals['east'],second)['state']=='cancelled'
    assert control.get_job(principals['east'],second)['result'] is None

def test_api_persistent_jobs_and_no_sync_bypass(org):
    from fastapi.testclient import TestClient
    from src.petkit.api import app
    _,keys,_=org
    with TestClient(app) as client:
        headers={'Authorization':'Bearer '+keys['east']}
        assert client.get('/me').status_code==401
        assert client.get('/me',headers=headers).json()['managed'] is True
        assert client.post('/ask',headers=headers,json={'question':'各型号净销售额'}).status_code==409
        response=client.post('/jobs',headers=headers,json={'question':'各型号净销售额'})
        assert response.status_code==202,response.text
        job=response.json()['id']
        assert client.get('/jobs/'+job,headers={'Authorization':'Bearer '+keys['west']}).status_code==404
        assert client.get('/ops/summary',headers=headers).status_code==403
        assert client.post('/jobs/'+job+'/cancel',headers=headers).json()['state']=='cancelled'

def test_history_authorization_version_isolation(org):
    from src.petkit.history import history_for
    principals,_,regions=org
    before=history_for(principals['east'])
    control.upsert_user('east','销售一部','analyst',[regions[1]])
    after=history_for(control.principal_for('east'))
    assert before.db_path!=after.db_path
