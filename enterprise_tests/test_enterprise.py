import os,time,uuid
from dataclasses import replace
from decimal import Decimal
from collections import defaultdict
import httpx,pytest,pymysql
from fastapi.testclient import TestClient
from src.enterprise.settings import Settings
from src.enterprise.store import Store,Conflict
from src.enterprise.identity import Identity,Denied
from src.enterprise.api import create_app
from src.enterprise.query import Query
from src.enterprise.worker import once
from src.enterprise.execution import Executor,bind,wire
from src.petkit.planning import examples
from src.petkit.builder import generate
from src.petkit.reference import expected
from src.petkit.catalog import catalog_payload

@pytest.fixture(scope='session')
def settings():
    assert os.environ.get('LINGXI_QA_CONFIRM')=='lingxi_enterprise_qa'
    scopes={s:dict(host=os.environ.get('LINGXI_QA_SR_HOST','127.0.0.1'),port=int(os.environ.get('LINGXI_QA_SR_PORT','9030')),database='lingxi_enterprise_qa',user='analytics_qa_'+s,regions=[] if s=='global' else ['R1'],password_env='QA_'+s.upper()) for s in ('global','region_r1')}
    for s in scopes:os.environ['QA_'+s.upper()]='analytics_qa_'+s+'_only'
    return Settings('integration',os.environ.get('LINGXI_QA_DSN','postgresql://analytics_qa:analytics_qa_only@127.0.0.1:56103/lingxi_enterprise_qa'),'petkit',
        'https://idp.invalid','analytics','https://idp.invalid/introspect','test-client','test-secret',
        'https://gateway.invalid/v1','test-gateway-key',scopes,enabled_metrics=tuple(m['id'] for m in catalog_payload()['metrics']))

@pytest.fixture
def store(settings):return Store(settings.dsn)

@pytest.fixture
def alice(store):return store.principal('petkit','alice')

def test_production_config_rejects_plaintext(settings):
    with pytest.raises(ValueError):replace(settings,environment='production')

def test_decimal():assert wire(Decimal('1234567890.01'))=='1234567890.01'

def test_binder_cte():
    actual=bind('WITH fact_order AS (SELECT product_id FROM dim_product) SELECT product_id FROM fact_order',{'dim_product':'g_dim_product'})
    assert 'g_dim_product' in actual and 'g_fact_order' not in actual

def claims(subject='alice'):
    return dict(active=True,iss='https://idp.invalid',aud=['analytics'],tenant_id='petkit',sub=subject,
        exp=time.time()+120,resource_access={'analytics':{'roles':['analytics_reader']}})

@pytest.mark.parametrize('field,value',[('active',False),('iss','wrong'),('aud',['wrong']),('tenant_id','other'),('exp',0),('resource_access',{})])
def test_identity_fail_closed(settings,store,field,value):
    c=claims();c[field]=value
    identity=Identity(settings,store,transport=httpx.MockTransport(lambda _:httpx.Response(200,json=c)))
    with pytest.raises(Denied):identity.resolve('test')

def test_http_contract(settings,store):
    def introspection(request):return httpx.Response(200,json=claims('bob' if b'token=bob' in request.content else 'alice'))
    identity=Identity(settings,store,transport=httpx.MockTransport(introspection))
    client=TestClient(create_app(settings,store,identity),raise_server_exceptions=False)
    assert client.get('/api/v1/analytics/queries').status_code==401
    headers={'Authorization':'Bearer alice','Idempotency-Key':uuid.uuid4().hex}
    assert client.post('/api/v1/analytics/queries',headers=headers,json={'question':'各型号净销售额','role':'admin'}).status_code==422
    r=client.post('/api/v1/analytics/queries',headers=headers,json={'question':'各型号净销售额'})
    assert r.status_code==202
    r2=client.post('/api/v1/analytics/queries',headers=headers,json={'question':'各型号净销售额'})
    assert r.json()['id']==r2.json()['id']
    assert client.post('/api/v1/analytics/queries',headers=headers,json={'question':'其他问题'}).status_code==409
    url=r.json()['status_url']
    assert client.get(url,headers={'Authorization':'Bearer bob'}).status_code==403
    assert client.post(url+'/cancel',headers=headers).json()['state']=='cancelled'

