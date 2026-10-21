import json
import os
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.petkit.retrieval import Index,current_catalog
from src.petkit.settings import DATA
cat=current_catalog(); allowed=set(cat['tables'])
model=json.loads((DATA/'embedding_runtime.json').read_text(encoding='utf-8'))['model']
lexical=Index(cat,''); semantic=Index(cat,model)
cases=json.loads((ROOT/'evals/petkit_holdout_v1.json').read_text(encoding='utf-8'))['cases']
metrics={m['id']:m for m in cat['metrics']}
report={'generation':cat['generation'],'model':model,'note':'开发检索对比，复用已见8题；衡量登记指标依赖，并非盲测或SQL正确率','routes':{}}
for route,index,method in [('bm25',lexical,'bm25'),('bm25_hash',lexical,'hybrid'),('bge',semantic,'vector'),('bm25_bge',semantic,'hybrid')]:
    rows=[]; start=time.monotonic()
    for case in cases:
        if 'metric_id' not in case: continue
        hits=index.search(case['question'],allowed,method=method)
        found={h['table'] for h in hits}; required=set(metrics[case['metric_id']]['dependencies'])
        rows.append(dict(id=case['id'],recall=len(required&found)/len(required),complete=required<=found,tables=[h['table'] for h in hits]))
    report['routes'][route]=dict(complete=sum(r['complete'] for r in rows),attempted=len(rows),mean_recall=sum(r['recall'] for r in rows)/len(rows),elapsed_seconds=round(time.monotonic()-start,2),rows=rows)
(ROOT/'reports/petkit500_embedding_comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:{a:b for a,b in v.items() if a!='rows'} for k,v in report['routes'].items()},ensure_ascii=False),flush=True)
