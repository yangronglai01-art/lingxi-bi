"""Explicit isolated integration fixture; never a production data migration."""
import json,os,sys,time
from pathlib import Path
from datetime import date
from decimal import Decimal
import pymysql,psycopg2
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.petkit.catalog import catalog_payload
from src.petkit.builder import generate
from src.enterprise.store import Store
from src.enterprise.identity import Principal
from src.petkit.row_policy import predicates_for

DATABASE='lingxi_enterprise_qa'
if os.environ.get('LINGXI_QA_CONFIRM')!=DATABASE:raise SystemExit('Explicit QA confirmation required')
# Public disposable QA credentials, not enterprise secrets.
PGDSN=os.environ.get('LINGXI_QA_DSN','postgresql://analytics_qa:analytics_qa_only@127.0.0.1:56103/lingxi_enterprise_qa')
from urllib.parse import urlsplit
if urlsplit(PGDSN).path!='/'+DATABASE or urlsplit(PGDSN).hostname not in {'127.0.0.1','localhost'}:raise SystemExit('Only loopback QA database is allowed')
raw=generate();catalog=catalog_payload();catalog.update(as_of='2026-10-16',generation='qa_v1')
for n,t in catalog['tables'].items():
    for column,meta in t['columns'].items():
        if meta['type']=='DOUBLE':meta['type']='DECIMAL(20,2)'
        if column in {'status','category','channel_name','fault_name','product_name','region_name'} and n in raw:
            i=list(t['columns']).index(column);meta['enum']=sorted({r[i] for r in raw[n]})
    if n not in raw:
        offset=sum(map(ord,n))%91
        for i in range(12):
            dt=date(2025+(9+i)//12,(9+i)%12+1,1)
            for pid,*_ in raw['dim_product']:
                count=offset+i+int(pid[1:])+1
                raw[n].append((dt,pid,count,round(count*(1+offset/10),2),catalog['version']))
admin=pymysql.connect(host='127.0.0.1',port=int(os.environ.get('LINGXI_QA_SR_PORT','9030')),user='root',autocommit=True)
with admin.cursor() as c:
    c.execute('SHOW DATABASES');existing={r[0] for r in c.fetchall()}
    if DATABASE in existing:raise SystemExit('QA database already exists; refuse to overwrite. Reuse verification instead.')
    c.execute(f'CREATE DATABASE `{DATABASE}`');c.execute(f'USE `{DATABASE}`')
    for n,t in catalog['tables'].items():
        physical='b_'+n
        cols=[]
        for i,(column,meta) in enumerate(t['columns'].items()):
            typ=meta['type']
            if typ=='VARCHAR':typ='VARCHAR(256)' if i==0 else 'VARCHAR(1024)'
            if typ=='TIMESTAMP':typ='DATETIME'
            cols.append(f'`{column}` {typ}')
        first=next(iter(t['columns']))
        c.execute(f'CREATE TABLE `{physical}` ({",".join(cols)}) ENGINE=OLAP DUPLICATE KEY(`{first}`) DISTRIBUTED BY HASH(`{first}`) BUCKETS 1 PROPERTIES("replication_num"="1")')
        rows=raw[n]
        for start in range(0,len(rows),2000):
            values=[tuple(Decimal(str(v)) if isinstance(v,float) else v for v in row) for row in rows[start:start+2000]]
            c.executemany(f'INSERT INTO `{physical}` VALUES ({",".join(["%s"]*len(cols))})',values)
    mappings={}
    for scope,regions in [('global',()),('region_r1',('R1',))]:
        user='analytics_qa_'+scope;password='analytics_qa_'+scope+'_only'
        c.execute(f"CREATE USER '{user}' IDENTIFIED BY '{password}'")
        p=Principal('fixture','petkit','analyst',regions,scope,1)
        predicates=predicates_for(p,lambda n:'`b_'+n+'`') if regions else {n:'TRUE' for n in catalog['tables']}
        mapping={}
        for n,predicate in predicates.items():
            physical=('g_' if not regions else 'r1_')+n
            # Scope-owned physical snapshots avoid claiming unverified native row policies.
            c.execute(f'CREATE TABLE `{physical}` LIKE `b_{n}`')
            c.execute(f'INSERT INTO `{physical}` SELECT * FROM `b_{n}` WHERE {predicate}')
            c.execute(f'GRANT SELECT ON TABLE `{DATABASE}`.`{physical}` TO USER {user}')
            mapping[n]=physical
        mappings[scope]=mapping
admin.close()
store=Store(PGDSN);store.migrate()
store.grant('petkit','alice','analyst',[],'global')
store.grant('petkit','bob','analyst',['R1'],'region_r1')
store.stage_release('petkit','qa_v1',catalog,mappings,dict(source_kind='synthetic-integration',batch_id='qa_v1',event_time='2026-10-15T00:00:00+08:00',ingested_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()))
store.publish('petkit','qa_v1',None,'isolated fixture; not business approval')
report=ROOT/'reports/enterprise/20261023';report.mkdir(parents=True,exist_ok=True)
(report/'fixture.json').write_text(json.dumps(dict(database=DATABASE,tables=len(catalog['tables']),source_rows=sum(map(len,raw.values())),source_kind='synthetic-integration'),indent=2),encoding='utf-8')
print('QA fixture ready',len(catalog['tables']),sum(map(len,raw.values())))

with store.db() as c:
    c.execute("SELECT 1 FROM pg_roles WHERE rolname='analytics_runtime'")
    if not c.fetchone():c.execute("CREATE ROLE analytics_runtime LOGIN PASSWORD 'analytics_runtime_qa_only'")
    c.execute((ROOT/'deploy/enterprise/runtime-grants.sql').read_text(encoding='utf-8'))
