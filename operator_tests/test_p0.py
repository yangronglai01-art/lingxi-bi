import copy
import json
from datetime import datetime, timezone

import httpx
import pytest

from src.enterprise.acceptance import governance_errors, evidence_errors, template, sha256
from scripts.freeze_enterprise_eval import freeze
from scripts.check_enterprise_tokenizer import measure
from scripts.smoke_enterprise import smoke


def catalog():
    return dict(synthetic=False, governance=dict(status='approved',approval_id='review-1'), tables={'orders':{'columns':{'id':{},'amount':{}}}}, metrics=[dict(
        id='net_sales', owner='finance-owner', approval_id='finance-1', approval_status='approved', definition='Paid less refunded', grain='order',
        version='v1', unit='CNY', currency='CNY', timezone='Asia/Shanghai', source_kind='enterprise', approved_dimensions=[], freshness_sla_seconds=3600,
        dependencies=['orders'], source_policies=dict(inclusion='paid',deletion='tombstone',time_attribution='paid_at',nulls='reject invalid amount'),
        source_mapping={'orders':dict(source_object='ERP.orders',primary_key='id',fields={'id':'ERP.orders.id','amount':'ERP.orders.amount'})})])


def test_confirmed_governance(): assert governance_errors(catalog(), ['net_sales']) == []


@pytest.mark.parametrize('field,value', [('owner',None),('approval_status','technical_contract_only'),('currency','CNY (unconfirmed)'),
    ('timezone','Asia/Shanghai (unconfirmed)'),('freshness_sla_seconds',True),('source_kind','synthetic'),('source_policies',{}),('source_mapping',{})])
def test_governance_rejects_unapproved_or_incomplete(field,value):
    c=catalog(); c['metrics'][0][field]=value
    assert governance_errors(c,['net_sales'])


def bundle(tmp_path):
    report=tmp_path/'proof.json'; report.write_text('{}',encoding='utf-8')
    b=template();b.update(tenant='t',release='r',model_revision='m',embedding_revision='e')
    for record in b['packages'].values():
        record.update(status='passed',provenance='real_environment',reviewer='operator',approval_reference='approved-1',
            reviewed_at=datetime.now(timezone.utc).isoformat(),artifacts=[dict(path=report.name,sha256=sha256(report))])
    return b


def check(b,root): return evidence_errors(b,root,tenant='t',release='r',model_revision='m',embedding_revision='e')


def test_evidence_binding_and_tamper(tmp_path):
    b=bundle(tmp_path); assert not check(b,tmp_path)
    (tmp_path/'proof.json').write_text('{"changed":true}',encoding='utf-8'); assert check(b,tmp_path)


@pytest.mark.parametrize('field,value',[('status','pending'),('provenance','protocol_stub'),('reviewer',''),('reviewed_at','2999-01-01T00:00:00+00:00')])
def test_unfinished_evidence_rejected(tmp_path,field,value):
    b=bundle(tmp_path);b['packages']['P0-03'][field]=value;assert check(b,tmp_path)


def test_outside_and_wrong_release_rejected(tmp_path):
    b=bundle(tmp_path);b['packages']['P0-01']['artifacts'][0]['path']='../outside.json';assert check(b,tmp_path)
    b=bundle(tmp_path);b['release']='other';assert check(b,tmp_path)


def cases(prefix):
    return [dict(id=prefix+d, question=prefix+d, group_id=prefix+d, difficulty=d, requires_model=True,
        source_kind='enterprise',reference_owner='owner',reference_provenance='independent raw-record oracle',
        expected_status='ok',expected_columns=['n'],expected_rows=[[1]]) for d in ('simple','join','complex','behavior')]


def inputs(tmp_path):
    holdout,dev=tmp_path/'holdout.jsonl',tmp_path/'dev.jsonl'
    for path,prefix in ((holdout,'h'),(dev,'d')):
        path.write_text('\n'.join(json.dumps(c) for c in cases(prefix)),encoding='utf-8')
    criteria=tmp_path/'criteria.json';criteria.write_text(json.dumps(dict(owner='owner',approval_reference='gate-1',by_difficulty={
        d:dict(min_correct_rate=.8,max_wrong_answer_rate=.05,min_cases=1,max_p95_seconds=60) for d in ('simple','join','complex','behavior')})),encoding='utf-8')
    return holdout,dev,criteria,tmp_path/'frozen.json'


