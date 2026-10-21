"""Sealed snapshot validation and crash-reconcilable single-instance publication."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
from .settings import DATA,ROOT,AS_OF

@lru_cache(maxsize=8)
def guard(root):
    from filelock import FileLock
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    return FileLock(str(root/'release.lock'),timeout=30)

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(temp,path)
    finally: temp.unlink(missing_ok=True)

def folder_for(generation,root=None):
    root=Path(root or DATA).resolve()
    if not re.fullmatch(r'petkit-v[0-9.]+-[a-f0-9]{10}',generation): raise ValueError('快照版本名称无效')
    folder=(root/generation).resolve()
    if folder.parent!=root or not folder.is_dir(): raise ValueError('快照目录不存在或越界')
    return folder

def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): value.update(block)
    return value.hexdigest()

def code_seal():
    names=['releases','reference','planning','guard','semantic','result_contract','row_policy','retrieval','embedding',
        'builder','catalog','control','access','execution','worker','settings','service','ingestion','ingestion_models','fulfillment','procurement','catalog_release','api','operations_api','supervision','health_checks','diagnostics','operations_ui','operations_panel']
    return {**{name:sha(ROOT/'src/petkit'/f'{name}.py') for name in names},'simulated_launcher':sha(ROOT/'scripts/start_simulated.py')}

def file_seal(folder):
    names=['warehouse.duckdb','catalog.json','summary.json']
    if (folder/'lineage.json').exists(): names.append('lineage.json')
    if (folder/'metadata.json').exists(): names.append('metadata.json')
    return {name:sha(folder/name) for name in names}

def embedding_config(root):
    path=Path(root)/'embedding_runtime.json'
    if not path.exists(): return {'model':'','signature':''}
    model=json.loads(path.read_text(encoding='utf-8'))['model']
    from .embedding import signature
    return dict(model=model,signature=signature(model))

def check_regions(conn):
    from . import control
    if not control.path().exists(): return
    with control.db() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        rows=c.execute('SELECT regions FROM users WHERE enabled=1').fetchall() if exists else []
    known={r[0] for r in conn.execute('SELECT region_id FROM dim_region').fetchall()}
    if any(set(json.loads(r[0]))-known for r in rows): raise ValueError('候选数据缺少已授权员工的地区')

def validate(generation):
    import duckdb
    from .reference import expected
    from .planning import examples
    from .guard import check
    from .semantic import validate_metric
    from .result_contract import validate as validate_result
    from src.engine.executor import QueryResult
    folder=folder_for(generation)
    receipt=dict(id=uuid.uuid4().hex[:12],generation=generation,status='rejected',started=time.time(),gates={})
    target=DATA/'releases'/(generation+'.json')
    from filelock import FileLock
    target.parent.mkdir(parents=True,exist_ok=True)
    with FileLock(str(target)+'.lock',timeout=30):
        before=file_seal(folder); initial_code=code_seal()
        try:
            cat=json.loads((folder/'catalog.json').read_text(encoding='utf-8'))
            metadata_only=(folder/'metadata.json').exists()
            if metadata_only:
                from .catalog_release import validate as validate_catalog
                receipt['gates']['catalog_upgrade']=validate_catalog(folder)
            if (folder/'lineage.json').exists():
                history=json.loads((folder/'lineage.json').read_text(encoding='utf-8'))
                if history.get('generation')!=generation or history.get('version')!=1: raise ValueError('血缘版本与快照不一致')
                from . import control
                with control.db() as c:
                    attempt=c.execute('SELECT state,candidate FROM ingestion_attempts WHERE id=?',(history.get('last_attempt'),)).fetchone()
                    if not metadata_only and (not attempt or attempt['state']!='staged' or attempt['candidate']!=generation): raise ValueError('候选接入未完成或已隔离')
                receipt['gates']['lineage']=dict(events=len(history['events']),batches=len(history['batches']),parent=history['parent_generation'])
            summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
            if cat['generation']!=generation or summary['generation']!=generation or cat['as_of']!=AS_OF: raise ValueError('快照版本或观察截止日与当前代码不一致')
            with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True,config={'enable_external_access':'false'}) as conn:
                names={r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
                if len(names)!=500 or names!=set(cat['tables']): raise ValueError('物理表与500表目录不一致')
                total=0; raw={}
                composite={'fact_campaign_day':['campaign_id','event_date'],'fact_device_day':['device_id','event_date'],'fact_inventory_day':['stock_date','sku_id','warehouse_id'],
                    'fact_product_price':['sku_id','effective_date'],'fact_product_cost':['sku_id','effective_date']}
                for name,t in cat['tables'].items():
                    columns=[(r[0],r[1]) for r in conn.execute(f'DESCRIBE "{name}"').fetchall()]
                    if columns!=[(k,v['type']) for k,v in t['columns'].items()]: raise ValueError('字段或类型不一致：'+name)
                    count=conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                    if count<=0 or count!=summary['counts'][name]: raise ValueError('数据量摘要不一致：'+name)
                    total+=count
                    if t['core']:
                        keys=composite.get(name,[next(iter(t['columns']))]); group=','.join('"'+k+'"' for k in keys)
                        nulls=' OR '.join('"'+k+'" IS NULL' for k in keys)
                        if conn.execute(f'SELECT 1 FROM "{name}" WHERE {nulls} LIMIT 1').fetchone(): raise ValueError('主键为空：'+name)
                        if conn.execute(f'SELECT 1 FROM "{name}" GROUP BY {group} HAVING count(*)>1 LIMIT 1').fetchone(): raise ValueError('粒度重复：'+name)
                        cur=conn.execute(f'SELECT * FROM "{name}"'); fields=[d[0] for d in cur.description]
                        raw[name]=[dict(zip(fields,row)) for row in cur.fetchall()]
                if total!=summary['total_rows'] or summary['orders']!=len(raw['fact_order']): raise ValueError('总行数或订单摘要不一致')
                for r in cat['relationships']:
                    sql=f'''SELECT count(*) FROM "{r['left']}" a LEFT JOIN "{r['right']}" b ON a."{r['left_key']}"=b."{r['right_key']}" WHERE a."{r['left_key']}" IS NOT NULL AND b."{r['right_key']}" IS NULL'''
                    if conn.execute(sql).fetchone()[0]: raise ValueError('悬空关联：'+r['left']+'.'+r['left_key'])
                orders={r['order_id']:r for r in raw['fact_order']}; items={r['item_id']:r for r in raw['fact_order_item']}
                from collections import defaultdict
                totals=defaultdict(float); payments=defaultdict(float); refunds=defaultdict(float); sold=defaultdict(int)
                for r in items.values():
                    if r['amount']<0 or r['quantity']<=0: raise ValueError('明细金额或数量无效')
                    totals[r['order_id']]+=r['amount']
                for r in raw['fact_payment']: payments[r['order_id']]+=r['amount']
                for r in raw['fact_refund']:
                    if r['amount']<0: raise ValueError('退款金额无效')
                    refunds[r['item_id']]+=r['amount']
                    if not orders[items[r['item_id']]['order_id']]['paid_date']<=r['refund_date']<=__import__('datetime').date.fromisoformat(AS_OF): raise ValueError('退款日期错误')
                for r in raw['bridge_order_device']: sold[r['item_id']]+=1
                if any(abs(totals[k]-r['paid_amount'])>.011 or abs(payments[k]-r['paid_amount'])>.011 for k,r in orders.items()): raise ValueError('订单支付金额不一致')
                if any(refunds[k]>r['amount']+.011 or sold[k]!=r['quantity'] for k,r in items.items()): raise ValueError('退款或设备数量不一致')
                movements=defaultdict(list)
                for r in raw['fact_stock_movement']: movements[r['sku_id'],r['warehouse_id']].append(r)
                for r in raw['fact_inventory_day']:
                    balance=sum(x['quantity'] for x in movements[r['sku_id'],r['warehouse_id']] if x['movement_date']<=r['stock_date'])
                    if balance!=r['quantity'] or balance<0: raise ValueError('库存账实不一致')
                from .fulfillment import check_inventory
                check_inventory(conn)
                from .procurement import check_procurement
                check_procurement(conn)
                check_regions(conn)
                if (folder/'lineage.json').exists():
                    from .ingestion import validate_lineage
                    receipt['gates']['source_reconciliation']=validate_lineage(conn,history)
                    receipt['source_files']=receipt['gates']['source_reconciliation']['source_files']
                receipt['gates']['data_quality']=dict(tables=500,total_rows=total,orders=len(orders),foreign_keys=len(cat['relationships']))
                metrics={m['id']:m for m in cat['metrics']}
                from .catalog import METRICS
                known={m['id'] for m in METRICS}
                if not {'net_sales','activation30','service30','offline_rate','tickets','inventory'}<=set(metrics) or not set(metrics)<=known or len(metrics)!=len(cat['metrics']): raise ValueError('指标目录缺少基础口径或包含未知/重复指标')
                cases=[case for case in examples() if case['metric_id'] in metrics]
                for case in cases:
                    ctx=dict(tables=cat['tables'],relationships=cat['relationships'],metric=metrics[case['metric_id']],data_as_of=cat['as_of'])
                    sql=check(case['sql'],ctx).sql; validate_metric(sql,ctx)
                    cur=conn.execute(sql); values=cur.fetchall(); columns=[d[0] for d in cur.description]
                    observed={row[0]:row[1:] for row in values}; reference=expected(case,raw)
                    if observed.keys()!=reference.keys() or len(observed)!=len(values): raise ValueError('业务分组错误：'+case['question'])
                    for key,wanted in reference.items():
                        if len(wanted)!=len(observed[key]) or any(abs(float(a)-float(b))>.011 for a,b in zip(observed[key],wanted)): raise ValueError('业务结果错误：'+case['question'])
                    validate_result(ctx['metric'],QueryResult(columns=columns,rows=[list(r) for r in values],row_count=len(values)))
                receipt['gates']['business_oracle']=dict(passed=len(cases),source='independent Python arithmetic over raw records')
            config=embedding_config(DATA)
            if config['model']:
                from .retrieval import Index
                index=Index(cat,config['model'])
                if not index.search('各型号净销售额',set(cat['tables'])): raise ValueError('语义检索未返回候选')
                receipt['gates']['embedding']=dict(model=config['model'],vectors=len(index.dense),dimensions=index.dense.shape[1])
            else: receipt['gates']['embedding']=dict(mode='lexical baseline; no local model configured')
            if before!=file_seal(folder) or initial_code!=code_seal(): raise ValueError('验证过程中快照或关键代码发生变化')
            receipt.update(status='validated',files=before,code=initial_code,embedding=config,completed=time.time())
        except Exception as exc:
            receipt.update(error=type(exc).__name__+': '+str(exc),completed=time.time())
        write(DATA/'validation_runs'/(generation+'-'+receipt['id']+'.json'),receipt)
        write(target,receipt)
    if receipt['status']!='validated': raise ValueError(receipt['error'])
    return receipt

def check_receipt(generation,root):
    folder=folder_for(generation,root)
    receipt=json.loads((Path(root)/'releases'/(generation+'.json')).read_text(encoding='utf-8'))
    if receipt['status']!='validated' or receipt['files']!=file_seal(folder) or receipt['code']!=code_seal() or receipt['embedding']!=embedding_config(root):
        raise ValueError('发布凭证缺失、失效或快照已被修改，请重新校验')
    for relative,expected in receipt.get('source_files',{}).items():
        source=(Path(root)/relative).resolve()
        if not source.is_relative_to((Path(root)/'ingestion/archive').resolve()) or sha(source)!=expected:
            raise ValueError('来源批次归档在验证后发生变化')
    return receipt

def reconcile_locked(root):
    from . import control
    root=Path(root); journal=root/'release_journal.json'
    if not journal.exists(): return None
    state=json.loads(journal.read_text(encoding='utf-8'))
    if state['state']!='prepared': return state
    current=json.loads((root/'current.json').read_text(encoding='utf-8'))
    if current==state['previous']:
        state['state']='aborted'; write(journal,state); return state
    if current!=state['target']: raise RuntimeError('发布日志与当前指针冲突，拒绝继续领取任务')
    check_receipt(current['generation'],root)
    with control.db() as c:
        c.execute('BEGIN IMMEDIATE')
        key='release:'+state['id']
        if not c.execute('SELECT 1 FROM runtime WHERE name=?',(key,)).fetchone():
            c.execute("UPDATE jobs SET state='superseded',error='数据版本已发布，请重新提交',updated=? WHERE state='queued' AND generation<>?",(time.time(),current['generation']))
            c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',(time.time(),state['actor'],'data_release',json.dumps(state,ensure_ascii=False)))
            c.execute('INSERT INTO runtime VALUES(?,?,?)',(key,json.dumps(current),time.time()))
    state['state']='committed'; state['completed']=time.time()
    write(root/'release_history'/(state['id']+'.json'),state)
    write(journal,state)
    return state

def activate(generation,actor='local-operator',expected_current=None,allow_historical=False):
    from . import control
    with guard(str(DATA)):
        reconcile_locked(DATA)
        receipt=check_receipt(generation,DATA)
        previous=json.loads((DATA/'current.json').read_text(encoding='utf-8'))
        if expected_current is not None and previous['generation']!=expected_current: raise ValueError('当前版本已变化，拒绝覆盖')
        if previous['generation']==generation: return dict(state='unchanged',target=previous)
        metadata_path=folder_for(generation)/'metadata.json'
        if metadata_path.exists() and not allow_historical:
            parent=json.loads(metadata_path.read_text(encoding='utf-8'))['parent']
            if expected_current is None or previous['generation']!=parent: raise ValueError('指标目录候选必须基于当前父版本发布')
        lineage_path=folder_for(generation)/'lineage.json'
        active_lineage=folder_for(previous['generation'])/'lineage.json'
        if active_lineage.exists() and not allow_historical:
            applied=json.loads(active_lineage.read_text(encoding='utf-8')).get('events',{})
            proposed=json.loads(lineage_path.read_text(encoding='utf-8')).get('events',{}) if lineage_path.exists() else {}
            if any(proposed.get(key)!=value for key,value in applied.items()):
                raise ValueError('新版本缺少当前已应用事件或改写事件内容；请重放接入记录，历史回滚必须显式执行')
        if lineage_path.exists() and not allow_historical:
            parent=json.loads(lineage_path.read_text(encoding='utf-8'))['parent_generation']
            if expected_current is None or previous['generation']!=parent: raise ValueError('接入候选必须基于当前父版本发布；请重新接入或显式回滚')
        import duckdb
        with duckdb.connect(str(folder_for(generation)/'warehouse.duckdb'),read_only=True) as conn: check_regions(conn)
        with control.db() as c:
            if c.execute("SELECT count(*) FROM jobs WHERE state='running'").fetchone()[0]: raise ValueError('仍有执行中任务，暂不发布；请等待完成或中断恢复')
        cat=json.loads((folder_for(generation)/'catalog.json').read_text(encoding='utf-8'))
        state=dict(id=uuid.uuid4().hex,state='prepared',created=time.time(),actor=actor,validation_id=receipt.get('id'),snapshot_files=receipt.get('files'),previous=previous,target=dict(generation=generation,version=cat['version']))
        write(DATA/'release_journal.json',state)
        write(DATA/'current.json',state['target'])
        return reconcile_locked(DATA)
