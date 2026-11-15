"""Immutable approved snapshot loading, independent row reconciliation, no table swaps.

Input is a frozen upstream snapshot, not a live mutable cursor. Failed loads remain
private and unpublished; retry uses a new release ID. No DROP or in-place overwrite.
"""
from datetime import date,datetime
from decimal import Decimal
from psycopg2.extras import Json
from .store import digest,Conflict
from .settings import name
from .identity import Principal
from src.petkit.row_policy import predicates_for

DDL='''CREATE TABLE IF NOT EXISTS analytics_loads(tenant TEXT NOT NULL,release TEXT NOT NULL,
 manifest_hash TEXT NOT NULL,state TEXT NOT NULL,proof JSONB,error_code TEXT,
 created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant,release));'''


def normalize(value,typ):
    if value is None:return None
    if typ.startswith('DECIMAL'):
        if isinstance(value,(float,bool)):raise ValueError('Source money requires decimal text, integer or Decimal')
        d=Decimal(str(value));rounded=d.quantize(Decimal('.01'))
        if not d.is_finite() or d!=rounded or abs(d)>=Decimal('1000000000000000000'):raise ValueError('Invalid DECIMAL(20,2)')
        return format(rounded,'f')
    if typ=='BIGINT':
        if type(value)!=int or not -(2**63)<=value<2**63:raise ValueError('Invalid BIGINT')
        return value
    if typ=='DATE':return date.fromisoformat(str(value)).isoformat()
    if typ in {'TIMESTAMP','DATETIME'}:return datetime.fromisoformat(str(value)).isoformat(sep=' ')
    if typ=='VARCHAR':
        if not isinstance(value,str) or len(value.encode())>1024:raise ValueError('Invalid VARCHAR')
        return value
    raise ValueError('Unapproved physical column type')


def source_quality(catalog,rows,keys):
    if len(rows)>500 or sum(len(r) for r in rows.values())>200000 or any(len(r)>50000 for r in rows.values()):raise ValueError('Snapshot exceeds bounded loader capacity; use upstream warehouse pipeline')
    normalized={}
    for table,records in rows.items():
        name(table)
        meta=catalog['tables'][table];columns=list(meta['columns']);seen=set();output=[]
        if not keys.get(table) or not set(keys[table])<=set(columns):raise ValueError('Explicit business key required')
        for record in records:
            if set(record)!=set(columns):raise ValueError('Schema drift')
            normalized_row={col:normalize(record[col],meta['columns'][col]['type']) for col in columns}
            key=tuple(normalized_row[col] for col in keys[table])
            if None in key or key in seen:raise ValueError('Null/duplicate business key')
            seen.add(key);output.append(normalized_row)
        normalized[table]=output
    for edge in catalog['relationships']:
        if edge['left'] not in rows:continue
        if edge['right'] not in rows:raise ValueError('Missing parent snapshot')
        parent=[r[edge['right_key']] for r in normalized[edge['right']]]
        parent_set=set(parent)
        if len(parent_set)!=len(parent):raise ValueError('Join parent key is not unique')
        if any(r[edge['left_key']] is not None and r[edge['left_key']] not in parent_set for r in normalized[edge['left']]):
            raise ValueError('Orphan foreign key')
    return normalized


def summarize_channel_coverage(channels):
    if not isinstance(channels, dict) or not channels or any(
        not isinstance(channel, str) or not channel.strip() or type(verified) is not bool
        for channel, verified in channels.items()
    ):
        raise ValueError('Expected channel names and verification flags')
    covered = sorted(channel for channel, verified in channels.items() if verified)
    return {
        'status': 'complete' if len(covered) == len(channels) else 'incomplete',
        'covered_channels': covered,
    }


def with_channel_coverage(watermark, scopes):
    """Derive release coverage from connector-declared partition reconciliation."""
    partitions = watermark.get('net_sales_partitions')
    if partitions is None:
        return watermark
    if not isinstance(partitions, dict) or set(partitions) - set(scopes):
        raise ValueError('Invalid net sales coverage scopes')
    coverage = {}
    for scope, channels in partitions.items():
        if isinstance(channels, dict) and set(channels) == {'by_month'}:
            from datetime import date

            months = channels['by_month']
            if not isinstance(months, dict) or not months:
                raise ValueError('Monthly channel coverage cannot be empty')
            monthly = {}
            for month, entries in months.items():
                if not isinstance(month, str) or len(month) != 7 or date.fromisoformat(month + '-01').isoformat()[:7] != month:
                    raise ValueError('Coverage month must use YYYY-MM')
                if not isinstance(entries, dict) or not entries:
                    raise ValueError('Expected channels for each coverage month')
                for sources in entries.values():
                    if not isinstance(sources, dict) or set(sources) != {'sales', 'refunds'}:
                        raise ValueError('Sales and refunds reconciliation flags are required')
                    if any(type(flag) is not bool for flag in sources.values()):
                        raise ValueError('Reconciliation flags must be boolean')
                monthly[month] = summarize_channel_coverage({
                    channel: all(sources.values()) for channel, sources in entries.items()
                })
            coverage[scope] = {'by_month': monthly}
        else:
            coverage[scope] = summarize_channel_coverage(channels)
    result = dict(watermark)
    result.pop('net_sales_partitions')
    result['metric_coverage'] = {**watermark.get('metric_coverage', {}), 'net_sales': coverage}
    return result


