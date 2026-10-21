"""Local integration and invariant tests; result oracle uses Python, not template SQL."""
import json
import os
import sys
from collections import defaultdict
from datetime import date,timedelta
from pathlib import Path

os.environ['PROJECT_PROFILE']='petkit'
os.environ['PETKIT_MODE']='demo'
os.environ.pop('PETKIT_EMBEDDING_MODEL',None)

import duckdb
import pytest
from src.engine.engine import QueryEngine
from src.petkit.access import Principal
from src.petkit.catalog import METRICS
from src.petkit.guard import check
from src.petkit.planning import examples
from src.petkit.retrieval import current_catalog,retrieve
from src.petkit.settings import artifacts,AS_OF


@pytest.fixture(scope='session')
def catalog(): return current_catalog()


@pytest.fixture(scope='session')
def raw(catalog):
    folder,_=artifacts()
    conn=duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True)
    try:
        tables={}
        for name,t in catalog['tables'].items():
            if t['core']:
                cur=conn.execute(f'SELECT * FROM "{name}"')
                cols=[c[0] for c in cur.description]
                tables[name]=[dict(zip(cols,row)) for row in cur.fetchall()]
        return tables
    finally: conn.close()


@pytest.fixture(scope='session')
def context(catalog):
    return dict(tables=catalog['tables'],relationships=catalog['relationships'])


def expected(case,raw):
    """Independent arithmetic over raw records, no joins/aggregations via template SQL."""
    products={r['product_id']:r['product_name'] for r in raw['dim_product']}
    devices={r['device_id']:r['product_id'] for r in raw['dim_device']}
    items={r['item_id']:r for r in raw['fact_order_item']}
    orders={r['order_id']:r for r in raw['fact_order']}
    metric,group,month=case['metric_id'],case['group'],case['month']
    def included(dt): return not month or dt.strftime('%Y-%m')==month
    output={}
    if metric=='net_sales':
        sku={r['sku_id']:r['product_id'] for r in raw['dim_sku']}
        channels={r['channel_id']:r['channel_name'] for r in raw['dim_channel']}
        refunds=defaultdict(float)
        for r in raw['fact_refund']: refunds[r['item_id']]+=r['amount']
        totals=defaultdict(float)
        for r in raw['fact_order_item']:
            o=orders[r['order_id']]
            if included(o['paid_date']):
                key=(products[sku[r['sku_id']]] if group=='model' else channels[o['channel_id']] if group=='channel' else o['paid_date'].strftime('%Y-%m'))
                totals[key]+=r['amount']-refunds[r['item_id']]
        output={key:(round(value,2),) for key,value in totals.items()}
    elif metric in {'activation30','service30'}:
        events=defaultdict(list)
        source,field=('fact_device_activation','activation_date') if metric=='activation30' else ('fact_service_ticket','created_date')
        for r in raw[source]: events[r['device_id']].append(r[field])
        cohorts=defaultdict(set); hits=defaultdict(set)
        cutoff=date.fromisoformat(AS_OF)-timedelta(days=30)
        for r in raw['bridge_order_device']:
            paid=orders[items[r['item_id']]['order_id']]['paid_date']
            if paid<=cutoff and included(paid):
                key=products[devices[r['device_id']]]
                cohorts[key].add(r['device_id'])
                if any(0<=(dt-paid).days<=30 for dt in events[r['device_id']]): hits[key].add(r['device_id'])
        output={key:(len(ids),len(hits[key]),round(len(hits[key])*100/len(ids),2)) for key,ids in cohorts.items()}
    elif metric=='offline_rate':
        names={r['firmware_id']:r['firmware_name'] for r in raw['dim_firmware']}
        totals=defaultdict(lambda:[0,0])
        for r in raw['fact_device_day']:
            if included(r['event_date']):
                key=names[r['firmware_id']] if group=='firmware' else products[devices[r['device_id']]]
                totals[key][0]+=1; totals[key][1]+=int(not r['online'])
        output={key:(value[0],round(value[1]*100/value[0],2)) for key,value in totals.items()}
    elif metric=='tickets':
        faults={r['fault_id']:r['fault_name'] for r in raw['dim_fault']}
        totals=defaultdict(int)
        for r in raw['fact_service_ticket']:
            if included(r['created_date']): totals[faults[r['fault_id']] if group=='fault' else products[devices[r['device_id']]]]+=1
        output={key:(value,) for key,value in totals.items()}
    elif metric=='received_units':
        warehouses={r['warehouse_id']:r['warehouse_name'] for r in raw['dim_warehouse']}
        totals=defaultdict(int)
        for r in raw['fact_receipt']:
            if included(r['receipt_date']): totals[warehouses[r['warehouse_id']]]+=r['quantity']
        output={key:(value,) for key,value in totals.items()}
    elif metric=='inventory':
        sku={r['sku_id']:r['product_id'] for r in raw['dim_sku']}
        latest={}
        for r in raw['fact_inventory_day']:
            if included(r['stock_date']):
                key=(r['sku_id'],r['warehouse_id'])
                if key not in latest or r['stock_date']>latest[key]['stock_date']: latest[key]=r
        totals=defaultdict(int)
        for r in latest.values(): totals[products[sku[r['sku_id']]]]+=r['quantity']
        output={key:(value,) for key,value in totals.items()}
    return output


