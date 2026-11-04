"""Operator-only snapshot reconciliation/quarantine. Never deletes or overwrites tables."""
from contextlib import contextmanager
from decimal import Decimal
from psycopg2.extras import Json
from .store import digest,Conflict
from .settings import name


@contextmanager
def release_lock(store,tenant,release):
    with store.db() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('snapshot:'+tenant+':'+release,))
        yield


def quarantine(store,tenant,release,manifest,approval):
    if not approval:raise ValueError('Quarantine approval required')
    with release_lock(store,tenant,release),store.db() as c:
        c.execute('SELECT 1 FROM analytics_releases WHERE tenant=%s AND id=%s',(tenant,release))
        if c.fetchone():raise Conflict('Staged/published release cannot be quarantined by loader tool')
        c.execute("UPDATE analytics_loads SET state='quarantined',error_code='OPERATOR_QUARANTINED' WHERE tenant=%s AND release=%s AND manifest_hash=%s AND state IN ('pending','failed')",(tenant,release,manifest))
        if c.rowcount!=1:raise Conflict('Load identity or state changed')
        store.event(c,tenant,'snapshot_operator',release,'snapshot_quarantined',{'manifest_hash':manifest,'approval':approval})


def reconcile(settings,store,admin,release,catalog,rows,keys,watermark,approval):
    """Recover a fully loaded unknown-state snapshot; incomplete data needs a new ID."""
    from .loader import source_quality,normalize
    from .regional import subset
    if not approval:raise ValueError('Recovery approval required')
    name(release)
    if 'global' not in settings.scopes or any(k!='global' and not v.get('regions') for k,v in settings.scopes.items()):raise ValueError('Explicit global and regional scopes required')
    if settings.environment in {'staging','production'} and catalog.get('synthetic',True):raise ValueError('Synthetic source cannot be recovered into production')
    rows=source_quality(catalog,rows,keys)
    manifest=digest(dict(catalog=catalog,rows=rows,keys=keys,watermark=watermark))
    with release_lock(store,settings.tenant,release):
        with store.db() as c:
            c.execute('SELECT state,manifest_hash FROM analytics_loads WHERE tenant=%s AND release=%s',(settings.tenant,release));record=c.fetchone()
            if not record or record['state'] not in {'pending','failed'} or record['manifest_hash']!=manifest:raise Conflict('Frozen manifest or recoverable load state mismatch')
            c.execute('SELECT state,catalog,views,watermark FROM analytics_releases WHERE tenant=%s AND id=%s',(settings.tenant,release));staged=c.fetchone()
            if staged and staged['state']!='staged':raise Conflict('Never mutate a published release')
        mappings={};counts={};totals={};grants=[]
        with admin.cursor() as cursor:
            for scope,config in settings.scopes.items():
                expected=rows if scope=='global' else subset(rows,config['regions'])
                prefix='v'+digest([settings.tenant,release] if scope=='global' else [settings.tenant,release,scope])[:12]+'_'
                db=name(config['database']);mapping={};scope_counts={}
                for table,records in expected.items():
                    physical=name(prefix+table);meta=catalog['tables'][table]['columns'];columns=list(meta)
                    target='`'+db+'`.`'+physical+'`'
                    cursor.execute(f'SELECT * FROM {target} LIMIT {len(records)+1}')
                    actual=cursor.fetchall()
                    expected_hashes=sorted(digest([r[c] for c in columns]) for r in records)
                    got=sorted(digest([normalize(v,meta[c]['type']) for c,v in zip(columns,row)]) for row in actual)
                    if got!=expected_hashes:raise ValueError('Incomplete or changed physical snapshot; quarantine and rebuild with new release ID')
                    mapping[table]=physical;scope_counts[table]=len(records)
                    for column,definition in meta.items():
                        if definition['type'].startswith('DECIMAL') or definition['type']=='BIGINT':
                            wanted=sum((Decimal(str(r[column])) for r in records if r[column] is not None),Decimal(0))
                            cursor.execute(f'SELECT SUM(`{name(column)}`) FROM {target}');value=cursor.fetchone()[0]
                            if (Decimal(str(value)) if value is not None else Decimal(0))!=wanted:raise ValueError('Decimal reconciliation mismatch')
                            totals[scope+'.'+table+'.'+column]=str(wanted)
                    grants.append((db,physical,name(config['user'])))
                mappings[scope]=mapping;counts[scope]=scope_counts
            if staged and digest(dict(catalog=staged['catalog'],views=staged['views'],watermark=staged['watermark']))!=digest(dict(catalog=catalog,views=mappings,watermark=watermark)):
                raise Conflict('Staged metadata differs from frozen input')
            for db,physical,user in grants:cursor.execute(f'GRANT SELECT ON TABLE `{db}`.`{physical}` TO USER {user}')
        proof=dict(passed=True,manifest_hash=manifest,source_counts=counts,decimal_totals=totals,recovered=True)
        if not staged:store.stage_release(settings.tenant,release,catalog,mappings,watermark)
        with store.db() as c:
            c.execute("UPDATE analytics_loads SET state='applied',proof=%s,error_code=NULL WHERE tenant=%s AND release=%s AND manifest_hash=%s AND state IN ('pending','failed')",(Json(proof),settings.tenant,release,manifest))
            if c.rowcount!=1:raise Conflict('Load state changed during reconciliation')
            store.event(c,settings.tenant,'snapshot_operator',release,'snapshot_recovered',{'approval':approval,'manifest_hash':manifest})
        return proof
