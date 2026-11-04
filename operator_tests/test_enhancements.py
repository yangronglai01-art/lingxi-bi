"""Deterministic P1/P2 gates; no real model quality claims."""
from dataclasses import replace
import json

import httpx
import pytest

from src.enterprise.intent import derive,verify_sql,followup,Clarify
from src.enterprise.review import review
from src.enterprise.contract import validate
from src.petkit.catalog import catalog_payload
from src.petkit.planning import examples,sales_sql
from scripts.p12_operator import tuning_assessment
from scripts.score_enterprise_retrieval import score


@pytest.fixture(scope='module')
def catalog():
    c=catalog_payload();c['as_of']='2026-10-16';return c


@pytest.mark.parametrize('example',range(37))
def test_approved_examples_preserved(catalog,example):
    e=examples(catalog['as_of'])[example];i=derive(e['question'],catalog)
    assert verify_sql(e['sql'],i,catalog)['general_equivalence_proof'] is False


def test_alias_rename_accepted(catalog):
    i=derive('请查询各型号的净销售额情况',catalog)
    assert verify_sql(sales_sql().replace(' i ',' line_item ').replace('i.','line_item.'),i,catalog)


@pytest.mark.parametrize('change',[
    lambda sql:sql.replace('SUM(i.amount-COALESCE(r.refunded,0))','SUM(i.amount)'),
    lambda sql:sql.replace('SUM(amount) AS refunded','MAX(amount) AS refunded'),
    lambda sql:sql.replace('LEFT JOIN refunds','JOIN refunds'),
    lambda sql:sql.replace('GROUP BY p.product_name','GROUP BY p.product_name, o.paid_date'),
])
def test_legal_sql_wrong_sales_shape_rejected(catalog,change):
    with pytest.raises(Clarify):verify_sql(change(sales_sql()),derive('各型号净销售额',catalog),catalog)


@pytest.mark.parametrize('question',['各型号净销售额只看华东','各型号净销售额支付状态为待付款','各型号净销售额同比','近3个月各型号净销售额','2026年13月各型号净销售额','各型号各渠道净销售额'])
def test_unapproved_condition_cannot_be_ignored(catalog,question):
    with pytest.raises(Clarify):derive(question,catalog)


def test_model_cannot_change_requested_month(catalog):
    intent=derive('2026年9月各型号净销售额是多少',catalog)
    with pytest.raises(Clarify):verify_sql(sales_sql(),intent,catalog)


def test_followup_patches_structured_state(catalog):
    intent=derive('各型号净销售额',catalog)
    assert followup('按渠道看',intent.payload(),catalog)=='各渠道净销售额'
    assert followup('改为2026年9月',intent.payload(),catalog)=='2026年9月各型号净销售额'
    with pytest.raises(Clarify):followup('只看华东',intent.payload(),catalog)
    c=dict(catalog,as_of='2026-10-17')
    with pytest.raises(Clarify):followup('按渠道看',intent.payload(),c)


def test_missing_refund_amount_alias_rejected():
    with pytest.raises(ValueError):validate({'id':'net_sales'},{'columns':['amount'],'rows':[['10.00']]})
    with pytest.raises(ValueError):validate({'id':'tickets'},{'columns':['tickets'],'rows':[['1.5']]})
    with pytest.raises(ValueError):validate({'id':'offline_rate'},{'columns':['device_days','offline_percent'],'rows':[[1,101]]})


def test_review_untrusted_result_and_unavailability(catalog):
    from types import SimpleNamespace
    s=SimpleNamespace(review_mode='shadow',review_revision='review-v1',review_seconds=1,review_alias='semantic-review',gateway_url='https://private.invalid/v1',gateway_key='not-real',max_context_chars=34000)
    i=derive('各型号净销售额',catalog);ctx={'metric':catalog['metrics'][0],'relationships':[]}
    for obj in ([],{'verdict':'run SQL'},{'verdict':'uncertain'}):
        transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(obj)}}]}))
        result=review(s,i,sales_sql(),ctx,transport)
        assert result['verdict'] in {'unavailable','uncertain'}


def test_feedback_does_not_automatically_trigger_training():
    assert tuning_assessment([dict(state='pending',decision='confirmed_issue',category='generation')])['confirmed_issues']==0
    result=tuning_assessment([dict(state='reviewed',decision='confirmed_issue',category='data',n=3)])
    assert result['by_category']=={'data':3} and result['training_authorized'] is False


def test_retrieval_context_and_lexical_recall_separate():
    result=score([dict(id='a',expected_tables=['x','y'],expected_metric='net_sales')],[dict(id='a',first_result=dict(context=dict(selected_tables=['x','y'],evidence=[{'table':'x'}],metric={'id':'net_sales'})))])
    assert result['cases'][0]['context_recall']==1 and result['cases'][0]['lexical_recall_at_8']==.5


def test_guard_normalized_cohort_and_model_limit(catalog):
    from src.petkit.guard import check
    e=examples(catalog['as_of'])[2]
    checked=check(e['sql'],dict(tables=catalog['tables'],relationships=catalog['relationships']),1000)
    assert verify_sql(checked.sql,derive(e['question'],catalog),catalog)
    with pytest.raises(Clarify):verify_sql(sales_sql()+' LIMIT 1',derive('各型号净销售额',catalog),catalog)


def test_review_ablation_never_self_enables():
    from scripts.compare_enterprise_runs import compare
    old=[dict(id='a',difficulty='simple',attempt=1,judgment='passed_value',elapsed_seconds=1)]
    new=[dict(id='a',difficulty='simple',attempt=1,judgment='failed_status',elapsed_seconds=2,first_result={'clarification_reason':'review'})]
    result=compare(old,new)
    assert result['correct_baseline_blocked_by_review']==1 and result['enable_authorized'] is False
    with pytest.raises(ValueError):compare(old,[])


def test_snapshot_operator_credentials_do_not_replace_runtime_reader(monkeypatch):
    from types import SimpleNamespace
    from scripts.p12_operator import snapshot_operator_connection
    import pymysql
    s=SimpleNamespace(environment='production',scopes={'global':dict(host='sr.invalid',database='analytics',user='runtime_reader',ssl_ca='/private/ca.pem')})
    monkeypatch.setenv('LINGXI_SNAPSHOT_OPERATOR_USER','snapshot_operator')
    monkeypatch.setenv('LINGXI_SNAPSHOT_OPERATOR_PASSWORD','test-only-secret')
    captured={}
    monkeypatch.setattr(pymysql,'connect',lambda **kwargs:captured.update(kwargs) or captured)
    snapshot_operator_connection(s)
    assert captured['user']=='snapshot_operator' and captured['ssl']['check_hostname'] is True
    assert s.scopes['global']['user']=='runtime_reader'


@pytest.mark.parametrize('user',['root','runtime_reader'])
def test_production_snapshot_operator_cannot_be_root_or_runtime(monkeypatch,user):
    from types import SimpleNamespace
    from scripts.p12_operator import snapshot_operator_connection
    monkeypatch.setenv('LINGXI_SNAPSHOT_OPERATOR_USER',user);monkeypatch.setenv('LINGXI_SNAPSHOT_OPERATOR_PASSWORD','test-only-secret')
    s=SimpleNamespace(environment='production',scopes={'global':dict(user='runtime_reader')})
    with pytest.raises(ValueError):snapshot_operator_connection(s)