def test_revocation_fencing(store,alice):
    subject='revoke_'+uuid.uuid4().hex
    store.grant('petkit',subject,'analyst',[],'global');p=store.principal('petkit',subject)
    jid=store.submit(p,'各型号净销售额',uuid.uuid4().hex)
    store.grant('petkit',subject,'analyst',[],'global',enabled=False)
    with pytest.raises(Denied):store.get(p,jid)
    with store.db() as c:c.execute("UPDATE analytics_jobs SET state='cancelled' WHERE id=%s",(jid,))
    jid=store.submit(alice,'各型号净销售额',uuid.uuid4().hex);job=store.claim('lease-test')
    assert job['id']==jid
    with store.db() as c:c.execute("UPDATE analytics_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(jid,))
    assert not store.heartbeat(job)
    with pytest.raises(Conflict):store.finish(job,alice,{'status':'ok'})
    assert store.claim('recovery') is None
    assert store.get(alice,jid)['state']=='interrupted'

def test_database_scope(settings):
    with Executor(settings).connect('region_r1') as connection:
        with connection.cursor() as c:
            c.execute('SELECT DISTINCT region_id FROM r1_fact_order');assert c.fetchall()==(('R1',),)
            for sql in ('SELECT * FROM b_fact_order LIMIT 1','SELECT * FROM g_fact_order LIMIT 1','DELETE FROM r1_fact_order','SELECT * FROM petkit500.fact_order LIMIT 1'):
                with pytest.raises(pymysql.MySQLError):c.execute(sql)

@pytest.fixture(scope='session')
def raw():
    catalog=catalog_payload();source=generate()
    return {n:[dict(zip(catalog['tables'][n]['columns'],r)) for r in rows] for n,rows in source.items()}

def decimal_sales(case,raw):
    orders={r['order_id']:r for r in raw['fact_order']};sku={r['sku_id']:r['product_id'] for r in raw['dim_sku']}
    products={r['product_id']:r['product_name'] for r in raw['dim_product']};channels={r['channel_id']:r['channel_name'] for r in raw['dim_channel']}
    refunds=defaultdict(Decimal);totals=defaultdict(Decimal)
    for r in raw['fact_refund']:refunds[r['item_id']]+=Decimal(str(r['amount']))
    for r in raw['fact_order_item']:
        o=orders[r['order_id']];month=o['paid_date'].strftime('%Y-%m')
        if case['month'] and case['month']!=month:continue
        key=products[sku[r['sku_id']]] if case['group']=='model' else channels[o['channel_id']] if case['group']=='channel' else month
        totals[key]+=Decimal(str(r['amount']))-refunds[r['item_id']]
    return {k:(v.quantize(Decimal('.01')),) for k,v in totals.items()}

@pytest.mark.parametrize('case',examples(),ids=lambda c:c['question'])
def test_37_approved(settings,store,alice,raw,case):
    jid=store.submit(alice,case['question'],uuid.uuid4().hex)
    assert once(settings,store)
    row=store.get(alice,jid)
    assert row['state']=='completed',row['error_code']
    answer=row['result'];assert answer['status']=='ok',answer
    assert answer['generation_mode']=='approved_template'
    assert answer['result']['query_id']
    got={r[0]:tuple(r[1:]) for r in answer['result']['rows']}
    target=decimal_sales(case,raw) if case['metric_id']=='net_sales' else expected(case,raw)
    assert got.keys()==target.keys()
    for key in target:
        assert len(got[key])==len(target[key])
        for actual,want in zip(got[key],target[key]):assert abs(Decimal(str(actual))-Decimal(str(want)))<=Decimal('.005')

@pytest.mark.parametrize('sql',['SELECT @@version FROM fact_order','SELECT /*+ SET_VAR(query_mem_limit=0) */ * FROM fact_order','SELECT * FROM mysql.user','SELECT * FROM other.fact_order','DROP TABLE fact_order','SELECT * FROM fact_order; DELETE FROM fact_order','SELECT sleep(100) FROM fact_order','SELECT * FROM fact_order CROSS JOIN fact_order_item'])
def test_guard(settings,store,alice,sql):
    from src.petkit.guard import check
    q=Query(settings,store);r=store.release('petkit');metric=r['catalog']['metrics'][0]
    context=q.context(alice,r,'各型号净销售额',metric)
    with pytest.raises((PermissionError,ValueError)):check(sql,context)

# Real-engine cancellation and bounded-result evidence, not model-quality tests.
def test_sql_physical_cancel(settings):
    import threading
    executor=Executor(replace(settings,query_seconds=4));captured=[];start=time.monotonic()
    with pytest.raises(TimeoutError):
        executor.run('global','SELECT sleep(20) FROM g_dim_product LIMIT 1',lambda:time.monotonic()-start<.5,captured.append)
    assert .4<time.monotonic()-start<8
    assert captured
    with executor.connect('global') as connection:
        with connection.cursor() as c:
            c.execute('SHOW PROCESSLIST')
            active=[r for r in c.fetchall() if r[0]==captured[0]]
            assert not active or all(r[5]!='Query' for r in active)

def test_stream_budgets(settings):
    result=Executor(replace(settings,max_rows=2)).run('global','SELECT * FROM g_dim_product')
    assert result['row_count']==2 and result['truncated']

def test_cdc_replay_order_delete_schema(store):
    from src.enterprise.ingestion import Batches
    b=Batches(store);b.migrate();source='qa_'+uuid.uuid4().hex
    schema={'fact_order':['order_id','amount']}
    def apply(version,op='upsert',amount='10.01'):
        batch='batch_'+uuid.uuid4().hex
        e=dict(table='fact_order',key='o1',source_version=version,op=op,event_time='2026-10-23T10:00:00+08:00',after={'order_id':'o1','amount':amount} if op=='upsert' else None)
        w=dict(source_version=version,event_time=e['event_time'],batch_id=batch)
        assert b.apply('petkit',source,batch,[e],schema,w)=='pending'
        assert b.apply('petkit',source,batch,[e],schema,w)=='pending'
        with pytest.raises(Conflict):b.apply('petkit',source,batch,[dict(e,source_version=99)],schema,w)
    apply(2);apply(1,amount='9.00');apply(3,'delete');apply(2)
    with store.db() as c:
        c.execute('SELECT * FROM analytics_source_rows WHERE source=%s',(source,));r=c.fetchone()
        assert r['source_version']==3 and r['deleted']
    with pytest.raises(ValueError):
        b.apply('petkit',source,'bad_batch',[dict(table='fact_order',key='o2',source_version=4,op='upsert',event_time='2026-10-23T10:00:00+08:00',after={'order_id':'o2','new_column':1})],schema,dict(source_version=4,event_time='x',batch_id='bad_batch'))

def _claim_process(dsn,owner,queue):
    job=Store(dsn).claim(owner)
    queue.put(job['id'] if job else None)

def test_two_worker_processes(store,alice):
    import multiprocessing
    ids={store.submit(alice,'各型号净销售额',uuid.uuid4().hex) for _ in range(2)}
    ctx=multiprocessing.get_context('spawn');queue=ctx.Queue()
    processes=[ctx.Process(target=_claim_process,args=(store.dsn,'worker'+str(i),queue)) for i in range(2)]
    for p in processes:p.start()
    for p in processes:p.join(15);assert p.exitcode==0
    assert {queue.get(timeout=2) for _ in processes}==ids
    for jid in ids:store.cancel(alice,jid)

def test_model_private_protocol(settings,store,alice):
    def gateway(request):
        import json
        body=json.loads(request.content)
        assert body['model']=='local-chat'
        assert '/chat/completions' in str(request.url)
        assert 'g_fact_order' not in request.content.decode()
        from src.petkit.planning import sales_sql
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(dict(status='ready',sql=sales_sql(),plan=dict(metric='net_sales',dimensions=['product'],time_range='registered snapshot',filters=[],joins=[],grain='order_item',assumptions=[])))}}]})
    jid=store.submit(alice,'请查询各型号的净销售额情况',uuid.uuid4().hex)
    assert once(settings,store,Query(settings,store,transport=httpx.MockTransport(gateway)))
    r=store.get(alice,jid)
    assert r['state']=='completed' and r['result']['generation_mode']=='local-chat'
    assert r['result']['result']['row_count']==12