def reconcile_net_sales(cursor, database, mapping, rows):
    """Compare source-derived channel/month amounts with the loaded snapshot."""
    required = {'fact_order_item', 'fact_order', 'fact_refund', 'dim_channel'}
    if not required <= rows.keys():
        return None
    orders = {row['order_id']: row for row in rows['fact_order']}
    channels = {row['channel_id']: row['channel_name'] for row in rows['dim_channel']}
    refunded = {}
    for row in rows['fact_refund']:
        key = row['item_id']
        refunded[key] = refunded.get(key, Decimal(0)) + Decimal(str(row['amount']))
    expected = {}
    for item in rows['fact_order_item']:
        order = orders[item['order_id']]
        key = (str(order['paid_date'])[:7], channels[order['channel_id']])
        expected[key] = expected.get(key, Decimal(0)) + Decimal(str(item['amount'])) - refunded.get(item['item_id'], Decimal(0))

    def table(logical):
        return f"`{name(database)}`.`{name(mapping[logical])}`"

    cursor.execute(f"""
        WITH refunds AS (SELECT item_id,SUM(amount) AS refunded
                         FROM {table('fact_refund')} GROUP BY item_id)
        SELECT DATE_FORMAT(o.paid_date,'%Y-%m'),c.channel_name,
               SUM(i.amount-COALESCE(r.refunded,0))
        FROM {table('fact_order_item')} i
        JOIN {table('fact_order')} o ON i.order_id=o.order_id
        JOIN {table('dim_channel')} c ON o.channel_id=c.channel_id
        LEFT JOIN refunds r ON i.item_id=r.item_id
        GROUP BY DATE_FORMAT(o.paid_date,'%Y-%m'),c.channel_name
    """)
    actual = {(month, channel): Decimal(str(amount)) for month, channel, amount in cursor.fetchall()}
    if actual != expected:
        raise ValueError('Independent net sales channel/month reconciliation failed')
    return {'passed': True, 'channel_month_groups': len(expected)}


def load(settings,store,admin,release,catalog,rows,keys,watermark):
    from .recovery import release_lock
    with release_lock(store,settings.tenant,release):
        return _load(settings,store,admin,release,catalog,rows,keys,watermark)


