"""Offline selection and prompt invariants; does not measure actual BGE/LLM quality."""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from src.enterprise.schema_context import authorized_catalog, fuse, build
from src.enterprise.prompt_context import user_content
from src.enterprise.intent import Clarify,derive
from src.enterprise.metric_contracts import resolve
from src.enterprise.query import Query,index
from src.petkit.catalog import catalog_payload
from src.petkit.planning import examples


def table(name):return dict(name=name,domain='sales',description='业务表',grain='一行',core=True,aliases=[],columns={'id':{'type':'BIGINT','description':'关联键'},'value':{'type':'DECIMAL(20,2)','description':'金额','enum':[1,2]}})
def edge(a,b,approved=True):return dict(left=a,left_key='id',right=b,right_key='id',approved=approved,cardinality='many_to_one')
def fixture(names=('a','bridge','b'),deps=('a','b')):
    m=dict(id='qa_metric',dependencies=list(deps),name='QA',aliases=[],definition='QA',grain='QA',version='qa')
    return dict(version='qa',as_of='2026-10-16',tables={n:table(n) for n in names},metrics=[m],relationships=[edge(a,b) for a,b in zip(names,names[1:])])
def pack(c,allowed=None,ranked=None,max_chars=32000):
    return build(c,set(c['tables']) if allowed is None else allowed,c['metrics'][0],ranked or [],'QA查询',{'metric':'qa_metric'},max_chars)


def test_bridge_added_before_optional_candidates():
    c=fixture();x=pack(c)
    assert x['mandatory_tables']==['a','bridge','b']
    assert next(r['reason'] for r in x['selection'] if r['table']=='bridge')=='approved_bridge'
    assert set(x['tables']['bridge']['columns'])==set(c['tables']['bridge']['columns'])
    assert x['tables']['bridge']['columns']['value']['enum']==[1,2]


@pytest.mark.parametrize('policy',['hidden','unapproved','missing_key','too_long'])
def test_no_invented_or_unauthorized_join_path(policy):
    c=fixture();allowed=set(c['tables'])
    if policy=='hidden':allowed.remove('bridge')
    if policy=='unapproved':c['relationships'][0]['approved']=False
    if policy=='missing_key':c['relationships'][0]['left_key']='missing'
    if policy=='too_long':c=fixture(('a','x1','x2','x3','x4','b'))
    with pytest.raises(Clarify):pack(c,allowed if policy!='too_long' else None)


def test_dependency_denied_before_schema_selection():
    with pytest.raises(PermissionError):pack(fixture(),{'a','bridge'})


def test_required_tables_are_never_silently_truncated():
    c=fixture(tuple('t'+str(i) for i in range(15)),tuple('t'+str(i) for i in range(15)))
    with pytest.raises(Clarify,match='14'):pack(c)


def test_optional_path_added_atomically_or_skipped():
    c=fixture(('a','x','b'),('a',));x=pack(c,ranked=[dict(table='b')])
    assert set(x['selected_tables'])=={'a','x','b'}
    c['tables']['x']['columns']['value']['enum']=['large_value'*500]*40
    x=pack(c,ranked=[dict(table='b')])
    assert x['selected_tables']==['a'] and x['dropped_candidates'][0]['reason']=='message_budget'


def test_mandatory_oversize_fails_without_losing_enums_or_fields():
    c=fixture();c['tables']['a']['columns']['value']['enum']=['large_value'*500]*40
    with pytest.raises(Clarify,match='预算'):pack(c)


def test_same_prompt_snapshot_and_long_sql_repair_fit_reserved_space():
    c=fixture();x=pack(c);contract={'metric':'qa_metric'}
    first=json.loads(user_content('QA查询',x,contract))
    previous={'previous_sql':'SELECT '*20000,'feedback':{'code':'sql_binding'}}
    repair=json.loads(user_content('QA查询',x,contract,previous))
    assert first['metadata']==repair['metadata'] and first['execution_contract']==repair['execution_contract']
    assert 'previous_sql' not in repair['repair'] and repair['repair']['previous_sql_omitted']
    assert len(user_content('QA查询',x,contract,previous))<x['context_budget']['max_message_chars']
    assert x==pack(c)