def test_pg_runtime_role(settings,store,alice):
    import psycopg2
    restricted=Store(settings.dsn.replace('analytics_qa:analytics_qa_only','analytics_runtime:analytics_runtime_qa_only'))
    restricted.assert_runtime_role()
    for operation in (lambda:restricted.grant('petkit','forbidden','analyst',[],'global'),lambda:restricted.migrate()):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):operation()
    jid=restricted.submit(alice,'各型号净销售额',uuid.uuid4().hex)
    assert once(settings,restricted)
    assert restricted.get(alice,jid)['result']['status']=='ok'

def test_bge_protocol_and_version(settings,store,alice):
    import json
    from src.enterprise.vectors import build,search,embedding
    r=store.release('petkit');release='vectors_'+uuid.uuid4().hex
    store.stage_release('petkit',release,r['catalog'],r['views'],r['watermark'])
    def gateway(request):
        body=json.loads(request.content);assert body['model']=='embedding-default'
        return httpx.Response(200,json={'data':[dict(index=i,embedding=[1.0]+[0.0]*1023) for i,_ in enumerate(body['input'])]})
    transport=httpx.MockTransport(gateway)
    build(settings,store,release,'test-vector-revision',transport)
    pinned=dict(r,id=release)
    names=search(settings,store,pinned,'净销售额',{'fact_order','dim_product'},'test-vector-revision',transport)
    assert set(names)=={'fact_order','dim_product'}
    with pytest.raises(Conflict):search(settings,store,pinned,'净销售额',{'fact_order'},'other-revision',transport)
    bad=httpx.MockTransport(lambda _:httpx.Response(200,json={'data':[{'index':0,'embedding':[0.0]*1024}]}))
    with pytest.raises(ValueError):embedding(settings,['test'],bad)

