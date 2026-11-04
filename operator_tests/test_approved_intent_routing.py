import sys,copy,json
from pathlib import Path
import pytest
from operator_tests.test_query_hardening import harness,runner,ready
from src.enterprise.intent import derive,approved_intent_example,Clarify
from src.enterprise.metric_contracts import resolve
from src.petkit.planning import examples

@pytest.mark.parametrize('e',examples('2026-10-16'))
@pytest.mark.parametrize('wrap',[lambda q:'请查询'+q+'情况',lambda q:'帮我查看'+q+'谢谢'])
def test_validated_intent_matches_registered_plan(harness,e,wrap):
    intent=derive(wrap(e['question']),harness[4])
    chosen=approved_intent_example(intent,harness[4])
    assert chosen['sql']==e['sql'] and chosen['month']==e['month']

@pytest.mark.parametrize('e',examples('2026-10-16')[:7]+examples('2026-10-16')[-1:])
def test_production_route_bypasses_model_but_keeps_execution(harness,e):
    harness[0].approved_intent_routing=True
    q,calls,executions=runner(harness,[RuntimeError('must not call model')])
    # Result fixtures need the correct metric output shape; exercise net_sales
    # end-to-end here, all metrics run against real StarRocks in the batch.
    if e['metric_id']!='net_sales':
        intent=derive('请查询'+e['question']+'情况',harness[4])
        assert approved_intent_example(intent,harness[4]) is not None
        return
    answer=q.run(dict(harness[3],question='请查询'+e['question']+'情况'),harness[2])
    assert answer['status']=='ok' and answer['generation_mode']=='approved_template'
    assert answer['routing']['reason']=='validated_intent_signature'
    assert calls==[] and len(executions)==1

def test_model_ablation_remains_explicit(harness):
    q,calls,executions=runner(harness,[ready()])
    assert q.run(harness[3],harness[2])['generation_mode']=='local-chat'
    assert len(calls)==1

def test_unregistered_month_does_not_reuse_all_time_plan(harness):
    intent=derive('请查询2026年7月各型号净销售额情况',harness[4])
    assert intent.month=='2026-07'
    assert approved_intent_example(intent,harness[4]) is None

@pytest.mark.parametrize('q',['请查询各型号净销售额只看华东情况','各型号净销售额同比','各型号销售额','各型号净销售额2026年13月'])
def test_ambiguity_and_filters_cannot_hit_template(harness,q):
    with pytest.raises(Clarify):derive(q,harness[4])

def test_routed_request_still_checks_revocation(harness):
    harness[0].approved_intent_routing=True;harness[1].active=False
    q,calls,executions=runner(harness,[ready()])
    with pytest.raises(PermissionError):q.run(harness[3],harness[2])
    assert calls==[] and executions==[]

@pytest.mark.parametrize('e',examples('2026-10-16'))
def test_compiler_shape_is_visible_and_hashed(harness,e):
    contract=resolve(derive(e['question'],harness[4]),harness[4])
    shape=contract['sql_shape']
    assert shape['outer']['projections'] and shape['outer']['source']
    assert 'not_general_equivalence' in shape['verification']
    assert len(contract['sha256'])==64

def test_offline_rate_contract_uses_allowed_conditional_sum(harness):
    contract=resolve(derive('各固件版本设备离线率',harness[4]),harness[4])
    assert 'COUNT_IF' not in contract['formula']
    assert 'SUM(CASE' in contract['formula']
