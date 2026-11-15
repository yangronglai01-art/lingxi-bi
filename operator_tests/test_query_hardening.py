"""Offline enterprise orchestration: real gates, scripted model and SQL transport."""
import copy
import json
from types import SimpleNamespace

import httpx
import pymysql
import pytest

from src.enterprise.query import Query
from src.enterprise.intent import Clarify, derive
from src.enterprise.metric_contracts import resolve
from src.enterprise.generation import Generator, repair_feedback
from src.enterprise.execution import Executor
from src.petkit.catalog import catalog_payload
from src.petkit.planning import sales_sql, examples


@pytest.fixture
def harness(monkeypatch):
    catalog=catalog_payload();catalog['as_of']='2026-10-16'
    release=dict(id='qa_release',catalog=catalog,views={'global':{n:'qa_'+n for n in catalog['tables']}},watermark={'source_kind':'synthetic-offline'})
    class Store:
        active=True
        def verify(self,p):
            if not self.active:raise PermissionError('grant revoked')
        def running(self,job):return True
        def release(self,*args):return release
        def executing(self,*args):pass
    store=Store()
    settings=SimpleNamespace(approved_intent_routing=False,environment='integration',metrics_token='',tenant='petkit',scopes={'global':{'regions':[]}},enabled_metrics=tuple(m['id'] for m in catalog['metrics']),
        model_revision='qa-fixed',embedding_revision='',max_rows=1000,max_context_chars=34000,
        model_seconds=45,query_seconds=15,review_mode='disabled',gateway_url='https://private.invalid/v1',gateway_key='not-a-real-secret')
    principal=SimpleNamespace(tenant='petkit',scope='global',regions=())
    job={'id':'qa-job','question':'请查询各型号的净销售额情况','release':'qa_release'}
    monkeypatch.setattr('src.enterprise.features.observe_batch',lambda *args:None)
    monkeypatch.setattr(Query,'context',lambda self,p,r,q,m:dict(version=catalog['version'],generation=r['id'],
        tables={n:catalog['tables'][n] for n in m['dependencies']},relationships=[r for r in catalog['relationships'] if r['left'] in m['dependencies'] and r['right'] in m['dependencies']],
        metric=m,data_as_of=catalog['as_of']))
    return settings,store,principal,job,catalog


def ready(sql=None):
    return dict(status='ready',sql=sql or sales_sql(),plan=dict(metric='net_sales',dimensions=['product'],
        time_range='snapshot',filters=[],joins=[],grain='order_item',assumptions=[]))


def response(obj):
    return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(obj,ensure_ascii=False)}}]})


def runner(h,outputs,executor=None):
    calls=[]
    def gateway(req):
        calls.append(json.loads(req.content))
        obj=outputs[min(len(calls)-1,len(outputs)-1)]
        if isinstance(obj,Exception):raise obj
        return obj if isinstance(obj,httpx.Response) else response(obj)
    query=Query(h[0],h[1],transport=httpx.MockTransport(gateway))
    executions=[]
    def run(*args,**kwargs):
        executions.append((args,kwargs))
        if executor:return executor(len(executions),args,kwargs)
        return dict(columns=['dimension','net_sales'],rows=[['QA model','12.30']],row_count=1,truncated=False)
    query.executor=SimpleNamespace(run=run)
    return query,calls,executions


@pytest.mark.parametrize('failure',[
    ready(sales_sql().replace('i.amount','i.missing_amount')),
    ready(sales_sql().replace('SUM(i.amount-COALESCE(r.refunded,0))','SUM(i.amount)')),
    {'status':'ready','sql':'SELECT 1','plan':{}},
])
def test_first_failure_repaired_same_contract(harness,failure):
    q,calls,executions=runner(harness,[failure,ready()])
    result=q.run(harness[3],harness[2])
    assert result['status']=='ok' and result['repair_count']==1 and len(calls)==2 and len(executions)==1
    bodies=[json.loads(c['messages'][1]['content']) for c in calls]
    assert bodies[0]['metadata']==bodies[1]['metadata']
    assert bodies[0]['execution_contract']==bodies[1]['execution_contract']==result['metric_contract']
    assert bodies[0]['question']==bodies[1]['question']
    assert bodies[1]['repair']['feedback']['code'] in {'sql_binding','metric_semantics','output_contract'}
    assert all(c['enable_thinking'] is False and 'chat_template_kwargs' not in c for c in calls)


def test_failed_repair_does_not_publish_sql_or_rows(harness):
    q,calls,executions=runner(harness,[ready(sales_sql().replace('i.amount','i.missing_amount'))])
    result=q.run(harness[3],harness[2])
    assert result['status']=='error' and result['error_code']=='SQL_REPAIR_EXHAUSTED'
    assert len(calls)==2 and executions==[] and result['sql']=='' and result['result'] is None


