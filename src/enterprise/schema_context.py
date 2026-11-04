"""Permission-first, connected schema selection; no model or database calls."""
from collections import deque
from copy import deepcopy
import hashlib
import json

from .intent import Clarify
from .prompt_context import user_content, GATEWAY_MESSAGE_CHARS, REPAIR_RESERVE_CHARS

MAX_TABLES = 14
MAX_HOPS = 4
MAX_OPTIONAL_ANCHORS = 2


def authorized_catalog(catalog, allowed):
    """Prevent hidden schemas/metric definitions from influencing lexical recall."""
    tables = {n:t for n,t in catalog['tables'].items() if n in allowed}
    return dict(catalog, tables=tables,
        metrics=[m for m in catalog['metrics'] if set(m['dependencies']) <= tables.keys()],
        relationships=approved_edges(catalog, set(tables)))


def approved_edges(catalog, allowed):
    edges=[]
    for r in catalog['relationships']:
        if r.get('approved') is not True or r['left'] not in allowed or r['right'] not in allowed:
            continue
        if r['left_key'] not in catalog['tables'][r['left']]['columns'] or r['right_key'] not in catalog['tables'][r['right']]['columns']:
            raise Clarify('批准关系引用了不存在的字段，需要更新目录。',reason='schema_relationship')
        edges.append(deepcopy(r))
    return sorted(edges,key=lambda r:(r['left'],r['left_key'],r['right'],r['right_key']))


def fuse(lexical, dense, allowed):
    scores={};sources={}
    for route, names in [('bm25',[e['table'] for e in lexical]),('bge',dense)]:
        valid=list(dict.fromkeys(n for n in names if n in allowed))
        for rank,n in enumerate(valid,1):
            scores[n]=scores.get(n,0)+1/(60+rank)
            sources.setdefault(n,{})[route]=rank
    return [dict(table=n,score=round(scores[n],9),ranks=sources[n])
            for n in sorted(scores,key=lambda n:(-scores[n],n))]


def _path(graph, starts, target):
    queue=deque((n,[n]) for n in sorted(starts));seen=set(starts)
    while queue:
        node,path=queue.popleft()
        if node==target:return path
        if len(path)-1>=MAX_HOPS:continue
        for neighbor in sorted(graph.get(node,())):
            if neighbor not in seen:
                seen.add(neighbor);queue.append((neighbor,path+[neighbor]))
    return None


def _compact(table):
    # Keep whole schemas and enums. Do not guess which fields the LLM may need.
    result={k:deepcopy(table[k]) for k in ('name','domain','description','grain','aliases','core','columns') if k in table}
    for field in ('description',):
        if field in result:result[field]=result[field][:240]
    if 'aliases' in result:result['aliases']=[str(a)[:80] for a in result['aliases'][:16]]
    for col in result['columns'].values():
        if 'description' in col:col['description']=col['description'][:240]
    return result


def build(catalog, allowed, metric, ranked, question, contract, max_chars):
    required=list(dict.fromkeys(metric.get('dependencies',[])))
    if not required:raise Clarify('指标缺少已登记依赖。',reason='schema_dependencies')
    if set(required)-allowed:raise PermissionError('Metric outside granted business domains')
    if len(required)>MAX_TABLES:
        raise Clarify('指标依赖超过14表预算，需要批准的汇总视图或拆分问题。',reason='schema_budget')
    edges=approved_edges(catalog,allowed);graph={}
    for r in edges:
        graph.setdefault(r['left'],set()).add(r['right']);graph.setdefault(r['right'],set()).add(r['left'])
    selected=[required[0]];reasons={required[0]:'metric_dependency'}
    for target in required[1:]:
        if target in selected:reasons[target]='metric_dependency';continue
        path=_path(graph,set(selected),target)
        if path is None:raise Clarify('授权范围内没有完整的批准关联路径，不能猜测JOIN。',reason='schema_relationship')
        for n in path:
            if n not in selected:selected.append(n)
            reasons[n]='metric_dependency' if n in required else 'approved_bridge'
    mandatory=list(selected)
    if len(mandatory)>MAX_TABLES:
        raise Clarify('依赖和批准桥表超过14表预算，不能截断关联路径。',reason='schema_budget')
    budget=min(max_chars,GATEWAY_MESSAGE_CHARS)-REPAIR_RESERVE_CHARS
    def pack(names):
        return dict(version=catalog['version'],data_as_of=catalog['as_of'],
            tables={n:_compact(catalog['tables'][n]) for n in names},metric=deepcopy(metric),
            relationships=[r for r in edges if r['left'] in names and r['right'] in names])
    packed=pack(selected)
    if len(user_content(question,packed,contract))>budget:
        raise Clarify('必需表字段、枚举及指标规则超出上下文预算，请缩小范围或登记汇总视图。',reason='schema_budget',
                      details={'missing_slots':['narrower_scope'],'required_tables':len(mandatory)})
    dropped=[];optional_anchors=0
    for candidate in ranked:
        target=candidate['table']
        if target not in allowed or target in selected:continue
        if optional_anchors>=MAX_OPTIONAL_ANCHORS:
            dropped.append(dict(table=target,reason='optional_candidate_budget'));continue
        path=_path(graph,set(selected),target)
        if path is None:dropped.append(dict(table=target,reason='no_approved_path'));continue
        names=list(dict.fromkeys(selected+path))
        if len(names)>MAX_TABLES:dropped.append(dict(table=target,reason='table_budget'));continue
        proposed=pack(names)
        if len(user_content(question,proposed,contract))>budget:
            dropped.append(dict(table=target,reason='message_budget'));continue
        for n in path:
            if n not in selected:
                selected.append(n);reasons[n]='retrieved_candidate' if n==target else 'approved_bridge'
        packed=proposed
        optional_anchors+=1
    serialized=user_content(question,packed,contract)
    packed.update(selected_tables=selected,selection=[dict(table=n,reason=reasons[n]) for n in selected],
        mandatory_tables=mandatory,dropped_candidates=dropped,
        context_budget=dict(schema_message_chars=len(serialized),max_message_chars=min(max_chars,GATEWAY_MESSAGE_CHARS),
            repair_reserved_chars=REPAIR_RESERVE_CHARS,table_count=len(selected),
            column_count=sum(len(t['columns']) for t in packed['tables'].values()),
            sha256=hashlib.sha256(serialized.encode()).hexdigest(),tokenizer_measured=False))
    return packed