def test_output_contract():
    from src.enterprise.contract import validate
    with pytest.raises(ValueError):validate({'id':'activation30'},{'columns':['devices','matched_devices','rate_percent'],'rows':[[100,20,'0.2']]})
    validate({'id':'activation30'},{'columns':['devices','matched_devices','rate_percent'],'rows':[[100,20,'20.00']]})

def test_snapshot_source_quality():
    from src.enterprise.loader import source_quality
    catalog={'tables':{'orders':{'columns':{'id':{'type':'VARCHAR'},'amount':{'type':'DECIMAL(20,2)'}}}},'relationships':[]}
    with pytest.raises(ValueError):source_quality(catalog,{'orders':[{'id':'1','amount':1.01}]},{'orders':['id']})
    with pytest.raises(ValueError):source_quality(catalog,{'orders':[{'id':'1','amount':'1.01'},{'id':'1','amount':'1.01'}]},{'orders':['id']})

def test_real_incremental_materialization(settings,store):
    from src.enterprise.loader import load
    from src.enterprise.ingestion import Batches
    only=replace(settings,scopes={'global':settings.scopes['global']});b=Batches(store);b.migrate()
    source='load_'+uuid.uuid4().hex
    catalog=dict(version='qa-source-1',as_of='2026-10-23',synthetic=True,metrics=[],relationships=[],tables={
        'qa_fact':dict(name='qa_fact',domain='shared',description='isolated source contract',grain='one row per id',core=True,aliases=[],columns={'id':{'type':'VARCHAR'},'amount':{'type':'DECIMAL(20,2)'},'quantity':{'type':'BIGINT'}})})
    release='load_'+uuid.uuid4().hex;stamp='2026-10-23T12:00:00+08:00';batch='batch_'+uuid.uuid4().hex
    events=[dict(table='qa_fact',key='f1',op='upsert',source_version=1,event_time=stamp,after={'id':'f1','amount':'10.01','quantity':1}),
        dict(table='qa_fact',key='f1',op='upsert',source_version=2,event_time=stamp,after={'id':'f1','amount':'12.03','quantity':2}),
        dict(table='qa_fact',key='f2',op='upsert',source_version=3,event_time=stamp,after={'id':'f2','amount':'5.00','quantity':1}),
        dict(table='qa_fact',key='f2',op='delete',source_version=4,event_time=stamp,after=None)]
    watermark=dict(batch_id=batch,source_version=4,event_time=stamp,ingested_at=stamp,source_kind='synthetic-integration')
    schema={'qa_fact':['id','amount','quantity']}
    b.apply('petkit',source,batch,events,schema,watermark)
    with store.db() as c:
        c.execute('SELECT payload FROM analytics_source_rows WHERE tenant=%s AND source=%s AND NOT deleted',('petkit',source))
        snapshot={'qa_fact':[r['payload'] for r in c.fetchall()]}
    admin=pymysql.connect(host=os.environ.get('LINGXI_QA_SR_HOST','127.0.0.1'),port=int(os.environ.get('LINGXI_QA_SR_PORT','9030')),user='root',autocommit=True)
    try:proof=load(only,store,admin,release,catalog,snapshot,{'qa_fact':['id']},watermark)
    finally:admin.close()
    assert proof['decimal_totals']['qa_fact.amount']=='12.03'
    b.materialized('petkit',source,batch,release,proof)
    original=store.release('petkit')['id']
    try:
        store.publish('petkit',release,original,'isolated incremental reconciliation')
        published=store.release('petkit');physical=published['views']['global']['qa_fact']
        result=Executor(only).run('global','SELECT amount,quantity FROM `'+physical+'`')
        assert result['rows']==[['12.03',2]]
    finally:store.publish('petkit',original,release,'restore previous QA release after integration proof')