@pytest.mark.parametrize('sql',["SELECT * FROM secrets",'DELETE FROM fact_order',sales_sql().replace('ON i.order_id=o.order_id','ON 1=1')])
def test_policy_failure_is_terminal(harness,sql):
    q,calls,executions=runner(harness,[ready(sql),ready()])
    with pytest.raises(PermissionError):q.run(harness[3],harness[2])
    assert len(calls)==1 and executions==[]


@pytest.mark.parametrize('question,reason',[
    ('各型号销售额','metric_ambiguity'),('各型号收入','metric_ambiguity'),
    ('各型号在线率','metric_ambiguity'),('各型号退款率','unsupported_metric'),
    ('各型号净销售额同比','comparison'),('2026年各渠道累计净销售额','complex_definition'),
    ('各型号净销售额只看华东','unsupported_filter'),('净销售额','dimension'),
])
def test_ambiguity_prevents_all_model_and_sql_calls(harness,question,reason):
    q,calls,executions=runner(harness,[ready()])
    result=q.run(dict(harness[3],question=question),harness[2])
    assert result['status']=='clarification' and result['clarification_reason']==reason
    assert result['clarification_details']['missing_slots'] and not calls and not executions


def test_template_path_does_not_call_model(harness):
    q,calls,executions=runner(harness,[ready()])
    result=q.run(dict(harness[3],question='各型号净销售额'),harness[2])
    assert result['status']=='ok' and result['repair_count']==0 and len(executions)==1 and calls==[]
    assert result['metric_contract']['time_range']['kind']=='published_snapshot'


def test_database_compile_failure_repair_is_scrubbed(harness):
    def execute(n,args,kwargs):
        if n==1:raise pymysql.err.ProgrammingError(1064,'Getting analyzing error. physical_db.secret_source credential text')
        return dict(columns=['dimension','net_sales'],rows=[['QA model','12.30']],row_count=1,truncated=False)
    q,calls,executions=runner(harness,[ready(),ready()],execute)
    result=q.run(harness[3],harness[2])
    assert result['status']=='ok' and len(executions)==2
    assert executions[0][1]['deadline']==executions[1][1]['deadline']
    assert 'secret_source' not in json.dumps(calls) and 'credential text' not in json.dumps(calls)


@pytest.mark.parametrize('exc',[TimeoutError('SQL_DEADLINE'),PermissionError('revoked'),
    pymysql.err.OperationalError(2006,'server lost'),pymysql.err.ProgrammingError(1064,'Memory limit exceeded')])
def test_execution_terminal_errors_not_retried(harness,exc):
    def execute(*args):raise exc
    q,calls,executions=runner(harness,[ready(),ready()],execute)
    with pytest.raises(type(exc)):q.run(harness[3],harness[2])
    assert len(calls)==1 and len(executions)==1 and repair_feedback(exc) is None


def test_revoke_after_model_before_execution(harness):
    def gateway(req):harness[1].active=False;return response(ready())
    q=Query(harness[0],harness[1],transport=httpx.MockTransport(gateway))
    q.executor=SimpleNamespace(run=lambda *a,**k:pytest.fail('must not execute'))
    with pytest.raises(PermissionError):q.run(harness[3],harness[2])


def test_shared_generation_budget_and_post_response_deadline(harness):
    clock=[0.0];seen=[]
    def gateway(req):
        seen.append(req.extensions['timeout']['read']);clock[0]+=20
        return response(ready())
    h=harness
    generator=Generator(h[0],httpx.MockTransport(gateway),clock=lambda:clock[0])
    c=h[4];i=derive(h[3]['question'],c);contract=resolve(i,c)
    deps=c['metrics'][0]['dependencies']
    ctx=dict(version=c['version'],tables={n:c['tables'][n] for n in deps},relationships=[],metric=c['metrics'][0],data_as_of=c['as_of'])
    generator.generate(h[3]['question'],ctx,contract,lambda:True)
    generator.generate(h[3]['question'],ctx,contract,lambda:True)
    with pytest.raises(TimeoutError):generator.generate(h[3]['question'],ctx,contract,lambda:True)
    assert seen==[45,25,5] and generator.spent==60


def test_sql_deadline_expired_before_connection(harness,monkeypatch):
    executor=Executor(harness[0]);monkeypatch.setattr(executor,'connect',lambda *a:pytest.fail('must not connect'))
    with pytest.raises(TimeoutError):executor.run('global','SELECT 1',deadline=0)