def test_hidden_catalog_and_metric_text_not_indexed():
    c=fixture();c['tables']['b']['description']='PRIVATE_SCHEMA_SENTINEL'
    c['metrics'][0]['definition']='PRIVATE_METRIC_SENTINEL'
    visible=authorized_catalog(c,{'a'})
    assert visible['metrics']==[] and set(visible['tables'])=={'a'}
    assert 'PRIVATE_' not in json.dumps(visible)
    docs=index(json.dumps(visible,sort_keys=True),'').documents
    assert all(name=='a' and 'PRIVATE_' not in text for name,kind,text in docs)


def test_fusion_deduplicates_and_filters_before_ranking():
    lexical=[{'table':'b'},{'table':'b'},{'table':'hidden'},{'table':'a'}]
    result=fuse(lexical,['a','a','hidden','b'],{'a','b'})
    assert [r['table'] for r in result]==['a','b']
    assert result[0]['ranks']=={'bm25':2,'bge':1}


@pytest.mark.parametrize('example',range(37))
def test_500_table_context_preserves_registered_schemas_and_budget(example):
    c=catalog_payload();c['as_of']='2026-10-16';e=examples(c['as_of'])[example]
    m=next(m for m in c['metrics'] if m['id']==e['metric_id'])
    settings=SimpleNamespace(embedding_revision='',max_context_chars=34000)
    p=SimpleNamespace(scope='global',allowed=lambda t:True)
    r=dict(id='qa_release',catalog=c,views={'global':{n:n for n in c['tables']}},watermark={})
    x=Query(settings,None).context(p,r,e['question'],m)
    assert set(m['dependencies'])<=x['tables'].keys() and len(x['tables'])<=14
    for n in m['dependencies']:assert x['tables'][n]['columns'].keys()==c['tables'][n]['columns'].keys()
    contract=resolve(derive(e['question'],c),c)
    assert len(user_content(e['question'],x,contract))<=24000
    assert x['context_budget']['repair_reserved_chars']==8000 and not x['context_budget']['tokenizer_measured']


def test_scorer_keeps_retrieval_separate_from_mandatory_completion():
    from scripts.score_enterprise_retrieval import score
    cases=[dict(id='a',expected_tables=['orders','items'],expected_metric='sales')]
    ctx=dict(metric={'id':'sales'},selected_tables=['orders','items'],mandatory_tables=['orders','items'],
        evidence=[{'table':'orders'}],retrieval_candidates={'bm25':['orders'],'bge':[],'fused':[{'table':'orders'}]})
    result=score(cases,[dict(id='a',first_result={'context':ctx})])['cases'][0]
    assert result['context_recall']==1 and result['fused_recall_at_24']==.5 and result['mandatory_completion_count']==1
    assert result['bge_recall_at_24'] is None
    with pytest.raises(ValueError):score(cases,[])


@pytest.mark.parametrize('bad',[None,[True]+[0.0]*1023,[float('nan')]+[0.0]*1023,[2.0]+[0.0]*1023,[0.0]*1024])
def test_corrupted_bge_vectors_fail_closed(bad):
    from src.enterprise.vectors import search
    from src.enterprise.store import digest,Conflict
    c=fixture(('a',),('a',));text=json.dumps(c['tables']['a'],ensure_ascii=False,sort_keys=True)
    class Store:
        @contextmanager
        def db(self):yield self
        def execute(self,*args):pass
        def fetchall(self):return [dict(table_name='a',content_hash=digest(text),vector=bad)]
    settings=SimpleNamespace(gateway_url='https://private.invalid/v1',gateway_key='fixture',tenant='qa')
    transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':[{'index':0,'embedding':[1.0]+[0.0]*1023}]}))
    with pytest.raises(Conflict):search(settings,Store(),dict(id='qa',catalog=c),'QA',{'a'},'qa-revision',transport,limit=24)


def test_configured_bge_unavailability_does_not_silently_use_bm25(monkeypatch):
    c=catalog_payload();c['as_of']='2026-10-16'
    settings=SimpleNamespace(embedding_revision='qa-revision',max_context_chars=34000)
    p=SimpleNamespace(scope='global',allowed=lambda t:True)
    r=dict(id='qa_release',catalog=c,views={'global':{n:n for n in c['tables']}},watermark={})
    def unavailable(*a,**k):raise RuntimeError('EMBEDDING_UNAVAILABLE')
    monkeypatch.setattr('src.enterprise.vectors.search',unavailable)
    with pytest.raises(RuntimeError):Query(settings,None).context(p,r,'各型号净销售额',c['metrics'][0])
