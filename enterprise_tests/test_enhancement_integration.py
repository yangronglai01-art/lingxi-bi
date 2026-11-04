"""Real PG/SR integration; generation and identity remain protocol fixtures."""
import uuid
from datetime import datetime,timezone
from dataclasses import replace

import httpx
import pytest
import pymysql
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from enterprise_tests.test_enterprise import settings,store,alice,claims
from src.enterprise.api import create_app
from src.enterprise.identity import Identity,Denied
from src.enterprise.intent import Clarify
from src.enterprise.worker import once
from src.enterprise.features import feedback,adjudicate
from src.enterprise.store import Store,Conflict,digest
from src.enterprise.loader import load
from src.enterprise.recovery import reconcile,quarantine


def complete(settings,store,alice):
    jid=store.submit(alice,'各型号净销售额',uuid.uuid4().hex)
    assert once(settings,store)
    assert store.get(alice,jid)['result']['status']=='ok'
    return jid


def test_followup_authorization_expiry_and_release_binding(settings,store,alice):
    parent=complete(settings,store,alice)
    child=store.submit(alice,'按渠道看',uuid.uuid4().hex,parent)
    assert once(settings,store)
    assert store.get(alice,child)['result']['intent']['group']=='channel'
    bob=store.principal('petkit','bob')
    with pytest.raises(Denied):store.submit(bob,'按渠道看',uuid.uuid4().hex,parent)
    with store.db() as c:c.execute("UPDATE analytics_jobs SET updated=clock_timestamp()-interval '2 hours' WHERE id=%s",(parent,))
    with pytest.raises(Clarify):store.submit(alice,'按渠道看',uuid.uuid4().hex,parent)


def test_feedback_replay_adjudication_and_cross_user(settings,store,alice):
    parent=complete(settings,store,alice)
    restricted_writer=Store(settings.dsn.replace('analytics_qa:analytics_qa_only','analytics_runtime:analytics_runtime_qa_only'))
    first=feedback(restricted_writer,alice,parent,'negative','metric','QA-only issue')
    assert feedback(store,alice,parent,'negative','metric','QA-only issue')==first
    with pytest.raises(Conflict):feedback(store,alice,parent,'positive','correct','different')
    with pytest.raises(Denied):feedback(store,store.principal('petkit','bob'),parent,'negative','metric','')
    restricted=Store(settings.dsn.replace('analytics_qa:analytics_qa_only','analytics_runtime:analytics_runtime_qa_only'))
    import psycopg2
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):adjudicate(restricted,'petkit',first['id'],'QA reviewer','QA approval','confirmed_issue')
    adjudicate(store,'petkit',first['id'],'QA reviewer','QA approval','confirmed_issue')
    with pytest.raises(Conflict):adjudicate(store,'petkit',first['id'],'QA reviewer','QA approval','confirmed_issue')


def test_http_feedback_and_clarification(settings,store,alice):
    identity=Identity(settings,store,transport=httpx.MockTransport(lambda r:httpx.Response(200,json=claims())))
    client=TestClient(create_app(settings,store,identity),raise_server_exceptions=False)
    parent=complete(settings,store,alice);headers={'Authorization':'Bearer QA'}
    assert client.post('/api/v1/analytics/queries/'+parent+'/feedback',headers=headers,json=dict(vote='positive',category='correct',comment='QA')).status_code==201
    h=dict(headers,**{'Idempotency-Key':uuid.uuid4().hex})
    assert client.post('/api/v1/analytics/queries',headers=h,json=dict(question='只看华东',previous_job_id=parent)).status_code==422


def test_real_reconcile_unknown_state_snapshot(settings,store):
    release='recover_'+uuid.uuid4().hex[:10]
    catalog=dict(version='qa-recovery',as_of='2026-10-16',synthetic=True,metrics=[],relationships=[],tables={'qa_data':dict(name='qa_data',domain='sales',columns={'id':{'type':'BIGINT'},'amount':{'type':'DECIMAL(20,2)'}})})
    rows={'qa_data':[{'id':1,'amount':'12.03'}]};keys={'qa_data':['id']}
    watermark=dict(source_kind='synthetic-integration',source_version=1,event_time='2026-10-16T00:00:00+08:00',ingested_at=datetime.now(timezone.utc).isoformat(),batch_id=release)
    s=replace(settings,scopes={'global':settings.scopes['global']})
    with pymysql.connect(host=s.scopes['global']['host'],port=s.scopes['global']['port'],user='root',autocommit=True) as admin:
        proof=load(s,store,admin,release,catalog,rows,keys,watermark)
        # Inject the crash window after staging, before the load ledger completion.
        with store.db() as c:c.execute("UPDATE analytics_loads SET state='pending',proof=NULL WHERE tenant=%s AND release=%s",(s.tenant,release))
        recovered=reconcile(s,store,admin,release,catalog,rows,keys,watermark,'isolated QA recovery')
        assert recovered['manifest_hash']==proof['manifest_hash']
        with pytest.raises(Conflict):reconcile(s,store,admin,release,catalog,rows,keys,watermark,'QA repeat')