@pytest.mark.parametrize('example',range(37))
def test_contract_for_every_approved_template(harness,example):
    c=harness[4];e=examples(c['as_of'])[example];i=derive(e['question'],c)
    a=resolve(i,c);assert len(a['sha256'])==64 and a==resolve(i,c)
    assert a['metric']==e['metric_id'] and a['dependencies']


def test_contract_detects_schema_version_unit_and_dimension_changes(harness):
    c=harness[4];i=derive('各型号净销售额',c);before=resolve(i,c)
    changed=copy.deepcopy(c);changed['tables']['fact_order']['description']+=' revised'
    assert resolve(i,changed)['sha256']!=before['sha256']
    for field,value in [('version','different'),('unit','USD'),('approved_dimensions',['channel'])]:
        changed=copy.deepcopy(c);changed['metrics'][0][field]=value
        with pytest.raises(Clarify):resolve(i,changed)


def test_contract_future_month_rejected(harness):
    c=harness[4]
    with pytest.raises(Clarify):resolve(derive('2027年1月各型号净销售额',c),c)


def test_result_contract_failure_is_repaired_without_sending_rows(harness):
    def execute(n,args,kwargs):
        return dict(columns=['amount'] if n==1 else ['dimension','net_sales'],
            rows=[['PRIVATE_RESULT']] if n==1 else [['QA model','12.30']],row_count=1,truncated=False)
    q,calls,executions=runner(harness,[ready(),ready()],execute)
    result=q.run(harness[3],harness[2])
    assert result['status']=='ok' and len(executions)==2
    assert 'PRIVATE_RESULT' not in json.dumps(calls)
    assert json.loads(calls[1]['messages'][1]['content'])['repair']['feedback']['code']=='result_contract'


def test_model_cannot_switch_metric_during_repair(harness):
    wrong=ready();wrong['plan']['metric']='inventory'
    q,calls,executions=runner(harness,[wrong,ready()])
    with pytest.raises(PermissionError):q.run(harness[3],harness[2])
    assert len(calls)==1 and executions==[]


@pytest.mark.parametrize('upstream',[httpx.Response(503,text='unavailable'),httpx.ConnectError('network down'),httpx.ReadTimeout('slow')])
def test_upstream_failure_never_replays_model_request(harness,upstream):
    q,calls,executions=runner(harness,[upstream,ready()])
    with pytest.raises((RuntimeError,httpx.HTTPError)):q.run(harness[3],harness[2])
    assert len(calls)==1 and executions==[]


def test_incomplete_generation_is_not_executed(harness):
    truncated=httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':json.dumps(ready())}}]})
    q,calls,executions=runner(harness,[truncated,ready()])
    assert q.run(harness[3],harness[2])['status']=='ok'
    assert len(calls)==2 and len(executions)==1


def test_model_clarification_never_executes(harness):
    q,calls,executions=runner(harness,[{'status':'clarification','message':'请确认观察窗口'}])
    result=q.run(harness[3],harness[2])
    assert result['status']=='clarification' and result['clarification_reason']=='model'
    assert len(calls)==1 and executions==[]


def test_revocation_after_compile_error_prevents_repair(harness):
    def execute(*args):
        harness[1].active=False
        raise pymysql.err.ProgrammingError(1064,'Getting analyzing error. unknown column')
    q,calls,executions=runner(harness,[ready(),ready()],execute)
    with pytest.raises(PermissionError):q.run(harness[3],harness[2])
    assert len(calls)==1 and len(executions)==1


def test_metadata_unit_matches_existing_governance_document(harness):
    from pathlib import Path
    governed=json.loads((Path(__file__).resolve().parents[1]/'docs/approved-metric-contracts.json').read_text(encoding='utf-8'))
    c=copy.deepcopy(harness[4])
    for m in c['metrics']:
        prior=next(x for x in governed['metrics'] if x['id']==m['id'])
        m.update(unit=prior['unit'],approved_dimensions=prior['approved_dimensions'])
    for e in examples(c['as_of']):assert resolve(derive(e['question'],c),c)


def test_http_clarification_includes_reason_and_missing_slots(harness):
    from fastapi.testclient import TestClient
    from src.enterprise.api import create_app
    def submit(*args,**kwargs):
        raise Clarify('请确认月份',reason='time_range',details={'missing_slots':['calendar_month']})
    h=harness;h[1].submit=submit
    app=create_app(h[0],h[1],SimpleNamespace(resolve=lambda token:h[2]))
    r=TestClient(app).post('/api/v1/analytics/queries',headers={'Authorization':'Bearer qa','Idempotency-Key':'qa-id'},json={'question':'各型号净销售额'})
    assert r.status_code==422 and r.json()['clarification_details']['missing_slots']==['calendar_month']
    assert r.json()['clarification_reason']=='time_range'