def test_freeze_and_no_overwrite(tmp_path):
    args=inputs(tmp_path); result=freeze(*args);assert result['total']==4
    with pytest.raises(FileExistsError):freeze(*args)


def test_split_group_overlap_rejected(tmp_path):
    a=inputs(tmp_path); rows=cases('d');rows[0]['group_id']='hsimple';a[1].write_text('\n'.join(json.dumps(c) for c in rows),encoding='utf-8')
    with pytest.raises(ValueError,match='overlap'):freeze(*a)


def test_reference_missing_rejected(tmp_path):
    a=inputs(tmp_path);rows=cases('h');del rows[0]['expected_rows'];a[0].write_text('\n'.join(json.dumps(c) for c in rows),encoding='utf-8')
    with pytest.raises(ValueError):freeze(*a)


def test_tokenizer_counts_chat_overhead_and_reserved_output():
    class Tokenizer:
        def apply_chat_template(self,messages,**kwargs):return [1]*15
    assert not measure(Tokenizer(),[],20,6)['passed']
    assert measure(Tokenizer(),[],21,6)['passed']


def test_smoke_two_actors_no_result_leak(monkeypatch):
    monkeypatch.setenv('LINGXI_SMOKE_OTHER_TOKEN','other')
    def handle(r):
        other=r.headers.get('authorization')=='Bearer other'
        if r.url.path.endswith('/me'):
            if not r.headers.get('authorization'):return httpx.Response(401)
            return httpx.Response(200,json=dict(subject='b' if other else 'a',managed=True))
        if r.url.path=='/health/ready':return httpx.Response(200)
        if r.url.path.endswith('/queries'):return httpx.Response(202,json={'id':'j'})
        if other:return httpx.Response(403)
        return httpx.Response(200,json=dict(state='completed',result=dict(status='ok',source_kind='enterprise',mock_used=False)))
    with httpx.Client(base_url='https://app.invalid',headers={'Authorization':'Bearer actor'},transport=httpx.MockTransport(handle)) as c:
        assert smoke(c,'query')['passed']


def test_smoke_rejects_same_subject(monkeypatch):
    monkeypatch.setenv('LINGXI_SMOKE_OTHER_TOKEN','same')
    def handle(r):
        if r.url.path.endswith('/me'):
            return httpx.Response(200,json=dict(subject='same',managed=True)) if r.headers.get('authorization') else httpx.Response(401)
        if r.url.path=='/health/ready':return httpx.Response(200)
        if r.url.path.endswith('/queries'):return httpx.Response(202,json={'id':'j'})
        return httpx.Response(200,json=dict(state='completed',result=dict(status='ok',source_kind='synthetic',mock_used=False)))
    with httpx.Client(base_url='https://app.invalid',headers={'Authorization':'Bearer actor'},transport=httpx.MockTransport(handle)) as c:
        assert not smoke(c,'query')['passed']


def test_quality_separates_model_and_template_and_denominator():
    from src.enterprise.quality import report
    cs=[dict(id='m',difficulty='simple',requires_model=True),dict(id='t',difficulty='simple',requires_model=False)]
    rs=[dict(id='m',judgment='failed_value',first_result={'status':'ok'},elapsed_seconds=2),
        dict(id='t',judgment='passed_value',first_result={'status':'ok'},elapsed_seconds=1)]
    result=report(rs,cs)['by_difficulty']['simple']['by_expected_path']
    assert result['model']['correct_rate']==0 and result['model']['wrong_answer_rate']==1
    assert result['template_or_behavior']['correct_rate']==1


def test_quality_zero_tolerance_overrides_average():
    from src.enterprise.quality import report
    cs=[dict(id='s',difficulty='behavior',requires_model=False,zero_tolerance=True)]
    rs=[dict(id='s',judgment='failed_status',first_result={'status':'clarification'},elapsed_seconds=1)]
    rules={'by_difficulty':{d:dict(min_cases=0,min_correct_rate=0,max_wrong_answer_rate=1,max_p95_seconds=60) for d in ('simple','join','complex','behavior')}}
    assert report(rs,cs,rules)['gate_passed'] is False