def test_quarantine_does_not_delete_tables(settings,store):
    release='quarantine_'+uuid.uuid4().hex[:10];manifest='qa-manifest'
    with store.db() as c:
        c.execute("INSERT INTO analytics_loads(tenant,release,manifest_hash,state) VALUES(%s,%s,%s,'pending')",(settings.tenant,release,manifest))
    with pytest.raises(Conflict):quarantine(store,settings.tenant,release,'wrong','QA approval')
    quarantine(store,settings.tenant,release,manifest,'isolated QA approval')
    with store.db() as c:
        c.execute('SELECT state FROM analytics_loads WHERE tenant=%s AND release=%s',(settings.tenant,release));assert c.fetchone()['state']=='quarantined'


@pytest.mark.parametrize('mode,verdict,expected',[('shadow','inconsistent','ok'),('enforce','inconsistent','clarification'),('enforce','consistent','ok')])
def test_review_shadow_and_enforce_are_separate(settings,store,alice,mode,verdict,expected):
    import json
    from src.enterprise.query import Query
    from src.petkit.planning import sales_sql
    s=replace(settings,review_mode=mode,review_alias='semantic-review',review_revision='qa-review-snapshot',review_approval='QA-only not production')
    def gateway(request):
        body=json.loads(request.content)
        if body['model']=='semantic-review':content=dict(verdict=verdict)
        else:content=dict(status='ready',sql=sales_sql(),plan=dict(metric='net_sales',dimensions=['product'],time_range='registered snapshot',filters=[],joins=[],grain='order_item',assumptions=[]))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(content)}}]})
    jid=store.submit(alice,'请查询各型号的净销售额情况',uuid.uuid4().hex)
    assert once(s,store,Query(s,store,transport=httpx.MockTransport(gateway)))
    result=store.get(alice,jid)['result'];assert result['status']==expected
    assert result['semantic_review']['verdict']==verdict


def test_followup_release_change_requires_new_question(settings,store,alice):
    parent=complete(settings,store,alice);original=store.release('petkit');release='followup_'+uuid.uuid4().hex[:10]
    store.stage_release('petkit',release,original['catalog'],original['views'],original['watermark'])
    store.publish('petkit',release,original['id'],'isolated QA release binding test')
    try:
        with pytest.raises(Clarify):store.submit(alice,'按渠道看',uuid.uuid4().hex,parent)
    finally:store.publish('petkit',original['id'],release,'restore QA release')


def test_stage_metrics_exposed_and_runtime_cannot_self_review(settings,store,alice):
    complete(settings,store,alice)
    identity=Identity(settings,store,transport=httpx.MockTransport(lambda r:httpx.Response(200,json=claims())))
    client=TestClient(create_app(settings,store,identity),raise_server_exceptions=False)
    response=client.get('/internal/metrics',headers={'Authorization':'Bearer QA'})
    assert response.status_code==200
    assert 'lingxi_stage_seconds_count' in response.text
    assert 'lingxi_watermark_sla_business_approved 0' in response.text


@pytest.mark.parametrize('load_state',['pending','failed','quarantined'])
def test_incomplete_or_quarantined_staged_snapshot_cannot_publish(settings,store,load_state):
    original=store.release(settings.tenant);release='unpublish_'+uuid.uuid4().hex[:10]
    store.stage_release(settings.tenant,release,original['catalog'],original['views'],original['watermark'])
    with store.db() as c:
        c.execute('INSERT INTO analytics_loads(tenant,release,manifest_hash,state) VALUES(%s,%s,%s,%s)',(settings.tenant,release,'isolated-QA-state-test',load_state))
    with pytest.raises(Conflict):store.publish(settings.tenant,release,original['id'],'isolated QA negative proof')
    assert store.release(settings.tenant)['id']==original['id']


def test_monitoring_identity_cannot_query_business(settings,store):
    token='qa-monitoring-only-random-secret-32bytes'
    s=replace(settings,metrics_token=token)
    # Even an incorrectly permissive IdP cannot promote this token to business identity.
    identity=Identity(s,store,transport=httpx.MockTransport(lambda r:httpx.Response(200,json=claims())))
    client=TestClient(create_app(s,store,identity),raise_server_exceptions=False)
    assert client.get('/internal/metrics',headers={'Authorization':'Bearer '+token}).status_code==200
    assert client.get('/api/v1/analytics/me',headers={'Authorization':'Bearer '+token}).status_code==403
    assert client.get('/internal/metrics',headers={'Authorization':'Bearer employee'}).status_code==403
