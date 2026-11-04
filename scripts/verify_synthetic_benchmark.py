"""Loopback-only StarRocks QA reference check; never invokes a model or publishes releases."""
import argparse,hashlib,json,secrets,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymysql
from src.enterprise.execution import bind,wire
from src.enterprise.loader import source_quality,normalize
from src.benchmarks.synthetic import invariants
from scripts.evaluate_enterprise import judge

DB='lingxi_synth_v2_qa'


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def records(path):return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]


def verify(root,confirm):
    if confirm!=DB:raise ValueError('Explicit isolated QA database confirmation required')
    root=Path(root).resolve()
    if (root/'verification.json').exists():raise FileExistsError('Never overwrite verification')
    for relative,digest in read(root/'checksums.json').items():
        p=(root/relative).resolve()
        if not p.is_relative_to(root) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('Frozen data checksum mismatch')
    start=time.monotonic();report=dict(purpose='synthetic_reference_sql_only',production_eligible=False,model_called=False,profiles={},database=DB,host='127.0.0.1:9030')
    connection=pymysql.connect(host='127.0.0.1',port=9030,user='root',password='',autocommit=True,charset='utf8mb4',read_timeout=180,write_timeout=180)
    reader='synth_v2_reader';password=secrets.token_urlsafe(32)
    try:
        with connection.cursor() as c:
            c.execute('SELECT current_version()');report['engine_version']=str(c.fetchone()[0])
            c.execute('SHOW DATABASES')
            if DB in {r[0] for r in c.fetchall()}:raise ValueError('QA database already exists; no overwrite or implicit reuse')
            c.execute('CREATE DATABASE `'+DB+'`')
            c.execute("CREATE USER '"+reader+"' IDENTIFIED BY %s",(password,))
            c.execute('SET enable_insert_strict=true')
            for profile,prefix in [('development','d_'),('holdout','h_'),('shift','s_')]:
                folder=root/profile;catalog=read(folder/'catalog.json');keys=read(folder/'keys.json')
                rows={n:records(folder/'tables'/(n+'.jsonl')) for n in catalog['tables']}
                normalized=source_quality(catalog,rows,keys)
                if not invariants(dict(catalog=catalog,rows=rows))['passed']:raise ValueError('Business controls fail')
                mapping={n:prefix+n for n,meta in catalog['tables'].items() if meta['core']};loaded=0
                for logical,physical in mapping.items():
                    metadata=catalog['tables'][logical]['columns'];columns=list(metadata);definitions=[]
                    for col,meta in metadata.items():
                        typ=meta['type'];typ='VARCHAR(1024)' if typ=='VARCHAR' else 'DATETIME' if typ=='TIMESTAMP' else typ
                        definitions.append('`'+col+'` '+typ)
                    target='`'+DB+'`.`'+physical+'`';first=columns[0]
                    c.execute(f'CREATE TABLE {target} ({",".join(definitions)}) ENGINE=OLAP DUPLICATE KEY(`{first}`) DISTRIBUTED BY HASH(`{first}`) BUCKETS 1 PROPERTIES("replication_num"="1")')
                    for i in range(0,len(rows[logical]),1000):
                        c.executemany(f'INSERT INTO {target} VALUES ({",".join(["%s"]*len(columns))})',[tuple(r[col] for col in columns) for r in normalized[logical][i:i+1000]])
                    c.execute(f'SELECT * FROM {target}');actual=c.fetchall()
                    def key(row):return json.dumps(row,ensure_ascii=False,separators=(',',':'))
                    want=sorted(key([r[col] for col in columns]) for r in normalized[logical])
                    got=sorted(key([normalize(v,metadata[col]['type']) for col,v in zip(columns,row)]) for row in actual)
                    if got!=want:raise ValueError('Physical row reconciliation failed: '+logical)
                    loaded+=len(actual)
                c.execute(f'GRANT SELECT ON ALL TABLES IN DATABASE `{DB}` TO USER {reader}')
                query_results=[]
                with pymysql.connect(host='127.0.0.1',port=9030,user=reader,password=password,database=DB,autocommit=True,charset='utf8mb4',read_timeout=60) as restricted:
                    with restricted.cursor() as rc:
                        rc.execute('SET query_timeout=30')
                        for case in records(folder/'answers.jsonl'):
                            if not case.get('reference_sql'):continue
                            before=time.monotonic()
                            try:
                                rc.execute(bind(case['reference_sql'],mapping));values=[[wire(v) for v in row] for row in rc.fetchall()]
                                columns=[d[0] for d in rc.description]
                                status=judge(dict(case,requires_model=None),dict(status='ok',result=dict(columns=columns,rows=values,truncated=False)))
                                detail={} if status=='passed_value' else dict(expected=case['expected_rows'],actual=values,columns=columns)
                            except Exception as exc:status='sql_error';detail={'error':str(exc)[:1500]}
                            query_results.append(dict(id=case['id'],status=status,seconds=round(time.monotonic()-before,4),**detail))
                failures=[q for q in query_results if q['status']!='passed_value']
                report['profiles'][profile]=dict(loaded_core_tables=len(mapping),loaded_rows=loaded,reference_queries=len(query_results),passed=len(query_results)-len(failures),failures=failures,query_results=query_results)
                print(profile+': '+str(len(query_results)-len(failures))+'/'+str(len(query_results))+' reference SQL checks',flush=True)
            report['passed']=all(not p['failures'] for p in report['profiles'].values())
            report['seconds']=round(time.monotonic()-start,2)
            with (root/'verification.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
            if not report['passed']:raise ValueError('Reference mismatch; see verification.json, do not use for model scoring')
            return report
    finally:
        # Credentials exist in memory only; removing the QA reader leaves data for inspection.
        try:
            with connection.cursor() as c:c.execute('DROP USER IF EXISTS '+reader)
        finally:connection.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',required=True);p.add_argument('--confirm-qa-database',required=True);a=p.parse_args()
    result=verify(a.data,a.confirm_qa_database);print('Reference SQL verification passed; no model inference.')