def test_physical_catalog_counts(catalog):
    folder,_=artifacts()
    summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    conn=duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True)
    try:
        names={r[0] for r in conn.execute('SHOW TABLES').fetchall()}
        assert names==set(catalog['tables'])
        assert len(names)==500
        assert sum(t['core'] for t in catalog['tables'].values())==40
        for name,t in catalog['tables'].items():
            cols=conn.execute(f'DESCRIBE "{name}"').fetchall()
            assert [c[0] for c in cols]==list(t['columns'])
            assert conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]==summary['counts'][name]>0
    finally: conn.close()


def test_core_referential_integrity(raw,catalog):
    for edge in catalog['relationships']:
        if edge['left'] in raw and edge['right'] in raw:
            target={r[edge['right_key']] for r in raw[edge['right']]}
            assert all(r[edge['left_key']] in target for r in raw[edge['left']]),edge


def test_order_payment_refund_consistency(raw):
    totals=defaultdict(float)
    for r in raw['fact_order_item']: totals[r['order_id']]+=r['amount']
    for r in raw['fact_order']: assert totals[r['order_id']]==pytest.approx(r['paid_amount'],abs=.001)
    paid={r['order_id']:r['paid_amount'] for r in raw['fact_order']}
    for r in raw['fact_payment']: assert r['amount']==paid[r['order_id']]
    amounts={r['item_id']:r['amount'] for r in raw['fact_order_item']}
    refunded=defaultdict(float)
    for r in raw['fact_refund']: refunded[r['item_id']]+=r['amount']
    assert all(0<=v<=amounts[k] for k,v in refunded.items())


def test_bridge_quantity_and_observation_dates(raw):
    devices=[r['device_id'] for r in raw['bridge_order_device']]
    assert len(devices)==len(set(devices))
    counts=defaultdict(int)
    for r in raw['bridge_order_device']: counts[r['item_id']]+=1
    assert all(counts[r['item_id']]==r['quantity'] for r in raw['fact_order_item'])
    orders={r['order_id']:r['paid_date'] for r in raw['fact_order']}
    items={r['item_id']:r['order_id'] for r in raw['fact_order_item']}
    sale={r['device_id']:orders[items[r['item_id']]] for r in raw['bridge_order_device']}
    for name,col in [('fact_device_activation','activation_date'),('fact_service_ticket','created_date')]:
        assert all(sale[r['device_id']]<=r[col]<=date.fromisoformat(AS_OF) for r in raw[name])


def test_ticket_current_status_matches_history(raw):
    latest={}
    for r in raw['fact_ticket_status']:
        if r['ticket_id'] not in latest or r['event_date']>latest[r['ticket_id']]['event_date']: latest[r['ticket_id']]=r
    assert all(r['status']==latest[r['ticket_id']]['status'] for r in raw['fact_service_ticket'])


