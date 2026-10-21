"""Seven serial paid-model checks on isolated active-snapshot and identity copies."""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def filehash(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--ids',default='')
    args=parser.parse_args()
    original=ROOT/'data/petkit500' 
    pointer=json.loads((original/'current.json').read_text(encoding='utf-8'))
    run=ROOT/'reports/controlled_e2e_runs'/uuid.uuid4().hex[:12]
    data=run/'isolated_data'
    data.mkdir(parents=True)
    active=original/pointer['generation']
    warehouse_hash=filehash(active/'warehouse.duckdb')
    pointer_hash=filehash(original/'current.json')
    shutil.copytree(active,data/pointer['generation'])
    shutil.copy2(original/'current.json',data/'current.json')
    with sqlite3.connect(original/'control.sqlite3') as source,sqlite3.connect(data/'control.sqlite3') as target:
        source.backup(target)
    if (original/'vector_cache').exists(): shutil.copytree(original/'vector_cache',data/'vector_cache')
    os.environ.update(PROJECT_PROFILE='petkit',PETKIT_MODE='llm',PETKIT_DEPLOYMENT='simulated',
        PETKIT_DATA_DIR=str(data),PETKIT_EMBEDDING_MODEL=r'D:\projects\Zhixi\models\bge-m3')
    import duckdb
    from src import config
    from src.petkit.control import principal_for
    from src.petkit.reference import expected
    from src.petkit.service import ask
    from src.engine import llm_client
    casepath=ROOT/'evals/petkit_controlled_e2e_v1.json'
    cases=json.loads(casepath.read_text(encoding='utf-8'))['cases']
    if args.ids:
        requested=set(args.ids.split(','))
        if requested-set(c['id'] for c in cases): raise ValueError('Unknown ids')
        cases=[c for c in cases if c['id'] in requested]
    (run/'frozen_cases.json').write_bytes(casepath.read_bytes())
    cat=json.loads((active/'catalog.json').read_text(encoding='utf-8'))
    raw={}
    with duckdb.connect(str(data/pointer['generation']/'warehouse.duckdb'),read_only=True) as c:
        for name,table in cat['tables'].items():
            if table['core']:
                result=c.execute(f'SELECT * FROM "{name}"')
                columns=[v[0] for v in result.description]
                raw[name]=[dict(zip(columns,row)) for row in result.fetchall()]
    report=dict(generation=pointer['generation'],model=config.MODEL_NAME,embedding='local BGE-M3',
        case_sha256=filehash(casepath),external_independent_benchmark=False,load_test=False,
        code_sha256={name:filehash(ROOT/name) for name in ['src/petkit/service.py','src/petkit/reference.py',
        'src/petkit/row_policy.py','scripts/eval_petkit_controlled_e2e.py']},rows=[],completed=False,
        isolated_directory=str(data),maximum_questions=len(cases),maximum_generation_attempts=2*len(cases),selected_ids=args.ids,not_new_blind_score=bool(args.ids))
    def save(): (run/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    attempts=[]
    original_chat=llm_client.chat_completion
    def counted(*args,**kwargs):
        attempts.append(time.time())
        return original_chat(*args,**kwargs)
    llm_client.chat_completion=counted
    aliases={}
    for table,key,label in [('dim_product','product_id','product_name'),('dim_channel','channel_id','channel_name'),
                            ('dim_firmware','firmware_id','firmware_name'),('dim_warehouse','warehouse_id','warehouse_name')]:
        aliases.update({r[key]:r[label] for r in raw[table]})
    save()
    try:
        for case in cases:
            principal=principal_for(case['subject'])
            scope=copy.copy(raw)
            if principal.regions:
                regions=set(principal.regions)
                if case['metric_id']=='net_sales':
                    scope['fact_order']=[r for r in raw['fact_order'] if r['region_id'] in regions]
                    ids={r['order_id'] for r in scope['fact_order']}
                    scope['fact_order_item']=[r for r in raw['fact_order_item'] if r['order_id'] in ids]
                elif case['metric_id']=='received_units':
                    warehouses={r['warehouse_id'] for r in raw['dim_warehouse'] if r['region_id'] in regions}
                    scope['fact_receipt']=[r for r in raw['fact_receipt'] if r['warehouse_id'] in warehouses]
                else: raise ValueError('No independently annotated regional oracle for this metric')
            reference=expected(case,scope)
            count=len(attempts)
            start=time.monotonic()
            answer=ask(case['question'],principal)
            # Accept the known extra numerator only after computing its own oracle.
            if (case['metric_id']=='offline_rate' and answer.result is not None
                    and 'offline_device_days' in answer.result.columns):
                names={r['firmware_id']:r['firmware_name'] for r in scope['dim_firmware']}
                offline={k:0 for k in reference}
                for event in scope['fact_device_day']:
                    if event['event_date'].strftime('%Y-%m')==case['month'] and event['online']==0:
                        offline[names[event['firmware_id']]]+=1
                reference={k:tuple(v)+(offline[k],) for k,v in reference.items()}
            found={}
            reason='status or result mismatch'
            passed=(answer.status=='success' and answer.result is not None and not answer.mock_used and not answer.truncated
                    and (answer.context.get('metric') or {}).get('id')==case['metric_id'])
            if passed:
                for row in answer.result.rows:
                    labels=[aliases.get(v,v) for v in row if isinstance(v,str)]
                    numbers=[float(v) for v in row if isinstance(v,(int,float)) and not isinstance(v,bool)]
                    if len(labels)!=1 or labels[0] in found or not all(math.isfinite(v) for v in numbers):
                        passed=False
                        break
                    found[labels[0]]=numbers
                passed=passed and found.keys()==reference.keys()
                if passed:
                    passed=all(len(found[k])==len(values) and all(abs(a-b)<=.011 for a,b in
                        zip(sorted(found[k]),sorted(float(v) for v in values))) for k,values in reference.items())
                reason='exact group set and numeric multiset matched independent Python arithmetic' if passed else 'group/number/count mismatch'
                if passed and case['metric_id'] in {'activation30','service30'}:
                    columns=answer.result.columns
                    passed=all(column in columns for column in ['devices','matched_devices','rate_percent'])
                    if passed:
                        for row in answer.result.rows:
                            label=next(aliases.get(v,v) for v in row if isinstance(v,str))
                            values=[row[columns.index(column)] for column in ['devices','matched_devices','rate_percent']]
                            if any(abs(a-b)>.011 for a,b in zip(values,reference[label])): passed=False
                    if not passed: reason='cohort named-column contract mismatch'
            error=answer.error.replace(config.API_KEY,'[REDACTED]') if config.API_KEY else answer.error
            record=dict(id=case['id'],subject=case['subject'],regions=principal.regions,metric_id=case['metric_id'],
                question=case['question'],status=answer.status,passed=passed,reason=reason,error=error,
                sql=answer.sql,bound_metric=(answer.context.get('metric') or {}).get('id'),columns=answer.result.columns if answer.result else None,
                result_rows=answer.result.rows if answer.result else None,reference=reference,
                generation_attempts=len(attempts)-count,elapsed_seconds=round(time.monotonic()-start,3),mock_used=answer.mock_used)
            report['rows'].append(record)
            save()
            print(json.dumps({k:record[k] for k in ['id','status','passed','generation_attempts','elapsed_seconds']},ensure_ascii=False),flush=True)
            if not passed:
                report['stop_reason']='First failed case; halt further paid questions for diagnosis'
                break
    finally:
        report.update(completed=len(report['rows'])==len(cases),passed=sum(r['passed'] for r in report['rows']),
            attempted=len(report['rows']),generation_attempts=len(attempts),
            original_warehouse_unchanged=filehash(active/'warehouse.duckdb')==warehouse_hash,
            original_pointer_unchanged=filehash(original/'current.json')==pointer_hash)
        save()
        print(json.dumps(dict(report=str(run/'results.json'),passed=report['passed'],attempted=report['attempted'],completed=report['completed']),ensure_ascii=False),flush=True)

if __name__=='__main__': main()
