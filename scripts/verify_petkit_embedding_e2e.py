import json
import os
import sys
import time
import argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'scripts'))
parser=argparse.ArgumentParser(); parser.add_argument('--llm',action='store_true'); parser.add_argument('--ids',default=''); args=parser.parse_args()
# Reuse independent Python arithmetic, imported before selecting runtime mode.
from eval_petkit_llm_pilot import expected,assess
from src.petkit.settings import DATA,artifacts
os.environ['PETKIT_MODE']='llm' if args.llm else 'demo'
os.environ['PETKIT_EMBEDDING_MODEL']=json.loads((DATA/'embedding_runtime.json').read_text(encoding='utf-8'))['model']
from src.petkit import service
service.MODE=os.environ['PETKIT_MODE']
from src.engine.engine import QueryEngine
from src.petkit.planning import examples
from src.petkit.retrieval import current_catalog
import duckdb
folder,_=artifacts(); conn=duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True)
raw={}
for name,t in current_catalog()['tables'].items():
    if t['core']:
        cur=conn.execute(f'SELECT * FROM "{name}"'); names=[c[0] for c in cur.description]
        raw[name]=[dict(zip(names,row)) for row in cur.fetchall()]
conn.close()
report={'retrieval':'BM25+BGE-M3+RRF','sql_generation':'real LLM' if args.llm else 'approved offline templates','not_blind_benchmark':True,'rows':[]}
cases=examples()
if args.llm:
    cases=[c for c in json.loads((ROOT/'evals/petkit_holdout_v1.json').read_text(encoding='utf-8'))['cases'] if c['id'] in {'H01','H04'}]
    if args.ids: cases=[c for c in cases if c['id'] in args.ids.split(',')]
for case in cases:
    start=time.monotonic(); answer=QueryEngine().ask(case['question'])
    assessed=assess(answer,expected(case,raw),raw)
    report['rows'].append(dict(question=case['question'],status=answer.status,assessment=assessed,sql=answer.sql,mock_used=answer.mock_used,retrieval_method=answer.context.get('retrieval_method'),elapsed_seconds=round(time.monotonic()-start,3)))
report['passed']=sum(r['assessment']['passed'] for r in report['rows']); report['attempted']=len(report['rows'])
(ROOT/('reports/petkit500_embedding_llm_followup.json' if args.llm and args.ids else 'reports/petkit500_embedding_llm.json' if args.llm else 'reports/petkit500_embedding_e2e.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':report['passed'],'attempted':report['attempted']},ensure_ascii=False),flush=True)
assert report['passed']==report['attempted']