def test_canonical_api_engine_roundtrip(settings,store):
    identity=Identity(settings,store,transport=httpx.MockTransport(lambda _:httpx.Response(200,json=claims())))
    client=TestClient(create_app(settings,store,identity),raise_server_exceptions=False)
    headers={'Authorization':'Bearer alice','Idempotency-Key':uuid.uuid4().hex}
    response=client.post('/api/v1/analytics/queries',headers=headers,json={'question':'各型号净销售额'})
    assert response.status_code==202
    # New Store and worker instance represent a restart, with no in-memory job state.
    assert once(settings,Store(settings.dsn))
    result=client.get(response.json()['status_url']+'/result',headers=headers)
    assert result.status_code==200
    answer=result.json();assert answer['status']=='ok' and answer['result']['row_count']==12
    assert all(isinstance(r[1],str) for r in answer['result']['rows'])
    assert client.get('/health/ready').status_code==200
    assert client.post('/api/v1/analytics/ask',headers=headers,json={'question':'bypass'}).status_code==404
    assert client.get('/api/v1/analytics/schema?limit=2',headers=headers).json()['total']==500
    assert client.get('/config.js').status_code==200
    assert 'lingxi_completed_jobs' in client.get('/internal/metrics',headers=headers).text

def test_no_legacy_runtime_imports():
    import sys
    assert 'duckdb' not in sys.modules
    assert 'src.engine' not in sys.modules
    assert 'src.petkit.releases' not in sys.modules
    assert 'src.petkit.service' not in sys.modules

def test_clarification_response_contract(settings,store,alice):
    jid=store.submit(alice,'激活率',uuid.uuid4().hex)
    assert once(settings,store)
    answer=store.get(alice,jid)['result']
    assert answer['status']=='clarification' and answer['insights']==[] and answer['result'] is None

def test_readiness_rejects_missing_index(settings,store):
    client=TestClient(create_app(replace(settings,embedding_revision='missing'),store),raise_server_exceptions=False)
    assert client.get('/health/ready').status_code==503

