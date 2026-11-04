"""Score first-attempt retrieval metadata against independently labeled table sets."""
import argparse
import json
from pathlib import Path


def score(cases,records):
    refs={c['id']:c for c in cases};rows=[]
    if len(refs)!=len(cases) or {r['id'] for r in records}!=set(refs):raise ValueError('Every case needs exactly one attempt record')
    if len({r['id'] for r in records})!=len(records):raise ValueError('Duplicate attempt records')
    for r in records:
        case=refs[r['id']];required=set(case.get('expected_tables',[]))
        if not required:continue
        context=r.get('first_result',{}).get('context',{})
        selected=set(context.get('selected_tables',[]))
        lexical=[e['table'] for e in context.get('evidence',[]) if 'table' in e]
        routes=context.get('retrieval_candidates',{})
        def recall(names):return len(required&set(names))/len(required)
        rows.append(dict(id=r['id'],context_recall=len(required&selected)/len(required),
            lexical_recall_at_8=len(required&set(lexical[:8]))/len(required),
            context_precision=len(required&selected)/len(selected) if selected else 0,
            bm25_recall_at_24=recall(routes.get('bm25',[])[:24]) if routes else None,
            bge_recall_at_24=recall(routes.get('bge',[])[:24]) if context.get('embedding_revision') and routes else None,
            fused_recall_at_24=recall([e['table'] for e in routes.get('fused',[])][:24]) if routes else None,
            mandatory_completion_count=len(set(context.get('mandatory_tables',[]))-set(e['table'] for e in routes.get('fused',[]))),
            context_budget=context.get('context_budget'),
            metric_route_correct=(context.get('metric') or {}).get('id')==case.get('expected_metric'),
            embedding_revision=context.get('embedding_revision'),generation=r.get('first_result',{}).get('generation')))
    return dict(labeled_cases=len(rows),cases=rows,
        mean_context_recall=sum(r['context_recall'] for r in rows)/len(rows) if rows else None,
        note='Dependencies added deterministically can make context recall high. This does not establish BGE semantic quality, SQL correctness or causal improvement; compare frozen BM25 vs hybrid runs.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('cases','results','out'):p.add_argument('--'+n,required=True)
    a=p.parse_args()
    def read(path):
        p=Path(path)
        if p.stat().st_size>50_000_000:raise ValueError('Input too large')
        return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
    result=score(read(a.cases),read(a.results))
    with Path(a.out).open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)


if __name__=='__main__':main()