def test_firmware_day_matches_successful_upgrade(raw):
    upgrades=defaultdict(list)
    for r in raw['fact_firmware_upgrade']:
        if r['success']: upgrades[r['device_id']].append(r)
    for row in raw['fact_device_day']:
        prior=[u for u in upgrades[row['device_id']] if u['upgrade_date']<=row['event_date']]
        if prior: assert row['firmware_id']==max(prior,key=lambda u:u['upgrade_date'])['firmware_id']


def test_inventory_snapshot_matches_ledger(raw):
    movement=defaultdict(list)
    for r in raw['fact_stock_movement']: movement[(r['sku_id'],r['warehouse_id'])].append(r)
    for r in raw['fact_inventory_day']:
        balance=sum(m['quantity'] for m in movement[(r['sku_id'],r['warehouse_id'])] if m['movement_date']<=r['stock_date'])
        assert balance==r['quantity']>=0


@pytest.mark.parametrize('case',examples(),ids=lambda x:x['question'])
def test_demo_answer_matches_independent_oracle(case,raw):
    answer=QueryEngine().ask(case['question'])
    assert answer.status=='success',answer.error
    assert answer.mock_used and not answer.truncated
    observed={r[0]:tuple(r[1:]) for r in answer.result.rows}
    oracle=expected(case,raw)
    assert len(observed)==answer.result.row_count
    assert observed.keys()==oracle.keys()
    for key in oracle: assert observed[key]==pytest.approx(oracle[key],abs=.011)


@pytest.mark.parametrize('metric',METRICS,ids=lambda m:m['id'])
def test_retrieval_dependencies_and_generation(metric):
    ctx=retrieve(metric['name'],Principal(),metric)
    assert set(metric['dependencies'])<=set(ctx['tables'])
    assert len(ctx['tables'])<=14
    assert ctx['generation']==current_catalog()['generation']
    assert all(r['left'] in ctx['tables'] and r['right'] in ctx['tables'] for r in ctx['relationships'])


@pytest.mark.parametrize('sql',[
 'DELETE FROM fact_order',
 'SELECT * FROM dim_product; DROP TABLE dim_product',
 "SELECT * FROM read_csv('secret.csv')",
 "SELECT * FROM query_table('dim_customer')",
 "SELECT read_text('secret.txt') FROM dim_product",
 'SELECT * FROM main.dim_product',
 'SELECT * FROM dim_product p, dim_channel c',
 'SELECT * FROM dim_product p JOIN dim_sku s ON 1=1',
 'SELECT * FROM dim_product p JOIN dim_sku s ON p.product_id=s.product_id OR 1=1',
 'SELECT * FROM dim_product p JOIN dim_sku s ON p.product_id=s.sku_id',
 'SELECT * FROM dim_product P JOIN dim_sku S ON p.product_id=s.sku_id',
 'WITH RECURSIVE x AS (SELECT * FROM dim_product) SELECT * FROM x',
 'SELECT * INTO new_table FROM dim_product',
 'SELECT * FROM dim_product UNION SELECT * FROM dim_product',
 "SELECT * FROM dim_product LIMIT 'all'",
 'SELECT missing_column FROM dim_product',
 'WITH secret AS (SELECT * FROM dim_customer) SELECT * FROM secret',
])
def test_guard_rejects_unsafe_or_unauthorized_sql(sql,catalog):
    restricted=dict(tables={n:t for n,t in catalog['tables'].items() if n!='dim_customer'},relationships=catalog['relationships'])
    with pytest.raises(Exception): check(sql,restricted)


@pytest.mark.parametrize('sql',[
 "SELECT product_name FROM dim_product WHERE category='喂食器' AND unit_price>0",
 "SELECT product_name FROM dim_product WHERE category='喂食器' OR category='饮水机'",
 "WITH x AS (SELECT product_id,product_name FROM dim_product) SELECT product_name FROM x",
 "SELECT p.product_name FROM dim_product P JOIN dim_sku S ON p.product_id=s.product_id",
])
def test_guard_allows_safe_scope_and_boolean_filters(sql,context):
    assert check(sql,context).tables


