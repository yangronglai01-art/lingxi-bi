"""Run generated policy and CDC fixtures against isolated local QA databases."""
import argparse,hashlib,json,sys,uuid,secrets
from contextlib import ExitStack
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymysql
from src.enterprise.execution import bind,wire
from src.enterprise.ingestion import Batches
from src.enterprise.store import Store,Conflict
from scripts.evaluate_enterprise import judge
from scripts.build_synthetic_benchmark import dump
from scripts.verify_synthetic_benchmark import DB,read,records


def verify(root,dsn):
    root=Path(root).resolve()
    for relative,digest in read(root/'supplement_checksums.json').items():
        p=(root/relative).resolve()
        if not p.is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('Supplement checksums changed')
    results=[]
    with ExitStack() as stack:
        admin=stack.enter_context(pymysql.connect(host='127.0.0.1',port=9030,user='root',password='',autocommit=True))
        user='synth_policy_reader';password=secrets.token_urlsafe(32)
        def cleanup():
            with admin.cursor() as ac:ac.execute('DROP USER IF EXISTS '+user)
        with admin.cursor() as ac:
            ac.execute('CREATE USER '+user+' IDENTIFIED BY %s',(password,))
            ac.execute('GRANT SELECT ON ALL TABLES IN DATABASE `'+DB+'` TO USER '+user)
        stack.callback(cleanup)
        conn=stack.enter_context(pymysql.connect(host='127.0.0.1',port=9030,user=user,password=password,database=DB,autocommit=True,charset='utf8mb4',read_timeout=60))
        with conn.cursor() as c:
            for profile,prefix in [('development','d_'),('holdout','h_'),('shift','s_')]:
                catalog=read(root/profile/'catalog.json');mapping={n:prefix+n for n,m in catalog['tables'].items() if m['core']}
                for case in records(root/profile/'policy_regression.jsonl'):
                    c.execute(bind(case['reference_sql'],mapping));rows=[[wire(v) for v in row] for row in c.fetchall()]
                    status=judge(dict(case,requires_model=None),dict(status='ok',result=dict(columns=[d[0] for d in c.description],rows=rows)))
                    results.append(dict(id=case['id'],status=status))
    store=Store(dsn);batches=Batches(store);tenant='synth_v2_'+uuid.uuid4().hex[:12];cdc=[]
    for case in records(root/'cdc_sequences.jsonl'):
        try:actual=batches.apply(tenant,case['source'],case['batch'],case['events'],case['schema'],case['watermark'])
        except Conflict:actual='Conflict'
        except ValueError:actual='ValueError'
        with store.db() as c:
            c.execute('SELECT source_version,deleted FROM analytics_source_rows WHERE tenant=%s AND source=%s',(tenant,case['source']));row=c.fetchone()
        passed=actual==case['expected_result'] and row['source_version']==case['expected_ledger_version'] and row['deleted']==case['expected_deleted']
        cdc.append(dict(id=case['id'],passed=passed,actual=actual,ledger_version=row['source_version'],deleted=row['deleted']))
    report=dict(purpose='synthetic_policy_reference_and_CDC_only',production_eligible=False,model_called=False,policy_results=results,cdc_results=cdc,isolated_tenant=tenant,passed=all(r['status']=='passed_value' for r in results) and all(r['passed'] for r in cdc))
    dump(root/'supplement_verification.json',report)
    if not report['passed']:raise ValueError('Supplement verification failed')
    print(str(len(results))+' registered-template references and '+str(len(cdc))+' CDC steps passed; no model calls')

if __name__=='__main__':
    import os
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);a=p.parse_args()
    dsn=os.environ.get('LINGXI_SYNTH_QA_PG_DSN','')
    # Disposable loopback QA only; do not accept enterprise or remote DSNs.
    from psycopg2.extensions import parse_dsn
    cfg=parse_dsn(dsn)
    if cfg.get('host')!='127.0.0.1' or cfg.get('port')!='56103' or cfg.get('dbname')!='lingxi_enterprise_qa':p.error('Explicit loopback QA PostgreSQL DSN required in LINGXI_SYNTH_QA_PG_DSN')
    verify(a.data,dsn)