def test_regional_snapshot_materialization(settings,store):
    from src.enterprise.loader import load
    release='region_'+uuid.uuid4().hex
    columns=lambda **types:{k:{'type':v} for k,v in types.items()}
    catalog=dict(version='regional-qa',as_of='2026-10-23',synthetic=True,metrics=[],relationships=[{'left':'fact_receipt','left_key':'warehouse_id','right':'dim_warehouse','right_key':'warehouse_id'}],tables={
        'dim_warehouse':{'columns':columns(warehouse_id='VARCHAR',warehouse_name='VARCHAR',region_id='VARCHAR')},
        'fact_receipt':{'columns':columns(receipt_id='VARCHAR',warehouse_id='VARCHAR',quantity='BIGINT')}})
    rows={'dim_warehouse':[dict(warehouse_id='W1',warehouse_name='one',region_id='R1'),dict(warehouse_id='W2',warehouse_name='two',region_id='R2')],
        'fact_receipt':[dict(receipt_id='rc1',warehouse_id='W1',quantity=2),dict(receipt_id='rc2',warehouse_id='W2',quantity=3)]}
    keys={'dim_warehouse':['warehouse_id'],'fact_receipt':['receipt_id']}
    stamp='2026-10-23T00:00:00+08:00';watermark=dict(source_kind='synthetic-integration',source_version=1,batch_id=release,event_time=stamp,ingested_at=stamp)
    admin=pymysql.connect(host=os.environ.get('LINGXI_QA_SR_HOST','127.0.0.1'),port=int(os.environ.get('LINGXI_QA_SR_PORT','9030')),user='root',autocommit=True)
    try:proof=load(settings,store,admin,release,catalog,rows,keys,watermark)
    finally:admin.close()
    assert proof['source_counts']['region_r1']['fact_receipt']==1
    with store.db() as c:
        c.execute('SELECT views FROM analytics_releases WHERE tenant=%s AND id=%s',('petkit',release));views=c.fetchone()['views']
    result=Executor(settings).run('region_r1','SELECT quantity FROM `'+views['region_r1']['fact_receipt']+'`')
    assert result['rows']==[[2]]
    with Executor(settings).connect('region_r1') as con:
        with con.cursor() as c:
            with pytest.raises(pymysql.MySQLError):c.execute('SELECT * FROM `'+views['global']['fact_receipt']+'`')

def test_real_session_resource_budgets(settings):
    result=Executor(settings).run('global','SELECT @@query_mem_limit,@@pipeline_dop FROM g_dim_product LIMIT 1')
    assert result['rows']==[[settings.query_memory_bytes,1]]

def test_transactional_histogram(settings,store,alice):
    with store.db() as c:
        c.execute("SELECT observations FROM analytics_histograms WHERE tenant='petkit' AND metric='sql' AND bucket='+Inf'");r=c.fetchone();before=r['observations'] if r else 0
    jid=store.submit(alice,'各型号净销售额',uuid.uuid4().hex)
    assert once(settings,store)
    assert store.get(alice,jid)['state']=='completed'
    with store.db() as c:
        c.execute("SELECT observations FROM analytics_histograms WHERE tenant='petkit' AND metric='sql' AND bucket='+Inf'");assert c.fetchone()['observations']==before+1