def _load(settings,store,admin,release,catalog,rows,keys,watermark):
    name(release)
    if 'global' not in settings.scopes:raise ValueError('Approved global source scope required for snapshot materialization')
    if not all(k in watermark for k in ('source_version','event_time','ingested_at','batch_id','source_kind')):
        raise ValueError('Complete source watermark required')
    if settings.environment in {'staging','production'} and catalog.get('synthetic',True):raise ValueError('Synthetic snapshot cannot be published as enterprise data')
    watermark=with_channel_coverage(watermark,settings.scopes)
    rows=source_quality(catalog,rows,keys)
    manifest=digest(dict(catalog=catalog,rows=rows,keys=keys,watermark=watermark))
    with store.db() as c:
        c.execute(DDL)
        c.execute("INSERT INTO analytics_loads(tenant,release,manifest_hash,state) VALUES(%s,%s,%s,'pending')",(settings.tenant,release,manifest))
    prefix='v'+digest([settings.tenant,release])[:12]+'_';mapping={};counts={};totals={}
    db=name(settings.scopes['global']['database'])
    try:
        with admin.cursor() as cursor:
            for logical,records in rows.items():
                physical=name(prefix+logical);meta=catalog['tables'][logical];columns=list(meta['columns']);definitions=[]
                for i,col in enumerate(columns):
                    typ=meta['columns'][col]['type']
                    if typ=='VARCHAR':typ='VARCHAR(256)' if i==0 else 'VARCHAR(1024)'
                    if typ=='TIMESTAMP':typ='DATETIME'
                    definitions.append('`'+name(col)+'` '+typ)
                target='`'+db+'`.`'+physical+'`';first=name(columns[0])
                replication='3' if settings.environment in {'staging','production'} else '1'
                cursor.execute(f'CREATE TABLE {target} ({",".join(definitions)}) ENGINE=OLAP DUPLICATE KEY(`{first}`) DISTRIBUTED BY HASH(`{first}`) BUCKETS 1 PROPERTIES("replication_num"="{replication}")')
                for start in range(0,len(records),2000):
                    cursor.executemany(f'INSERT INTO {target} VALUES ({",".join(["%s"]*len(columns))})',[tuple(r[c] for c in columns) for r in records[start:start+2000]])
                cursor.execute(f'SELECT * FROM {target} LIMIT {len(records)+1}');actual=cursor.fetchall()
                expected_rows=sorted(digest([r[c] for c in columns]) for r in records)
                actual_rows=sorted(digest([normalize(v,meta['columns'][c]['type']) for c,v in zip(columns,row)]) for row in actual)
                if actual_rows!=expected_rows:raise ValueError('Independent source/StarRocks row reconciliation failed')
                mapping[logical]=physical;counts[logical]=len(records)
                for col,typ in meta['columns'].items():
                    if typ['type'].startswith('DECIMAL') or typ['type']=='BIGINT':
                        value=sum((Decimal(str(r[col])) for r in records if r[col] is not None),Decimal(0))
                        cursor.execute(f'SELECT SUM(`{name(col)}`) FROM {target}')
                        actual_total=cursor.fetchone()[0]
                        if (Decimal(str(actual_total)) if actual_total is not None else Decimal(0))!=value:raise ValueError('Independent Decimal control total mismatch')
                        totals[logical+'.'+col]=str(value)
            net_sales_proofs = {'global': reconcile_net_sales(cursor, db, mapping, rows)}
            all_mappings={'global':mapping};all_counts={'global':counts}
            pending_grants=[(db,physical,name(settings.scopes['global']['user'])) for physical in mapping.values()]
            for scope,config in settings.scopes.items():
                if scope=='global':continue
                if not config.get('regions'):raise ValueError('Regional snapshot needs explicit region codes')
                from .regional import subset
                expected=subset(rows,config['regions']);regional_mapping={};regional_counts={}
                principal=Principal('snapshot_operator',settings.tenant,'analyst',tuple(config['regions']),scope,1)
                predicates=predicates_for(principal,lambda n:'`'+db+'`.`'+mapping.get(n,prefix+n)+'`')
                scoped_db=name(config['database'])
                for logical,records in expected.items():
                    physical=name('v'+digest([settings.tenant,release,scope])[:12]+'_'+logical)
                    target='`'+scoped_db+'`.`'+physical+'`'
                    source='`'+db+'`.`'+mapping[logical]+'`'
                    cursor.execute(f'CREATE TABLE {target} LIKE {source}')
                    cursor.execute(f'INSERT INTO {target} SELECT * FROM {source} WHERE {predicates[logical]}')
                    columns=list(catalog['tables'][logical]['columns']);metadata=catalog['tables'][logical]['columns']
                    cursor.execute(f'SELECT * FROM {target} LIMIT {len(records)+1}')
                    actual=cursor.fetchall()
                    wanted=sorted(digest([r[c] for c in columns]) for r in records)
                    got=sorted(digest([normalize(v,metadata[c]['type']) for c,v in zip(columns,row)]) for row in actual)
                    if got!=wanted:raise ValueError('Independent regional lineage reconciliation failed')
                    regional_mapping[logical]=physical;regional_counts[logical]=len(records)
                    pending_grants.append((scoped_db,physical,name(config['user'])))
                net_sales_proofs[scope] = reconcile_net_sales(cursor, scoped_db, regional_mapping, expected)
                all_mappings[scope]=regional_mapping;all_counts[scope]=regional_counts
            for scope_db,physical,user in pending_grants:
                cursor.execute(f'GRANT SELECT ON TABLE `{scope_db}`.`{physical}` TO USER {user}')
        proof=dict(passed=True,manifest_hash=manifest,source_counts=all_counts,decimal_totals=totals,net_sales_reconciliation=net_sales_proofs)
        store.stage_release(settings.tenant,release,catalog,all_mappings,watermark)
        with store.db() as c:
            c.execute("UPDATE analytics_loads SET state='applied',proof=%s WHERE tenant=%s AND release=%s",(Json(proof),settings.tenant,release))
            store.event(c,settings.tenant,'ingestion',release,'snapshot_reconciled',{'manifest_hash':manifest,'tables':len(mapping)})
        return proof
    except Exception:
        with store.db() as c:c.execute("UPDATE analytics_loads SET state='failed',error_code='LOAD_OR_RECONCILIATION_FAILED' WHERE tenant=%s AND release=%s",(settings.tenant,release))
        raise
