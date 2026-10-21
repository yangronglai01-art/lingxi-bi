"""Sequential schema-context validation; no LLM or business queries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def inspect(context,case,principal):
    tables=context['tables']
    missing=[f'{table}.{column}' for table,columns in case['required_columns'].items()
             for column in columns if column not in tables.get(table,{}).get('columns',{})]
    graph={name:set() for name in tables}
    bad_edges=[]
    for edge in context['relationships']:
        left,right=edge['left'],edge['right']
        if (not edge.get('approved') or left not in tables or right not in tables
            or edge['left_key'] not in tables[left]['columns']
            or edge['right_key'] not in tables[right]['columns']):
            bad_edges.append(edge)
            continue
        graph[left].add(right)
        graph[right].add(left)
    required=set(case['required_columns'])
    visited=set()
    pending=[next(iter(required))]
    while pending:
        node=pending.pop()
        if node in visited: continue
        visited.add(node)
        pending.extend(graph.get(node,set())-visited)
    unauthorized=[name for name,t in tables.items() if not principal.allowed(t)]
    evidence_unauthorized=[e['table'] for e in context['evidence'] if e['table'] not in tables and
        not principal.allowed(CATALOG['tables'][e['table']])]
    return dict(missing_columns=missing,disconnected_tables=sorted(required-visited),
        bad_relationships=bad_edges,unauthorized_tables=unauthorized,
        unauthorized_evidence=evidence_unauthorized,table_count=len(tables),
        passed=not(missing or required-visited or bad_edges or unauthorized or evidence_unauthorized)
               and len(tables)<=14)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--embedding-model',default='')
    args=parser.parse_args()
    os.environ['PETKIT_EMBEDDING_MODEL']=args.embedding_model
    from src.petkit.access import Principal
    from src.petkit.retrieval import current_catalog,retrieve
    global CATALOG
    CATALOG=current_catalog()
    path=ROOT/'evals/petkit_schema_context_v1.json'
    raw=path.read_bytes()
    definitions=json.loads(raw)
    metrics={m['id']:m for m in CATALOG['metrics']}
    folder=ROOT/'reports/schema_context_runs'/uuid.uuid4().hex[:12]
    folder.mkdir(parents=True)
    (folder/'frozen_cases.json').write_bytes(raw)
    report=dict(generation=CATALOG['generation'],case_sha256=hashlib.sha256(raw).hexdigest(),
        code_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ['scripts/validate_petkit_schema_context.py','src/petkit/retrieval.py','src/petkit/access.py']},
        embedding_model=args.embedding_model,real_llm_called=False,load_test=False,
        external_independent_benchmark=False,field_ranking_evaluated=False,rows=[],completed=False)
    def save():
        (folder/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    for case in definitions['cases']:
        principal=Principal('schema-evaluation','analyst')
        for completion in (False,True):
            context=retrieve(case['question'],principal,metrics[case['metric_id']] if completion else None)
            report['rows'].append(dict(id=case['id'],kind='context',metric_completion=completion,
                assessment=inspect(context,case,principal)))
        save()
    for identity in definitions['identities']:
        principal=Principal('schema-'+identity['id'],identity['role'],tuple(identity['regions']),managed=True)
        for metric_id,metric in metrics.items():
            case=next(c for c in definitions['cases'] if c['metric_id']==metric_id)
            expected=metric_id in identity['allowed_metrics']
            try:
                context=retrieve(case['question'],principal,metric)
            except PermissionError:
                assessment=dict(passed=not expected,denied=True,expected_allowed=expected)
            else:
                assessment=inspect(context,case,principal)
                assessment.update(expected_allowed=expected,denied=False)
                assessment['passed']=assessment['passed'] and expected
            report['rows'].append(dict(id=identity['id']+':'+metric_id,kind='permission',assessment=assessment))
        save()
    report['completed']=True
    report['passed']=sum(r['assessment']['passed'] for r in report['rows'])
    report['total']=len(report['rows'])
    save()
    print(json.dumps(dict(report=str(folder/'results.json'),passed=report['passed'],total=report['total']),ensure_ascii=False))
    if report['passed']!=report['total']: sys.exit(1)

if __name__=='__main__': main()