@pytest.mark.parametrize('role',['viewer','sales','service'])
def test_retrieval_does_not_expose_forbidden_metadata(role,catalog):
    p=Principal('role-test',role)
    ctx=retrieve('客户财务设备薪酬订阅',p)
    assert all(p.allowed(catalog['tables'][name]) for name in ctx['tables'])
    assert all(p.allowed(catalog['tables'][x['table']]) for x in ctx['evidence'])


@pytest.mark.parametrize('question,status',[
 ('各型号激活率','clarification'),('各型号故障率','clarification'),
 ('删除所有订单','unsupported'),('随便猜收入','unsupported'),('','error')])
def test_clarification_and_unsupported(question,status):
    assert QueryEngine().ask(question).status==status


def test_denied_metric_has_no_result():
    result=QueryEngine().ask('各型号净销售额',principal=Principal('viewer-test','viewer'))
    assert result.status=='denied' and result.result is None and not result.sql


def test_executor_truncation_and_slot_release(context):
    from src.petkit.execution import execute
    stmt=check('SELECT device_id FROM dim_device',context)
    result,truncated=execute(stmt,current_catalog()['generation'])
    assert truncated and result.row_count==1000


def test_executor_timeout_and_slot_release(monkeypatch,context):
    from src.petkit import execution
    from src.petkit.guard import Checked
    monkeypatch.setattr(execution,'QUERY_SECONDS',.01)
    # Trusted harness bypasses guard only to force expensive CPU work and exercise cancellation.
    with pytest.raises(TimeoutError):
        execution.execute(Checked('SELECT SUM(SQRT(i)) FROM range(10000000000) t(i)',[]),current_catalog()['generation'])
    monkeypatch.setattr(execution,'QUERY_SECONDS',15)
    result,_=execution.execute(check('SELECT COUNT(*) AS n FROM dim_product',context),current_catalog()['generation'])
    assert result.rows[0][0]==12


def test_result_bytes_and_concurrency_limits_release_slots(monkeypatch,context):
    from src.petkit import execution
    monkeypatch.setattr(execution,'MAX_BYTES',10)
    with pytest.raises(ValueError,match='字节预算'):
        execution.execute(check('SELECT product_name FROM dim_product',context),current_catalog()['generation'])
    monkeypatch.setattr(execution,'MAX_BYTES',2_000_000)
    for _ in range(3): assert execution.SLOTS.acquire(blocking=False)
    try:
        with pytest.raises(TimeoutError,match='并发'):
            execution.execute(check('SELECT COUNT(*) FROM dim_product',context),current_catalog()['generation'])
    finally:
        for _ in range(3): execution.SLOTS.release()
    result,_=execution.execute(check('SELECT COUNT(*) FROM dim_product',context),current_catalog()['generation'])
    assert result.rows==[[12]]


