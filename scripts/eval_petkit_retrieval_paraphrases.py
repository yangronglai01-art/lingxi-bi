"""Serial, uncompleted raw retrieval benchmark. No LLM or warehouse writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', default=str(ROOT/'evals/petkit_retrieval_paraphrases_v1.json'))
    parser.add_argument('--embedding-model', default='')
    args = parser.parse_args()
    os.environ['PETKIT_EMBEDDING_MODEL'] = args.embedding_model
    from src.petkit.access import Principal
    from src.petkit.retrieval import Index, current_catalog
    source = Path(args.cases)
    raw = source.read_bytes()
    definitions = json.loads(raw)
    ids = [c['id'] for c in definitions['cases']]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError('Empty or duplicate case ids')
    catalog = current_catalog()
    for case in definitions['cases']:
        if not case['required_tables'] or not set(case['required_tables']) <= set(catalog['tables']):
            raise ValueError('Invalid required tables: '+case['id'])
    output = ROOT/'reports'/'retrieval_runs'/uuid.uuid4().hex[:12]
    output.mkdir(parents=True)
    (output/'frozen_cases.json').write_bytes(raw)
    report = dict(generation=catalog['generation'], case_sha256=hashlib.sha256(raw).hexdigest(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        retrieval_sha256=hashlib.sha256((ROOT/'src/petkit/retrieval.py').read_bytes()).hexdigest(),
        embedding_model=args.embedding_model, real_llm_called=False, load_test=False,
        external_independent_benchmark=False, dependency_completion_used=False, rows=[], completed=False)
    target = output/'results.json'
    def save():
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    index = Index(catalog, args.embedding_model)
    for case in definitions['cases']:
        principal = Principal('retrieval-evaluation',case['role'])
        allowed = {n for n,t in catalog['tables'].items() if principal.allowed(t)}
        required = set(case['required_tables'])
        if not required <= allowed:
            raise ValueError('Required tables unauthorized: '+case['id'])
        for method in ('bm25','hybrid'):
            start = time.monotonic()
            hits = index.search(case['question'],allowed,limit=8,method=method)
            selected = [h['table'] for h in hits]
            found = required & set(selected)
            report['rows'].append(dict(id=case['id'],metric_id=case['metric_id'],method=method,
                top8=selected,recall_at8=len(found)/len(required),complete=required<=set(selected),
                missing=sorted(required-set(selected)),elapsed_seconds=round(time.monotonic()-start,4)))
        save()
    report['summary'] = {}
    for method in ('bm25','hybrid'):
        rows = [r for r in report['rows'] if r['method']==method]
        report['summary'][method] = dict(questions=len(rows),complete=sum(r['complete'] for r in rows),
            macro_recall_at8=sum(r['recall_at8'] for r in rows)/len(rows))
    report['completed'] = True
    save()
    print(json.dumps(dict(report=str(target),summary=report['summary']),ensure_ascii=False))

if __name__ == '__main__':
    main()
