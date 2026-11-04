import copy,hashlib,json
from decimal import Decimal
import pytest
from src.benchmarks.synthetic import make,invariants,money
from src.benchmarks.oracle import Oracle,percent
from src.benchmarks.cases import build_cases
from src.enterprise.loader import source_quality
from scripts.build_synthetic_benchmark import negative_cases
from scripts.freeze_enterprise_eval import freeze,read_cases
from scripts.evaluate_enterprise import validate_evaluation_profile

@pytest.fixture(scope='module')
def datasets():return [make(20261023,30,'development'),make(20271030,40,'holdout')]

def test_decimal_anchors():
    assert money(1001)=='10.01'
    assert str(percent(1,6))=='16.67'
    assert percent(0,0) is None

def test_valid_graph_and_business_controls(datasets):
    for d in datasets:
        assert invariants(d)['passed']
        assert len(source_quality(d['catalog'],d['rows'],d['keys']))==500

def test_determinism_and_seed_diversity(datasets):
    d=datasets[0];other=make(20261023,30,'development')
    assert d==other
    assert d['rows']['fact_order']!=datasets[1]['rows']['fact_order']

def test_negative_controls_are_real(datasets):
    cases=negative_cases(datasets[0]);assert len(cases)>=12 and all(c['verified'] for c in cases)

def test_dev_and_holdout_sql_groups_disjoint(datasets):
    dev,test=[build_cases(d,Oracle(d),d['profile']=='development') for d in datasets]
    assert len(dev)==60 and len(test)==350
    assert not ({c['group_id'] for c in dev}&{c['group_id'] for c in test})
    assert all(c['expected_status']=='ok' for c in test if c['reference_sql'])
    assert all(c['source_kind']=='synthetic-benchmark' for c in test)

def test_oracle_order_independence_and_refund_splitting(datasets):
    d=datasets[1];changed=copy.deepcopy(d)
    for records in changed['rows'].values():records.reverse()
    oracle=Oracle(d);other=Oracle(changed)
    for kind in ('net','yoy','ytd','top2','activation','service','latest','backlog','activation_gap'):
        a=oracle.complex(kind,'2026-09');b=other.complex(kind,'2026-09')
        assert sorted(a,key=str)==sorted(b,key=str)
    refund=changed['rows']['fact_refund'][0];amount=Decimal(refund['amount']);refund['amount']='0.01'
    changed['rows']['fact_refund'].append(dict(refund,refund_id='SPLIT_TEST',amount=str(amount-Decimal('.01'))))
    assert sorted(oracle.complex('net','2026-09'),key=str)==sorted(Oracle(changed).complex('net','2026-09'),key=str)

def test_hand_calculated_refund_oracle():
    r={n:[] for n in ('dim_product','dim_device','dim_sku','fact_order','fact_order_item','fact_refund')}
    r['dim_product']=[dict(product_id='P',product_name='合成')];r['dim_sku']=[dict(sku_id='S',product_id='P')]
    r['fact_order']=[dict(order_id='O',paid_date='2026-09-01',status='已支付')]
    r['fact_order_item']=[dict(item_id='I',order_id='O',sku_id='S',amount='100.01')]
    r['fact_refund']=[dict(item_id='I',amount='10.01'),dict(item_id='I',amount='20.00')]
    o=Oracle(dict(rows=r,catalog={'as_of':'2026-10-16'}))
    assert o.complex('net','2026-09')==[['合成','70.00']]
    assert o.complex('refund_rate','2026-09')==[['合成','100.01','30.01','30.01']]

def test_synthetic_cannot_impersonate_enterprise(tmp_path,datasets):
    dev,test=[build_cases(d,Oracle(d),d['profile']=='development') for d in datasets]
    for name,records in [('dev',dev),('test',test)]:
        (tmp_path/name).write_text('\n'.join(json.dumps(c) for c in records),encoding='utf-8')
    with pytest.raises(ValueError):read_cases(tmp_path/'test')
    assert len(read_cases(tmp_path/'test','synthetic'))==350
    criteria=dict(purpose='synthetic_engineering_only',owner='test-spec',approval_reference='not-enterprise-approval',by_difficulty={d:dict(min_correct_rate=.9,max_wrong_answer_rate=.05,min_cases=1,max_p95_seconds=60) for d in ('simple','join','complex','behavior')})
    (tmp_path/'criteria').write_text(json.dumps(criteria),encoding='utf-8')
    result=freeze(tmp_path/'test',tmp_path/'dev',tmp_path/'criteria',tmp_path/'frozen','synthetic')
    assert result['production_eligible'] is False
    assert result['evaluation_profile']=='synthetic'

def test_evaluator_profile_and_mixing():
    cases=[{'source_kind':'synthetic-benchmark'}]
    manifest={'evaluation_profile':'synthetic','production_eligible':False}
    criteria={'purpose':'synthetic_engineering_only'}
    validate_evaluation_profile(cases,manifest,criteria,'synthetic')
    for profile,m,c in [('enterprise',manifest,criteria),('synthetic',None,criteria),('synthetic',manifest,{})]:
        with pytest.raises(ValueError):validate_evaluation_profile(cases,m,c,profile)
    with pytest.raises(ValueError):validate_evaluation_profile(cases+[{'source_kind':'enterprise'}],manifest,criteria,'synthetic')

def test_policy_manual_anchor():
    from src.benchmarks.policy import answer
    r={n:[] for n in ('dim_product','dim_device','dim_sku','dim_channel','fact_order','fact_order_item','fact_refund')}
    r['dim_product']=[{'product_id':'P','product_name':'合成'}];r['dim_sku']=[{'sku_id':'S','product_id':'P'}]
    r['dim_channel']=[{'channel_id':'CH','channel_name':'合成渠道'}]
    r['fact_order']=[{'order_id':'O','paid_date':'2026-09-01','channel_id':'CH'}]
    r['fact_order_item']=[{'item_id':'I','order_id':'O','sku_id':'S','amount':'100.01'}]
    r['fact_refund']=[{'item_id':'I','amount':'30.01'}]
    assert answer({'rows':r},{'metric_id':'net_sales','group':'channel','month':'2026-09'})==(['dimension','net_sales'],[['合成渠道','70.00']])
