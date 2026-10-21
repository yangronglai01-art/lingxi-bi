"""Atomic append-only mock business batches staged as immutable snapshots."""
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid
from .settings import DATA,AS_OF,VERSION,START,END
from . import control,releases
from .ingestion_models import Batch,Refund,Activation,Ticket,PaidOrder,Shipment,Purchase,Receipt

MAX_BYTES=2_000_000

def canonical(value): return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
def digest(value): return hashlib.sha256(canonical(value)).hexdigest()
def identity(source,event_id): return json.dumps([source,event_id],separators=(',',':'))

def store_bytes(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        os.replace(temp,path)
    finally: temp.unlink(missing_ok=True)

def initialize():
    control.initialize()
    with control.db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS ingestion_batches(source TEXT NOT NULL,batch_id TEXT NOT NULL,digest TEXT NOT NULL,archive TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(source,batch_id));
        CREATE TABLE IF NOT EXISTS ingestion_events(source TEXT NOT NULL,event_id TEXT NOT NULL,digest TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(source,event_id));
        CREATE TABLE IF NOT EXISTS ingestion_attempts(id TEXT PRIMARY KEY,source TEXT,batch_id TEXT,parent TEXT,candidate TEXT,state TEXT NOT NULL,archive TEXT NOT NULL,error TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
        ''')

def lineage(folder):
    path=folder/'lineage.json'
    value=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(version=1,events={},batches={})
    if value.get('version')!=1 or not isinstance(value.get('events'),dict) or not isinstance(value.get('batches'),dict): raise ValueError('父快照血缘格式不兼容')
    return value

def sold_device(conn,device_id):
    row=conn.execute('''SELECT o.paid_date,o.customer_id FROM bridge_order_device b
      JOIN fact_order_item i ON b.item_id=i.item_id JOIN fact_order o ON i.order_id=o.order_id
      WHERE b.device_id=?''',[device_id]).fetchone()
    if row is None: raise ValueError('设备未归属已支付订单')
    return row

def check_after_shipment(conn,device_id,day):
    row=conn.execute("SELECT MIN(s.ship_date) FROM fact_shipment s JOIN fact_order_item i ON s.order_id=i.order_id JOIN bridge_order_device b ON i.item_id=b.item_id WHERE b.device_id=?",[device_id]).fetchone()
    if row[0] is not None and day<row[0]: raise ValueError('激活或售后早于已有发货日期')

def unique(conn,table,column,value):
    if conn.execute(f'SELECT 1 FROM {table} WHERE {column}=?',[value]).fetchone(): raise ValueError('业务主键已存在：'+table)


def order_rows(conn,event):
    if not date.fromisoformat(START)<=event.paid_date<=min(date.fromisoformat(END),date.fromisoformat(AS_OF)): raise ValueError('订单支付日期超出当前历史窗口')
    customer=conn.execute('SELECT region_id FROM dim_customer WHERE customer_id=?',[event.customer_id]).fetchone()
    if not customer or not conn.execute('SELECT 1 FROM dim_channel WHERE channel_id=?',[event.channel_id]).fetchone(): raise ValueError('订单引用未知客户或渠道')
    rows=[('fact_order',(event.order_id,event.customer_id,event.channel_id,customer[0],event.paid_date,float(event.paid_amount),'已支付')),
          ('fact_payment',(event.order_id+':payment',event.order_id,event.paid_date,float(event.paid_amount)))]
    for item in event.items:
        sku=conn.execute('SELECT product_id FROM dim_sku WHERE sku_id=?',[item.sku_id]).fetchone()
        cost=conn.execute('SELECT cost FROM fact_product_cost WHERE sku_id=? AND effective_date<=? ORDER BY effective_date DESC LIMIT 1',[item.sku_id,event.paid_date]).fetchone()
        if not sku or not cost: raise ValueError('订单引用未知SKU或缺少有效成本')
        total_cost=(Decimal(str(cost[0]))*item.quantity).quantize(Decimal('.01'))
        rows.append(('fact_order_item',(item.item_id,event.order_id,item.sku_id,item.quantity,float(item.amount),float(total_cost))))
        for device in item.devices:
            rows.extend([('dim_device',(device.device_id,sku[0],device.manufactured_date)),('bridge_order_device',(device.device_id,item.item_id))])
    return rows

def apply_order(conn,event):
    keys={'fact_order':'order_id','fact_payment':'payment_id','fact_order_item':'item_id','dim_device':'device_id','bridge_order_device':'device_id'}
    for table,row in order_rows(conn,event):
        unique(conn,table,keys[table],row[0])
        conn.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in row)+')',list(row))

def verify_order(conn,event):
    keys={'fact_order':'order_id','fact_payment':'payment_id','fact_order_item':'item_id','dim_device':'device_id','bridge_order_device':'device_id'}
    for table,expected in order_rows(conn,event):
        rows=conn.execute('SELECT * FROM '+table+' WHERE '+keys[table]+'=?',[expected[0]]).fetchall()
        if len(rows)!=1 or any(abs(float(a)-b)>.001 if isinstance(b,float) else a!=b for a,b in zip(rows[0],expected)): raise ValueError('订单事件与业务表不一致：'+table)
    if conn.execute('SELECT count(*) FROM fact_order_item WHERE order_id=?',[event.order_id]).fetchone()[0]!=len(event.items): raise ValueError('订单存在额外明细')
    if conn.execute('SELECT count(*) FROM fact_payment WHERE order_id=?',[event.order_id]).fetchone()[0]!=1: raise ValueError('订单支付记录数量不一致')

def apply_event(conn,event):
    cutoff=date.fromisoformat(AS_OF)
    if isinstance(event,(Purchase,Receipt)):
        from .procurement import apply
        apply(conn,event)
    elif isinstance(event,Shipment):
        from .fulfillment import apply
        apply(conn,event)
    elif isinstance(event,PaidOrder):
        apply_order(conn,event)
    elif isinstance(event,Refund):
        unique(conn,'fact_refund','refund_id',event.refund_id)
        row=conn.execute('''SELECT i.amount,o.paid_date,(SELECT COALESCE(SUM(r.amount),0) FROM fact_refund r WHERE r.item_id=i.item_id)
          FROM fact_order_item i JOIN fact_order o ON i.order_id=o.order_id WHERE i.item_id=?''',[event.item_id]).fetchone()
        if row is None: raise ValueError('退款引用未知订单行')
        if not row[1]<=event.refund_date<=cutoff: raise ValueError('退款日期超出支付日和观察截止日')
        amount=Decimal(event.amount)
        refunded=Decimal(str(row[2])).quantize(Decimal('.01'))
        if refunded+amount>Decimal(str(row[0])).quantize(Decimal('.01')): raise ValueError('累计退款超过订单行实付')
        conn.execute('INSERT INTO fact_refund VALUES(?,?,?,?)',[event.refund_id,event.item_id,event.refund_date,float(amount)])
    elif isinstance(event,Activation):
        paid,customer=sold_device(conn,event.device_id)
        check_after_shipment(conn,event.device_id,event.activation_date)
        if not paid<=event.activation_date<=cutoff: raise ValueError('首次激活日期超出有效范围')
        unique(conn,'fact_device_activation','device_id',event.device_id)
        unique(conn,'fact_device_binding','device_id',event.device_id)
        conn.execute('INSERT INTO fact_device_activation VALUES(?,?)',[event.device_id,event.activation_date])
        conn.execute('INSERT INTO fact_device_binding VALUES(?,?,?)',[event.device_id,customer,event.activation_date])
    elif isinstance(event,Ticket):
        paid,_=sold_device(conn,event.device_id)
        check_after_shipment(conn,event.device_id,event.created_date)
        if not paid<=event.created_date<=cutoff: raise ValueError('工单日期超出有效范围')
        unique(conn,'fact_service_ticket','ticket_id',event.ticket_id)
        for table,column,value in [('dim_fault','fault_id',event.fault_id),('dim_employee','employee_id',event.employee_id)]:
            if not conn.execute(f'SELECT 1 FROM {table} WHERE {column}=?',[value]).fetchone(): raise ValueError('工单引用未知主数据')
        unique(conn,'fact_ticket_status','status_id',event.ticket_id+':created')
        unique(conn,'fact_support_contact','contact_id',event.ticket_id+':contact')
        conn.execute('INSERT INTO fact_service_ticket VALUES(?,?,?,?,?,?)',[event.ticket_id,event.device_id,event.fault_id,event.employee_id,event.created_date,'已创建'])
        conn.execute('INSERT INTO fact_ticket_status VALUES(?,?,?,?)',[event.ticket_id+':created',event.ticket_id,event.created_date,'已创建'])
        conn.execute('INSERT INTO fact_support_contact VALUES(?,?,?,?)',[event.ticket_id+':contact',event.ticket_id,event.employee_id,event.created_date])
    else: raise ValueError('不支持的业务事件')

def apply_transaction(conn,events):
    conn.execute('BEGIN')
    try:
        for event in events: apply_event(conn,event)
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK'); raise

def ingest(path):
    from filelock import FileLock
    path=Path(path)
    if path.stat().st_size>MAX_BYTES: raise ValueError('批次文件超过2MB，未读取或接入')
    with path.open('rb') as f: raw=f.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES: raise ValueError('批次文件超过2MB')
    initialize()
    with FileLock(str(DATA/'ingestion.lock'),timeout=30):
        attempt=uuid.uuid4().hex; source=None; batch_id=None; parent=None; candidate=None
        quarantine=DATA/'ingestion/quarantine'/(attempt+'.json')
        quarantine.parent.mkdir(parents=True,exist_ok=True)
        with control.db() as c:
            c.execute("UPDATE ingestion_attempts SET state='interrupted',updated=? WHERE state='processing'",(time.time(),))
            c.execute('INSERT INTO ingestion_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',(attempt,None,None,None,None,'processing',str(quarantine),'',time.time(),time.time()))
        try:
            batch=Batch.model_validate_json(raw)
            source,batch_id=batch.source,batch.batch_id
            body=batch.model_dump(mode='json'); batch_hash=digest(body)
            archive=DATA/'ingestion/archive'/source/batch_id/(hashlib.sha256(raw).hexdigest()+'.json')
            archive.parent.mkdir(parents=True,exist_ok=True)
            if archive.exists():
                if archive.read_bytes()!=raw: raise ValueError('归档校验冲突')
            else: store_bytes(archive,raw)
            with releases.guard(str(DATA)):
                releases.reconcile_locked(DATA)
                parent=json.loads((DATA/'current.json').read_text(encoding='utf-8'))['generation']
            folder=releases.folder_for(parent)
            parent_files=releases.file_seal(folder)
            prior=lineage(folder)
            event_hashes={identity(source,e.event_id):digest(e.model_dump(mode='json')) for e in batch.events}
            with control.db() as c:
                observed=c.execute('SELECT digest FROM ingestion_batches WHERE source=? AND batch_id=?',(source,batch_id)).fetchone()
                if observed and observed[0]!=batch_hash: raise ValueError('相同批次ID内容冲突')
                c.execute('INSERT OR IGNORE INTO ingestion_batches VALUES(?,?,?,?,?)',(source,batch_id,batch_hash,str(archive),time.time()))
                for event in batch.events:
                    seen=c.execute('SELECT digest FROM ingestion_events WHERE source=? AND event_id=?',(source,event.event_id)).fetchone()
                    key=identity(source,event.event_id)
                    if (seen and seen[0]!=event_hashes[key]) or (key in prior['events'] and prior['events'][key]!=event_hashes[key]): raise ValueError('相同事件ID内容冲突')
            pending=[e for e in batch.events if identity(source,e.event_id) not in prior['events']]
            if not pending:
                candidate=parent; state='already_applied'
            else:
                candidate=f'{VERSION}-{uuid.uuid4().hex[:10]}'
                target=DATA/candidate
                with control.db() as c: c.execute('UPDATE ingestion_attempts SET source=?,batch_id=?,parent=?,candidate=?,archive=? WHERE id=?',(source,batch_id,parent,candidate,str(archive),attempt))
                shutil.copytree(folder,target)
                (target/'metadata.json').unlink(missing_ok=True)
                if releases.file_seal(folder)!=parent_files: raise ValueError('复制期间父快照发生变化')
                import duckdb
                with duckdb.connect(str(target/'warehouse.duckdb'),config={'enable_external_access':'false'}) as conn:
                    apply_transaction(conn,pending)
                    catalog=json.loads((target/'catalog.json').read_text(encoding='utf-8'))
                    counts={name:conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in catalog['tables']}
                    for name in ['fact_service_ticket','fact_ticket_status']:
                        values=sorted(r[0] for r in conn.execute(f'SELECT DISTINCT status FROM {name}').fetchall())
                        catalog['tables'][name]['columns']['status'].update(enum=values,enum_scope='complete_synthetic_snapshot')
                    conn.execute('CHECKPOINT')
                from .catalog import METRICS
                if 'metrics' in catalog: catalog['metrics']=METRICS
                catalog['generation']=candidate
                summary=json.loads((target/'summary.json').read_text(encoding='utf-8'))
                summary.update(generation=candidate,orders=counts.get("fact_order",summary.get("orders")),counts=counts,total_rows=sum(counts.values()),verification='模拟上游批次候选，尚未发布')
                prior['events'].update({identity(source,e.event_id):event_hashes[identity(source,e.event_id)] for e in pending})
                prior['batches'][identity(source,batch_id)]=batch_hash
                prior.update(parent_generation=parent,parent_files=parent_files,generation=candidate,last_attempt=attempt)
                releases.write(target/'catalog.json',catalog); releases.write(target/'summary.json',summary)
                releases.write(target/'lineage.json',prior)
                state='staged'
            with control.db() as c:
                c.execute('BEGIN IMMEDIATE')
                for event in batch.events: c.execute('INSERT OR IGNORE INTO ingestion_events VALUES(?,?,?,?)',(source,event.event_id,event_hashes[identity(source,event.event_id)],time.time()))
                c.execute('UPDATE ingestion_attempts SET source=?,batch_id=?,parent=?,candidate=?,state=?,archive=?,updated=? WHERE id=?',(source,batch_id,parent,candidate,state,str(archive),time.time(),attempt))
                c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',(time.time(),'local-operator','ingestion_'+state,json.dumps(dict(attempt=attempt,source=source,batch_id=batch_id,parent=parent,candidate=candidate,applied=len(pending)),ensure_ascii=False)))
            return dict(attempt=attempt,source=source,batch_id=batch_id,parent=parent,candidate=candidate,state=state,applied=len(pending),skipped=len(batch.events)-len(pending))
        except Exception as exc:
            store_bytes(quarantine,raw)
            error='schema validation failed' if type(exc).__name__=='ValidationError' else type(exc).__name__+': '+str(exc)[:300]
            with control.db() as c:
                c.execute("UPDATE ingestion_attempts SET source=?,batch_id=?,parent=?,candidate=?,state='quarantined',archive=?,error=?,updated=? WHERE id=?",(source,batch_id,parent,candidate,str(quarantine),error,time.time(),attempt))
                c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',(time.time(),'local-operator','ingestion_quarantined',json.dumps(dict(attempt=attempt,error=error))))
            raise ValueError('批次已隔离：'+error) from exc


def validate_lineage(conn,history):
    """Prove every claimed event exists in source archives and in the snapshot."""
    records={}; source_files={}
    with control.db() as c:
        for key,expected in history['batches'].items():
            source,batch_id=json.loads(key)
            row=c.execute('SELECT digest,archive FROM ingestion_batches WHERE source=? AND batch_id=?',(source,batch_id)).fetchone()
            if not row or row['digest']!=expected: raise ValueError('批次血缘与接入账本不一致')
            archive=Path(row['archive']).resolve()
            # Archives may be restored under a new instance directory.
            original=Path(row['archive'])
            parts=original.parts
            indices=[i for i in range(len(parts)-1) if parts[i]=='ingestion' and parts[i+1]=='archive']
            if indices:
                archive=(DATA/Path(*parts[indices[-1]:])).resolve()
            if not archive.is_relative_to((DATA/'ingestion/archive').resolve()) or not archive.is_file(): raise ValueError('来源归档缺失或越界')
            source_files[str(archive.relative_to(DATA.resolve()))]=releases.sha(archive)
            batch=Batch.model_validate_json(archive.read_bytes())
            if batch.source!=source or batch.batch_id!=batch_id or digest(batch.model_dump(mode='json'))!=expected: raise ValueError('来源归档内容已改变')
            for event in batch.events:
                event_key=identity(source,event.event_id)
                if event_key in records and digest(records[event_key].model_dump(mode='json'))!=digest(event.model_dump(mode='json')): raise ValueError('归档事件内容冲突')
                records[event_key]=event
        for key,expected in history['events'].items():
            source,event_id=json.loads(key)
            row=c.execute('SELECT digest FROM ingestion_events WHERE source=? AND event_id=?',(source,event_id)).fetchone()
            event=records.get(key)
            if not row or row['digest']!=expected or event is None or digest(event.model_dump(mode='json'))!=expected: raise ValueError('事件血缘与归档账本不一致')
            if isinstance(event,(Purchase,Receipt)):
                from .procurement import verify
                verify(conn,event)
            elif isinstance(event,Shipment):
                from .fulfillment import verify
                verify(conn,event)
            elif isinstance(event,PaidOrder):
                verify_order(conn,event)
            elif isinstance(event,Refund):
                row=conn.execute('SELECT item_id,refund_date,amount FROM fact_refund WHERE refund_id=?',[event.refund_id]).fetchone()
                if not row or row[0]!=event.item_id or row[1]!=event.refund_date or abs(row[2]-float(event.amount))>.001: raise ValueError('退款事件未正确落入业务表')
            elif isinstance(event,Activation):
                row=conn.execute('SELECT activation_date FROM fact_device_activation WHERE device_id=?',[event.device_id]).fetchone()
                if not row or row[0]!=event.activation_date: raise ValueError('激活事件未正确落入业务表')
                _,customer=sold_device(conn,event.device_id)
                row=conn.execute('SELECT customer_id,bound_date FROM fact_device_binding WHERE device_id=?',[event.device_id]).fetchone()
                if not row or row!=(customer,event.activation_date): raise ValueError('激活绑定关系与事件不一致')
            elif isinstance(event,Ticket):
                row=conn.execute('SELECT device_id,fault_id,employee_id,created_date FROM fact_service_ticket WHERE ticket_id=?',[event.ticket_id]).fetchone()
                if not row or row!=(event.device_id,event.fault_id,event.employee_id,event.created_date): raise ValueError('工单事件未正确落入业务表')
                if not conn.execute("SELECT 1 FROM fact_ticket_status WHERE ticket_id=? AND event_date=? AND status='已创建'",[event.ticket_id,event.created_date]).fetchone(): raise ValueError('工单缺少创建状态事件')
                if not conn.execute('SELECT 1 FROM fact_support_contact WHERE ticket_id=? AND employee_id=? AND contact_date=?',[event.ticket_id,event.employee_id,event.created_date]).fetchone(): raise ValueError('工单缺少关联客服接触记录')
    return dict(events=len(history['events']),source_batches=len(history['batches']),source_and_rows_verified=True,source_files=source_files)