def test_api_history_identity_isolation_and_paging(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from src.petkit import api,history
    monkeypatch.setattr(history,'DATA',tmp_path)
    monkeypatch.setenv('PETKIT_API_TOKENS',json.dumps({
        'test-a':{'subject':'alice','role':'analyst'},'test-b':{'subject':'bob','role':'viewer'}}))
    client=TestClient(api.app)
    a={'Authorization':'Bearer test-a'}; b={'Authorization':'Bearer test-b'}
    assert client.get('/health').status_code==200
    assert client.get('/schema').status_code==401
    assert client.get('/schema',headers={'Authorization':'Bearer wrong'}).status_code==401
    schema=client.get('/schema?limit=100',headers=a).json()
    assert schema['total']==500 and len(schema['tables'])==100
    names=[]
    for offset in range(0,500,100): names.extend(t['name'] for t in client.get(f'/schema?limit=100&offset={offset}',headers=a).json()['tables'])
    assert len(set(names))==500
    assert client.get('/schema?limit=101',headers=a).status_code==422
    viewer=client.get('/schema',headers=b).json()
    assert viewer['total']<500 and all(t['domain'] in {'product','shared'} for t in viewer['tables'])
    assert client.get('/metrics',headers=b).json()['metrics']==[]
    assert client.post('/ask',headers=a,json={'question':''}).status_code==422
    answer=client.post('/ask',headers=a,json={'question':'各型号净销售额'}).json()
    assert answer['status']=='success',answer
    record=answer['history_id']
    assert len(client.get('/history',headers=a).json()['items'])==1
    assert client.get(f'/history/{record}',headers=b).status_code==404
    assert client.post(f'/history/{record}/favorite',headers=b).status_code==404
    assert client.delete(f'/history/{record}',headers=b).status_code==404
    denied=client.post('/ask',headers=b,json={'question':'各型号净销售额'}).json()
    assert denied['status']=='denied' and denied['result'] is None
    assert client.post(f'/history/{record}/favorite',headers=a).json()['favorited']
    assert client.get('/history?favorite_only=true',headers=a).json()['items']
    assert client.delete(f'/history/{record}',headers=a).json()['deleted']


def test_llm_mode_does_not_silently_fallback(monkeypatch):
    from src.petkit import service
    from src import config
    from src.engine import llm_client
    monkeypatch.setattr(service,'MODE','llm')
    monkeypatch.setattr(config,'API_KEY','test-key-not-real')
    def unavailable(*args,**kwargs): raise llm_client.LLMError('synthetic endpoint failure')
    monkeypatch.setattr(llm_client,'chat_completion',unavailable)
    a=QueryEngine().ask('各型号净销售额')
    assert a.status=='error' and not a.mock_used and a.result is None


def test_llm_binding_repair_revalidates_context(monkeypatch):
    from src.petkit import service
    from src import config
    from src.engine import llm_client
    monkeypatch.setattr(service,'MODE','llm')
    monkeypatch.setattr(config,'API_KEY','test-key-not-real')
    calls=[]
    def reply(messages,**kwargs):
        calls.append(json.loads(messages[1]['content']))
        sql='SELECT missing_column FROM dim_product' if len(calls)==1 else 'SELECT COUNT(*) AS n FROM dim_product'
        return json.dumps(dict(status='ready',plan={k:[] for k in ['metric','dimensions','time_range','filters','joins','grain','assumptions']},sql=sql))
    monkeypatch.setattr(llm_client,'chat_completion',reply)
    a=QueryEngine().ask('各型号净销售额')
    assert a.status=='success',a.error
    assert a.result.rows==[[12]] and len(calls)==2
    assert calls[0]['metadata']==calls[1]['metadata']
    assert 'previous_error' in calls[1]


def test_llm_policy_denial_never_executes_or_repairs(monkeypatch):
    from src.petkit import service
    from src import config
    from src.engine import llm_client
    monkeypatch.setattr(service,'MODE','llm')
    monkeypatch.setattr(config,'API_KEY','test-key-not-real')
    calls=[]
    def malicious(*args,**kwargs):
        calls.append(1)
        return json.dumps(dict(status='ready',plan={k:[] for k in ['metric','dimensions','time_range','filters','joins','grain','assumptions']},sql='DELETE FROM fact_order'))
    monkeypatch.setattr(llm_client,'chat_completion',malicious)
    def forbidden(*args,**kwargs): raise AssertionError('unsafe SQL reached execution')
    monkeypatch.setattr(service,'execute',forbidden)
    a=QueryEngine().ask('各型号净销售额')
    assert a.status=='denied' and a.result is None and len(calls)==1


def test_workbench_renders_and_executes(monkeypatch,tmp_path):
    from streamlit.testing.v1 import AppTest
    from src.petkit import history
    monkeypatch.setattr(history,'DATA',tmp_path)
    monkeypatch.delenv('PETKIT_API_TOKENS',raising=False)
    at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'src'/'app.py')).run(timeout=30)
    assert not at.exception,[e.message for e in at.exception]
    assert [m.value for m in at.metric][:2]==['500','40']
    at.selectbox[0].select('各型号净销售额').run(timeout=30)
    button=next(b for b in at.button if b.label=='开始分析')
    button.click().run(timeout=30)
    assert not at.exception,[e.message for e in at.exception]
    assert at.session_state['petkit-answer'].status=='success'
