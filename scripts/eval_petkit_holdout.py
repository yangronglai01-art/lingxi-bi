"""One-shot frozen holdout; re-runs go to new run directories, never replace history."""
import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

os.environ['PROJECT_PROFILE']='petkit'
os.environ['PETKIT_MODE']='llm'
os.environ.pop('PETKIT_EMBEDDING_MODEL',None)
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts'))
from eval_petkit_llm_pilot import expected,assess
import duckdb
from src import config
from src.engine.engine import QueryEngine
from src.petkit.access import Principal
from src.petkit.catalog import METRICS
from src.petkit.retrieval import current_catalog,retrieve
from src.petkit.settings import artifacts


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cases',default=str(ROOT/'evals/petkit_holdout_v1.json'))
    parser.add_argument('--ids',default='',help='定向开发复核，不算新的首次留出成绩')
    parser.add_argument('--embedding-model',default='',help='已有本地模型目录；空值保持历史词法基线')
    args=parser.parse_args()
    if args.embedding_model: os.environ['PETKIT_EMBEDDING_MODEL']=args.embedding_model
    path=Path(args.cases)
    definitions=json.loads(path.read_text(encoding='utf-8'))
    folder,manifest=artifacts()
    cat=current_catalog()
    case_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    prior=[]
    for result in (ROOT/'reports'/'holdout_runs').glob('*/results.json'):
        previous=json.loads(result.read_text(encoding='utf-8'))
        if previous.get('case_sha256')==case_hash: prior.append(str(result.relative_to(ROOT)))
    selected=[c for c in definitions['cases'] if not args.ids or c['id'] in args.ids.split(',')]
    output=ROOT/'reports'/'holdout_runs'/uuid.uuid4().hex[:12]
    output.mkdir(parents=True)
    hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in [
        'src/petkit/service.py','src/petkit/planning.py','src/petkit/retrieval.py',
        'src/petkit/guard.py','src/petkit/semantic.py','src/engine/llm_client.py',
        'scripts/eval_petkit_holdout.py','scripts/eval_petkit_llm_pilot.py']}
    report=dict(version=definitions['version'],generation=manifest['generation'],model=config.MODEL_NAME,
        case_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),code_hashes=hashes,
        frozen_before_run=True,first_pass=not prior and not args.ids,prior_runs=prior,selected_ids=args.ids,external_independent_benchmark=False,
        retrieval='BM25+BGE/local sentence-transformers+RRF' if args.embedding_model else 'BM25 + 字符词法向量',embedding_model=args.embedding_model,rows=[],completed=False)
    (output/'frozen_cases.json').write_bytes(path.read_bytes())
    result_file=output/'results.json'
    result_file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    conn=duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True)
    try:
        raw={}
        for name,t in cat['tables'].items():
            if t['core']:
                cur=conn.execute(f'SELECT * FROM "{name}"')
                names=[v[0] for v in cur.description]
                raw[name]=[dict(zip(names,row)) for row in cur.fetchall()]
    finally: conn.close()
    for case in selected:
        principal=Principal('holdout-evaluation',case.get('role','analyst'))
        retrieval=None
        if case.get('metric_id'):
            metric=next(m for m in METRICS if m['id']==case['metric_id'])
            top=retrieve(case['question'],principal,None)
            required=set(metric['dependencies'])
            hits=set(e['table'] for e in top['evidence'])
            retrieval=dict(raw_top8=[e['table'] for e in top['evidence']],
                registered_dependency_recall=len(required&hits)/len(required),
                complete=required<=hits,
                note='按指标登记依赖衡量原始Top8，含当前分组可能不需要的依赖，不是最小充分表集标注')
        begin=time.monotonic()
        answer=QueryEngine().ask(case['question'],principal=principal)
        state=case['expected_status']
        if state=='success':
            reference=expected(case,raw)
            values=answer.result.rows if answer.result else None
            if values is not None and case.get('group')=='month':
                values=[[v[:7] if isinstance(v,str) and len(v)>=10 and v[:7] in reference else v for v in row] for row in values]
            assessed=assess(SimpleNamespace(status=answer.status,result=SimpleNamespace(rows=values) if values is not None else None),reference,raw)
            if assessed['passed'] and case.get('descending'):
                numbers=[[v for v in row if isinstance(v,(float,int)) and not isinstance(v,bool)] for row in values]
                ordered=all(numbers[i][0]>=numbers[i+1][0] for i in range(len(numbers)-1))
                if not ordered: assessed=dict(passed=False,reason='数值正确，但未遵循降序要求')
        elif state=='safe_refusal':
            assessed=dict(passed=answer.status in {'clarification','denied','unsupported'} and answer.result is None,
                          reason='写操作必须明确拒绝或澄清，不接受普通程序错误冒充拒绝')
        else:
            assessed=dict(passed=answer.status==state and answer.result is None,reason='状态与预期匹配' if answer.status==state else '状态不匹配')
        record=dict(id=case['id'],question=case['question'],expected_status=state,status=answer.status,
            error=answer.error.replace(config.API_KEY,'[REDACTED]') if config.API_KEY else answer.error,
            sql=answer.sql,plan=answer.plan,columns=answer.result.columns if answer.result else None,
            result_rows=answer.result.rows if answer.result else None,
            elapsed_seconds=round(time.monotonic()-begin,3),assessment=assessed,
            raw_retrieval=retrieval,selected_tables=answer.context.get('table_names',[]),mock_used=answer.mock_used)
        report['rows'].append(record)
        result_file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:record[k] for k in ['id','status','elapsed_seconds','assessment']},ensure_ascii=False),flush=True)
        if answer.status=='error' and any(s in answer.error for s in ['401','402','403','404','429','请求大模型失败']):
            report['stop_reason']='服务不可用，停止后续计费调用'
            break
    report['completed']=len(report['rows'])==len(selected)
    report['passed']=sum(r['assessment']['passed'] for r in report['rows'])
    report['attempted']=len(report['rows'])
    report['numeric_questions']=sum(r['expected_status']=='success' for r in report['rows'])
    report['numeric_passed']=sum(r['expected_status']=='success' and r['assessment']['passed'] for r in report['rows'])
    result_file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'reports/petkit500_holdout_latest.json').write_text(json.dumps(dict(run=str(output.relative_to(ROOT)),result=str(result_file.relative_to(ROOT)))),encoding='utf-8')
    print(json.dumps(dict(report=str(result_file),passed=report['passed'],attempted=report['attempted'],numeric_passed=report['numeric_passed']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
