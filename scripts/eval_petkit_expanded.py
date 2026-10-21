"""Frozen development evaluation, prepared first; bounded paid execution only."""
import argparse,hashlib,json,math,os,shutil,sqlite3,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def jobs_hash(data):
    with sqlite3.connect(data/'control.sqlite3') as c:rows=c.execute('SELECT id,state,updated FROM jobs ORDER BY id').fetchall()
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['prepare','behavior','numeric'],default='prepare')
    parser.add_argument('--ids',default='')
    parser.add_argument('--max-generation-attempts',type=int,default=16)
    args=parser.parse_args()
    if not 0<=args.max_generation_attempts<=16:parser.error('generation budget must be 0..16')
    casepath=ROOT/'evals/petkit_expanded_v1.json'
    definitions=json.loads(casepath.read_text(encoding='utf-8'));all_cases=definitions['cases']
    cases=[c for c in all_cases if args.mode=='prepare' or (c['expected_status']=='success')==(args.mode=='numeric')]
    if args.ids:
        requested=set(args.ids.split(','))
        if requested-set(c['id'] for c in cases):parser.error('Unknown or mode-incompatible ids')
        cases=[c for c in cases if c['id'] in requested]
    elif args.mode=='numeric':cases=cases[:8]
    if args.mode=='numeric' and len(cases)>8:parser.error('At most eight paid questions per run')
    if not cases:parser.error('No selected cases')
    source=ROOT/'data/petkit500'
    pointer=json.loads((source/'current.json').read_text(encoding='utf-8'))
    original=dict(pointer=sha(source/'current.json'),warehouse=sha(source/pointer['generation']/'warehouse.duckdb'),jobs=jobs_hash(source))
    run=ROOT/'reports/expanded_eval_runs'/uuid.uuid4().hex[:12];data=run/'isolated_data';data.mkdir(parents=True)
    shutil.copytree(source/pointer['generation'],data/pointer['generation'])
    shutil.copy2(source/'current.json',data/'current.json')
    with sqlite3.connect(source/'control.sqlite3') as origin,sqlite3.connect(data/'control.sqlite3') as target:origin.backup(target)
    if args.mode!='prepare' and (source/'vector_cache').exists():shutil.copytree(source/'vector_cache',data/'vector_cache')
    os.environ.update(PROJECT_PROFILE='petkit',PETKIT_MODE='llm',PETKIT_DEPLOYMENT='simulated',
        PETKIT_DATA_DIR=str(data),PETKIT_EMBEDDING_MODEL=r'D:\projects\Zhixi\models\bge-m3')
    import duckdb
    from src import config
    from src.petkit.control import principal_for
    from src.petkit.reference import expected
    from src.petkit.planning import plan,examples,normalize
    from src.petkit.service import ask
    from src.engine import llm_client
    from scripts.expanded_eval_support import regional_rows,grade
    cat=json.loads((data/pointer['generation']/'catalog.json').read_text(encoding='utf-8'))
    raw={}
    with duckdb.connect(str(data/pointer['generation']/'warehouse.duckdb'),read_only=True) as c:
        for name,table in cat['tables'].items():
            if table['core']:
                result=c.execute('SELECT * FROM "'+name+'"');columns=[r[0] for r in result.description]
                raw[name]=[dict(zip(columns,row)) for row in result.fetchall()]
    (run/'frozen_cases.json').write_bytes(casepath.read_bytes())
    budget=args.max_generation_attempts if args.mode=='numeric' else 0
    report=dict(version=definitions['version'],mode=args.mode,generation=pointer['generation'],model=config.MODEL_NAME,
        case_sha256=sha(casepath),selected_ids=[c['id'] for c in cases],frozen_before_execution=True,
        external_independent_benchmark=False,load_test=False,developer_authored=True,
        generation_budget=budget,rows=[],completed=False,code_sha256={name:sha(ROOT/name) for name in
        ['scripts/eval_petkit_expanded.py','scripts/expanded_eval_support.py','src/petkit/planning.py',
         'src/petkit/service.py','src/petkit/semantic.py','src/petkit/reference.py']})
    def save():(run/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    calls=[];blocked=[];chat=llm_client.chat_completion
    def counted(*a,**kw):
        if len(calls)>=budget:
            blocked.append(time.time());raise RuntimeError('Evaluation generation budget exhausted before provider call')
        calls.append(time.time());return chat(*a,**kw)
    llm_client.chat_completion=counted
    save()
    try:
        if len({c['id'] for c in all_cases})!=len(all_cases):raise ValueError('Duplicate ids')
        for case in cases:
            principal=principal_for(case['subject']);proposal=plan(case['question'],demo=False)
            scoped=regional_rows(raw,principal.regions)
            reference=None
            if case['expected_status']=='success':
                if (proposal.get('metric') or {}).get('id')!=case['metric_id']:raise ValueError('Metric not bound: '+case['id'])
                if normalize(case['question']) in {normalize(x['question']) for x in examples()}:
                    raise ValueError('Numeric case accidentally uses an approved exact template')
                if set(proposal['metric']['dependencies'])-{n for n,t in cat['tables'].items() if principal.allowed(t)}:
                    raise ValueError('Numeric case is not authorized: '+case['id'])
                reference=expected(case,scoped)
                if any(not all(isinstance(v,(int,float)) and math.isfinite(v) for v in values) for values in reference.values()):
                    raise ValueError('Invalid oracle output')
            count=len(calls);start=time.monotonic()
            if args.mode=='prepare':
                assessment=dict(passed=True,reason='case and oracle prepared; model not executed')
                record=dict(id=case['id'],expected_status=case['expected_status'],subject=case['subject'],
                    reference=reference,reference_groups=len(reference) if reference is not None else None,
                    prepared_plan_status=proposal['status'],assessment=assessment,generation_attempts=0)
            else:
                answer=ask(case['question'],principal)
                assessment=grade(case,answer,reference,scoped)
                if args.mode=='behavior' and (len(calls)!=count or blocked):assessment=dict(passed=False,reason='Unexpected attempt to call model for deterministic policy/ambiguity case')
                record=dict(id=case['id'],question=case['question'],subject=case['subject'],regions=principal.regions,
                    expected_status=case['expected_status'],status=answer.status,assessment=assessment,
                    generation_attempts=len(calls)-count,elapsed_seconds=round(time.monotonic()-start,3),
                    bound_metric=(answer.context.get('metric') or {}).get('id'),sql=answer.sql,
                    columns=answer.result.columns if answer.result else None,result_rows=answer.result.rows if answer.result else None,
                    reference=reference,error=answer.error.replace(config.API_KEY,'[REDACTED]') if config.API_KEY else answer.error)
            report['rows'].append(record);save()
            print(json.dumps(dict(id=case['id'],assessment=assessment,generation_attempts=len(calls)-count),ensure_ascii=False),flush=True)
            if not assessment['passed']:
                report['stop_reason']='First failure; no further questions executed';break
    finally:
        report.update(completed=len(report['rows'])==len(cases),passed=sum(r['assessment']['passed'] for r in report['rows']),
            attempted=len(report['rows']),generation_attempts=len(calls),blocked_generation_attempts=len(blocked),
            original_pointer_unchanged=sha(source/'current.json')==original['pointer'],
            original_warehouse_unchanged=sha(source/pointer['generation']/'warehouse.duckdb')==original['warehouse'],
            original_jobs_unchanged=jobs_hash(source)==original['jobs'])
        save()
        print(json.dumps(dict(report=str(run/'results.json'),mode=args.mode,completed=report['completed'],passed=report['passed'],
            attempted=report['attempted'],generation_attempts=len(calls)),ensure_ascii=False),flush=True)
    if report['passed']!=len(cases):sys.exit(1)

if __name__=='__main__':main()