@pytest.mark.parametrize('question,policy', [('各渠道净销售额','partial'),('各型号净销售额','withheld')])
def test_net_sales_incomplete_coverage(harness,question,policy):
    settings,store,principal,job,_=harness
    store.release()['watermark']['metric_coverage']={
        'net_sales':{'global':{'status':'incomplete','covered_channels':['A']}}}
    query,calls,executions=runner(harness,[],lambda *_:dict(
        columns=['dimension','net_sales'],rows=[['A','12.30'],['B','20.00']],row_count=2,truncated=False))
    result=query.run(dict(job,question=question),principal)
    assert result['answer_policy']==policy
    assert not calls
    if policy=='partial':
        assert result['result']['rows']==[['A','12.30']] and len(executions)==1
    else:
        assert result['result'] is None and not executions

def test_release_channel_manifest_drives_net_sales_policy(harness):
    from src.enterprise.loader import with_channel_coverage
    settings,store,principal,job,_=harness
    watermark=store.release()['watermark']
    query,calls,executions=runner(harness,[])
    for flags,expected in [({'QA model':True,'missing':False},'partial'),({'QA model':True},'full')]:
        manifest=with_channel_coverage(dict(watermark,net_sales_partitions={'global':flags}),settings.scopes)
        store.release()['watermark']=manifest
        result=query.run(dict(job,question='各渠道净销售额'),principal)
        assert result['answer_policy']==expected
        assert result['result']['rows']==[['QA model','12.30']]
    assert not calls and len(executions)==2
    with pytest.raises(ValueError):
        with_channel_coverage({'net_sales_partitions':{'other':{'A':True}}},settings.scopes)

def test_channel_net_sales_multiple_refunds_matches_independent_amounts(harness):
    import sqlite3
    from contextlib import closing
    from decimal import Decimal

    settings,store,principal,job,_=harness
    store.release()['watermark']['metric_coverage']={
        'net_sales':{'global':{'status':'complete','covered_channels':['A','B']}}}
    with closing(sqlite3.connect(':memory:')) as db:
        db.executescript("""
          CREATE TABLE qa_fact_order_item(item_id INT, order_id INT, sku_id INT, amount NUMERIC);
          CREATE TABLE qa_fact_order(order_id INT, channel_id INT);
          CREATE TABLE qa_fact_refund(item_id INT, amount NUMERIC);
          CREATE TABLE qa_dim_sku(sku_id INT, product_id INT);
          CREATE TABLE qa_dim_product(product_id INT, product_name TEXT);
          CREATE TABLE qa_dim_channel(channel_id INT, channel_name TEXT);
          INSERT INTO qa_fact_order_item VALUES(1,1,1,100),(2,2,1,80);
          INSERT INTO qa_fact_order VALUES(1,1),(2,2);
          INSERT INTO qa_fact_refund VALUES(1,10),(1,15);
          INSERT INTO qa_dim_sku VALUES(1,1);
          INSERT INTO qa_dim_product VALUES(1,'QA');
          INSERT INTO qa_dim_channel VALUES(1,'A'),(2,'B');
        """)
        def execute(_,args,kwargs):
            cursor=db.execute(args[1])
            rows=[list(row) for row in cursor.fetchall()]
            return dict(columns=[column[0] for column in cursor.description],rows=rows,row_count=len(rows),truncated=False)
        query,calls,_=runner(harness,[],execute)
        result=query.run(dict(job,question='各渠道净销售额'),principal)
    actual={name:Decimal(str(amount)) for name,amount in result['result']['rows']}
    assert actual=={'A':Decimal(100)-Decimal(10)-Decimal(15),'B':Decimal(80)}
    assert not calls

def test_net_sales_monthly_coverage_requires_both_dependencies(harness):
    from src.enterprise.loader import with_channel_coverage
    settings,store,principal,job,_=harness
    store.release()['watermark']=with_channel_coverage({
        'net_sales_partitions': {'global': {'by_month': {
            '2026-09': {'A': {'sales': True, 'refunds': False},
                        'B': {'sales': True, 'refunds': True}}
        }}}
    }, settings.scopes)
    query,calls,executions=runner(harness,[],lambda *_:dict(
        columns=['dimension','net_sales'],rows=[['A','12.30'],['B','20.00']],row_count=2,truncated=False))
    result=query.run(dict(job,question='2026年9月各渠道净销售额'),principal)
    assert result['answer_policy']=='partial'
    assert result['result']['rows']==[['B','20.00']]
    assert query.run(dict(job,question='各渠道净销售额'),principal)['answer_policy']=='withheld'
    assert len(executions)==1 and not calls
