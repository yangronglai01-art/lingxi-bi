"""Source-neutral CDC batch validation and replay-safe materialization ledger.

The source connector supplies complete keyed after-images. Deletes are tombstones.
This layer does not claim to be an ERP connector or a Flink deployment.
"""
from datetime import datetime
from psycopg2.extras import Json
from .store import digest,Conflict
from .settings import name

DDL='''
CREATE TABLE IF NOT EXISTS analytics_batches(tenant TEXT NOT NULL,source TEXT NOT NULL,batch TEXT NOT NULL,
 fingerprint TEXT NOT NULL,state TEXT NOT NULL,watermark JSONB NOT NULL,release TEXT,error_code TEXT,
 created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant,source,batch));
CREATE TABLE IF NOT EXISTS analytics_source_rows(tenant TEXT NOT NULL,source TEXT NOT NULL,table_name TEXT NOT NULL,
 row_key TEXT NOT NULL,source_version BIGINT NOT NULL,deleted BOOLEAN NOT NULL,payload JSONB,
 event_time TIMESTAMPTZ NOT NULL,ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant,source,table_name,row_key));
'''


class Batches:
    def __init__(self,store):self.store=store
    def migrate(self):
        with self.store.db() as c:c.execute(DDL)

    def apply(self,tenant,source,batch,events,schema,watermark):
        name(source);name(batch)
        if not events or len(events)>10000:raise ValueError('Batch size must be 1..10000')
        if not all(k in watermark for k in ('source_version','event_time','batch_id')) or watermark['batch_id']!=batch:
            raise ValueError('Explicit source watermark required')
        fingerprint=digest({'events':events,'watermark':watermark})
        validated=[]
        for e in events:
            table=e['table'];version=e['source_version'];key=e['key'];op=e['op'];payload=e.get('after')
            if table not in schema or type(version)!=int or version<0 or not isinstance(key,str) or not 1<=len(key)<=256 or op not in {'upsert','delete'}:
                raise ValueError('Invalid CDC event identity')
            when=datetime.fromisoformat(e['event_time'].replace('Z','+00:00'))
            if not when.tzinfo:raise ValueError('Timezone-qualified event_time required')
            if op=='upsert' and (not isinstance(payload,dict) or set(payload)!=set(schema[table])):
                raise ValueError('Schema drift or incomplete after-image')
            validated.append((table,key,version,op=='delete',payload,when))
        with self.store.db() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('cdc:'+tenant+':'+source,))
            c.execute('SELECT fingerprint,state FROM analytics_batches WHERE tenant=%s AND source=%s AND batch=%s',(tenant,source,batch));old=c.fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise Conflict('Batch ID payload mismatch')
                return old['state']
            c.execute("INSERT INTO analytics_batches(tenant,source,batch,fingerprint,state,watermark) VALUES(%s,%s,%s,%s,'pending',%s)",(tenant,source,batch,fingerprint,Json(watermark)))
            for table,key,version,deleted,payload,when in validated:
                c.execute('SELECT * FROM analytics_source_rows WHERE tenant=%s AND source=%s AND table_name=%s AND row_key=%s FOR UPDATE',(tenant,source,table,key));previous=c.fetchone()
                if previous and previous['source_version']==version:
                    if previous['deleted']!=deleted or previous['payload']!=payload:raise Conflict('Same source version has conflicting content')
                    continue
                if previous and previous['source_version']>version:continue
                c.execute('''INSERT INTO analytics_source_rows(tenant,source,table_name,row_key,source_version,deleted,payload,event_time)
                 VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant,source,table_name,row_key) DO UPDATE SET
                 source_version=excluded.source_version,deleted=excluded.deleted,payload=excluded.payload,event_time=excluded.event_time,ingested_at=clock_timestamp()''',
                 (tenant,source,table,key,version,deleted,Json(payload) if payload is not None else None,when))
            self.store.event(c,tenant,'ingestion',batch,'batch_staged',{'source':source,'events':len(events)})
        return 'pending'

    def materialized(self,tenant,source,batch,release,reconciliation):
        # Publication is separate: readers only see a verified immutable release.
        if not reconciliation.get('passed') or not reconciliation.get('manifest_hash'):
            raise ValueError('Independent reconciliation proof required')
        with self.store.db() as c:
            c.execute("UPDATE analytics_batches SET state='applied',release=%s WHERE tenant=%s AND source=%s AND batch=%s AND state='pending'",(release,tenant,source,batch))
            if c.rowcount!=1:raise Conflict('Batch is not pending')
            self.store.event(c,tenant,'ingestion',batch,'batch_materialized',{'release':release,'manifest_hash':reconciliation['manifest_hash']})
