"""Add current-policy regression and CDC contracts without changing frozen challenge sets."""
import argparse,copy,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.benchmarks.policy import answer
from src.benchmarks.cases import entry
from src.petkit.planning import examples
import sqlglot
from scripts.build_synthetic_benchmark import dump,lines


def supplements(root):
    root=Path(root);added=[]
    for profile in ('development','holdout','shift'):
        folder=root/profile;catalog=json.loads((folder/'catalog.json').read_text(encoding='utf-8'))
        rows={n:[json.loads(l) for l in (folder/'tables'/(n+'.jsonl')).read_text(encoding='utf-8').splitlines()] for n,m in catalog['tables'].items() if m['core']}
        data=dict(rows=rows,catalog=catalog,profile=profile,seed=json.loads((folder/'summary.json').read_text(encoding='utf-8'))['seed'])
        cases=[]
        for index,spec in enumerate(examples(catalog['as_of'])):
            cols,result=answer(data,spec)
            sql=sqlglot.parse_one(spec['sql'],read='postgres').sql(dialect='starrocks')
            case=entry(data,profile+'_policy','join',index,spec['question'],sql,cols,result,kind=spec['metric_id'],month=spec['month'])
            case.update(requires_model=False,current_support='registered_template_regression',policy_spec={k:v for k,v in spec.items() if k!='sql'})
            cases.append(case)
        path=folder/'policy_regression.jsonl';lines(path,cases);added.append(path)
        if profile=='development':base=copy.deepcopy(rows['dim_product'][0]);columns=list(catalog['tables']['dim_product']['columns'])
    schema={'dim_product':columns};when='2026-10-16T10:00:00+08:00'
    def event(version,op='upsert',after=None):return dict(table='dim_product',key=base['product_id'],source_version=version,op=op,after=copy.deepcopy(base if after is None and op=='upsert' else after),event_time=when)
    newer=dict(base,product_name='合成CDC更新设备');conflict=dict(newer,product_name='合成CDC冲突设备')
    sequences=[('initial',event(1),'pending',1,False),('duplicate_batch',event(1),'pending',1,False),('update',event(3,after=newer),'pending',3,False),('late_old_version',event(2),'pending',3,False),('same_version_conflict',event(3,after=conflict),'Conflict',3,False),('delete',event(4,'delete'),'pending',4,True),('late_after_delete',event(3,after=newer),'pending',4,True),('same_delete_replay',event(4,'delete'),'pending',4,True),('schema_drift',event(5,after={'product_id':base['product_id']}),'ValueError',4,True),('missing_timezone',dict(event(5),event_time='2026-10-16T10:00:00'),'ValueError',4,True)]
    cases=[]
    for ident,e,expected,version,deleted in sequences:
        batch='initial' if ident=='duplicate_batch' else ident
        cases.append(dict(id=ident,source='synthetic_cdc',batch=batch,events=[e],schema=schema,watermark=dict(source_version=e['source_version'],event_time=e['event_time'],batch_id=batch),expected_result=expected,expected_ledger_version=version,expected_deleted=deleted))
    path=root/'cdc_sequences.jsonl';lines(path,cases);added.append(path)
    security=[dict(id='anonymous',requires='no token',endpoint='GET /api/v1/analytics/me',expected_http=401),dict(id='client_role',requires='analyst token',endpoint='POST /api/v1/analytics/queries',body={'question':'各渠道净销售额','role':'admin'},expected_http=422),dict(id='cross_user',requires='second authenticated user and owned query',endpoint='GET /api/v1/analytics/queries/{other_job}',expected_http=403),dict(id='revocation',requires='SSO/operator revokes actor during queued/running request',expected_error='AUTHORIZATION_CHANGED'),dict(id='regional_scope',requires='R1 reader plus independently materialized regional source',expected='No R2 rows and R1 numeric results equal independent subset oracle')]
    path=root/'security_workflows.json';dump(path,dict(status='fixtures_only_not_new_SSO_evidence',cases=security));added.append(path)
    path=root/'supplement_checksums.json';dump(path,{str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in added})
    print('Added 111 registered-template references, 10 CDC steps and 5 security workflows')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);a=p.parse_args();supplements(a.data)