def test_partial_load_not_publishable_and_rebuild(settings,store):
    from src.enterprise.loader import load
    from src.enterprise.store import digest
    from contextlib import contextmanager
    release='failed_'+uuid.uuid4().hex;previous=store.release('petkit')['id'];only=replace(settings,scopes={'global':settings.scopes['global']})
    catalog=dict(version='failure-qa',as_of='2026-10-23',synthetic=True,relationships=[],metrics=[],tables={n:{'columns':{'id':{'type':'VARCHAR'}}} for n in ('qa_one','qa_two')})
    rows={n:[{'id':'one'}] for n in catalog['tables']};keys={n:['id'] for n in rows};stamp='2026-10-23T00:00:00+08:00'
    watermark=dict(source_kind='synthetic-integration',source_version=1,batch_id=release,event_time=stamp,ingested_at=stamp)
    admin=pymysql.connect(host=os.environ.get('LINGXI_QA_SR_HOST','127.0.0.1'),port=int(os.environ.get('LINGXI_QA_SR_PORT','9030')),user='root',autocommit=True)
    class Cursor:
        def __init__(self,real):self.real=real
        def __getattr__(self,name):return getattr(self.real,name)
        def execute(self,sql,*args):
            if sql.startswith('CREATE TABLE') and 'qa_two' in sql:raise RuntimeError('injected crash after first real table loaded')
            return self.real.execute(sql,*args)
    class FailingConnection:
        @contextmanager
        def cursor(self):
            with admin.cursor() as c:yield Cursor(c)
    try:
        with pytest.raises(RuntimeError):load(only,store,FailingConnection(),release,catalog,rows,keys,watermark)
        assert store.release('petkit')['id']==previous
        with pytest.raises(Conflict):store.release('petkit',release)
        with store.db() as c:
            c.execute('SELECT state FROM analytics_loads WHERE tenant=%s AND release=%s',('petkit',release));assert c.fetchone()['state']=='failed'
        private='v'+digest(['petkit',release])[:12]+'_qa_one'
        with Executor(settings).connect('global') as con:
            with con.cursor() as c:
                with pytest.raises(pymysql.MySQLError):c.execute('SELECT * FROM `'+private+'`')
        proof=load(only,store,admin,'rebuilt_'+uuid.uuid4().hex,catalog,rows,keys,watermark)
        assert proof['passed'] and proof['source_counts']['global']=={'qa_one':1,'qa_two':1}
    finally:admin.close()


def test_withdraw_active_release_fences_jobs_and_results(store):
    tenant = 'withdraw_' + uuid.uuid4().hex
    store.grant(tenant, 'operator_test', 'analyst', [], 'global')
    principal = store.principal(tenant, 'operator_test')
    for version, previous in [('v1', None), ('v2', 'v1')]:
        store.stage_release(tenant, version, {}, {'global': {}}, {})
        store.publish(tenant, version, previous, 'isolated withdrawal regression')
        if version == 'v1':
            old_job = store.submit(principal, 'old version query', 'old')
    assert store.get(principal, old_job)['state'] == 'queued'
    old_claim = store.claim('old-owner', tenant)
    store.finish(old_claim, principal, {'status': 'clarification'})
    completed_id = store.submit(principal, 'completed query', 'completed')
    completed = store.claim('completed-owner', tenant)
    assert completed['id'] == completed_id
    store.finish(completed, principal, {'status': 'clarification', 'result': 'invalid'})
    running_id = store.submit(principal, 'running query', 'running')
    running = store.claim('running-owner', tenant)
    assert running['id'] == running_id
    store.executing(running, 31415)
    queued = store.submit(principal, 'queued query', 'queued')
    with pytest.raises(Conflict):
        store.withdraw_active_release(tenant, 'v2', 'missing', 'invalid replacement')
    assert store.release(tenant)['id'] == 'v2'
    store.withdraw_active_release(tenant, 'v2', 'v1', 'incorrect calculation')
    assert store.release(tenant)['id'] == 'v1'
    assert store.get(principal, old_job)['state'] == 'completed'
    for job_id in [completed_id, running_id, queued]:
        job = store.get(principal, job_id)
        assert job['state'] == 'cancelled'
        assert job['error_code'] == 'RELEASE_WITHDRAWN'
        assert job['result'] is None
    inspected = store.inspect_job(tenant, running_id)
    assert inspected['connection_id'] == 31415
    assert inspected['release'] == 'v2' and inspected['state'] == 'cancelled'
    assert 'result' not in inspected and 'question' not in inspected
    with pytest.raises(Conflict):
        store.inspect_job('another_tenant', running_id)
    assert not store.running(running)
    with pytest.raises(Conflict):
        store.finish(running, principal, {'status': 'clarification'})
    with pytest.raises(Conflict):
        store.release(tenant, 'v2')
    new_id = store.submit(principal, 'new query', 'new')
    assert store.get(principal, new_id)['release'] == 'v1'
