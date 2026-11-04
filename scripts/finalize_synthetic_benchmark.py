"""Explicit boundary references, QA checks, and version-bound delivery evidence."""
import argparse,hashlib,json,sys,secrets
from contextlib import ExitStack
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymysql
from src.benchmarks.oracle import Oracle
from src.benchmarks.cases import generic_case,complex_case,entry
from src.enterprise.execution import bind,wire
from scripts.build_synthetic_benchmark import dump,lines
from scripts.evaluate_enterprise import judge
from scripts.verify_synthetic_benchmark import DB,read,records


def finalize(root):
    root=Path(root).resolve();results=[]
    with ExitStack() as stack:
        admin=stack.enter_context(pymysql.connect(host='127.0.0.1',port=9030,user='root',password='',autocommit=True))
        user='synth_edge_reader';password=secrets.token_urlsafe(32)
        with admin.cursor() as c:
            c.execute('CREATE USER '+user+' IDENTIFIED BY %s',(password,));c.execute('GRANT SELECT ON ALL TABLES IN DATABASE `'+DB+'` TO USER '+user)
        def cleanup():
            with admin.cursor() as c:c.execute('DROP USER IF EXISTS '+user)
        stack.callback(cleanup)
        conn=stack.enter_context(pymysql.connect(host='127.0.0.1',port=9030,user=user,password=password,database=DB,autocommit=True,charset='utf8mb4',read_timeout=60))
        with conn.cursor() as c:
            for profile,prefix,tag in [('development','d_','D'),('holdout','h_','H'),('shift','s_','S')]:
                folder=root/profile;catalog=read(folder/'catalog.json');rows={n:records(folder/'tables'/(n+'.jsonl')) for n,m in catalog['tables'].items() if m['core']}
                data=dict(rows=rows,catalog=catalog,profile=profile,seed=read(folder/'summary.json')['seed']);oracle=Oracle(data);cases=[]
                sequence=[('order_count','2022-01'),('refund_sum','2022-01'),('offline_scalar','2022-01'),('net','2022-01'),('yoy','2022-01'),('activation_gap','2026-10'),('latest','2022-01'),('ytd','2024-02'),('top2','2024-02'),('activation','2024-02')]
                for i,(kind,month) in enumerate(sequence):
                    if i<3:
                        q,spec,sql=generic_case(kind,month,0,tag);cols=[a for _,_,a in spec['aggregates']];answer=oracle.generic(spec)
                    else:q,sql,cols=complex_case(kind,month,catalog['as_of']);answer=oracle.complex(kind,month)
                    case=entry(data,profile+'_edge','simple' if i<3 else 'complex',i,q,sql,cols,answer,kind=kind,month=month);cases.append(case)
                    c.execute(bind(sql,{n:prefix+n for n in rows}));actual=[[wire(v) for v in row] for row in c.fetchall()]
                    status=judge(dict(case,requires_model=None),dict(status='ok',result=dict(columns=[d[0] for d in c.description],rows=actual)))
                    results.append(dict(id=case['id'],status=status))
                    if status!='passed_value':raise ValueError('Boundary check failed: '+case['id'])
                if not any(row[1]==0 and row[-1] is None for row in cases[5]['expected_rows']):raise ValueError('Missing actual zero-denominator case')
                lines(folder/'boundary_cases.jsonl',cases)
    dump(root/'boundary_verification.json',dict(passed=True,model_called=False,production_eligible=False,results=results))
    repo=Path(__file__).resolve().parents[1]
    names=['src/benchmarks/'+n for n in ('synthetic.py','oracle.py','cases.py','policy.py')]+['scripts/'+n for n in ('build_synthetic_benchmark.py','supplement_synthetic_benchmark.py','verify_synthetic_benchmark.py','verify_synthetic_supplements.py','finalize_synthetic_benchmark.py','freeze_enterprise_eval.py','evaluate_enterprise.py')]+['operator_tests/test_synthetic_benchmark.py']
    dump(root/'implementation_hashes.json',{n:hashlib.sha256((repo/n).read_bytes()).hexdigest() for n in names})
    hashes={str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file()}
    dump(root/'delivery_manifest.json',dict(source_kind='synthetic-benchmark',production_eligible=False,model_called=False,files=hashes))
    print('30 boundary SQL checks passed; versioned delivery manifest written')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);a=p.parse_args();finalize(a.data)
