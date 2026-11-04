"""Reproducible synthetic PETKIT-like records. No real company records or PII."""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
import copy,hashlib,random

from src.petkit.catalog import catalog_payload

AS_OF='2026-10-16'
START='2023-01-01'


def money(cents):return format(Decimal(cents)/100,'.2f')


def months(start=START,end=AS_OF):
    d=date.fromisoformat(start).replace(day=1);last=date.fromisoformat(end)
    result=[]
    while d<=last:
        result.append(d);d=date(d.year+(d.month==12),d.month%12+1,1)
    return result


def make(seed,orders=1200,profile='development'):
    if not 10<=orders<=3000 or profile not in {'development','holdout','shift'}:raise ValueError('Bounded synthetic profile required')
    rng=random.Random(seed);catalog=copy.deepcopy(catalog_payload());start=date.fromisoformat(START);cutoff=date.fromisoformat(AS_OF)
    tag={'development':'D','holdout':'H','shift':'S'}[profile]
    catalog.update(version='lingxi-synthetic-v2',as_of=AS_OF,synthetic=True,generation=profile,
        governance={'status':'synthetic_test_only','approval_id':None},source='script-generated; not PETKIT internal data')
    for m in catalog['metrics']:m.update(version=catalog['version'],approval_status='synthetic_test_only',owner=None,approval_id=None)
    rows={n:[] for n in catalog['tables']}
    for t in catalog['tables'].values():
        t['source']='synthetic-benchmark; not enterprise'
        for c in t['columns'].values():
            if c['type']=='DOUBLE':c['type']='DECIMAL(20,2)'
    def add(table,**record):
        if set(record)!=set(catalog['tables'][table]['columns']):raise ValueError('Generator schema mismatch: '+table)
        rows[table].append(record)
    for i in range(1,13):
        pid=f'P{i:03}';price=(19900,39900,129900)[(i-1)//4]+(i%4)*10000
        add('dim_product',product_id=pid,product_name=f'合成{tag}设备{i:02}',category=('饮水机','喂食器','猫砂盆')[(i-1)//4],unit_price=money(price))
        for k in (1,2):
            sid=f'{pid}-S{k}'
            add('dim_sku',sku_id=sid,product_id=pid,sku_name=f'合成{tag}规格{i:02}-{k}')
            add('fact_product_price',sku_id=sid,effective_date=START,price=money(price))
            add('fact_product_cost',sku_id=sid,effective_date=START,cost=money(price*58//100))
    for i,n in enumerate(('合成自营','合成平台','合成经销','合成零售'),1):add('dim_channel',channel_id=f'CH{i}',channel_name=n)
    for i,n in enumerate(('华东','华南','华北','西南'),1):
        add('dim_region',region_id=f'R{i}',region_name='合成'+n)
        add('dim_warehouse',warehouse_id=f'W{i}',warehouse_name=f'合成{tag}{n}仓',region_id=f'R{i}')
    for i in range(1,301):add('dim_customer',customer_id=f'C{i:04}',customer_name=f'合成{tag}客户{i:04}',region_id=f'R{(i-1)%4+1}')
    for i in range(1,7):
        add('dim_supplier',supplier_id=f'S{i}',supplier_name=f'合成{tag}供应商{i}')
        add('dim_employee',employee_id=f'E{i}',employee_name=f'合成{tag}客服{i}')
    for i in range(1,5):add('dim_fault',fault_id=f'F{i}',fault_name=('连接异常','传感器异常','电机异常','使用咨询')[i-1])
    for i in range(1,4):add('dim_firmware',firmware_id=f'V{i}',firmware_name=f'合成版本{i}',release_date=START)
    for n in range((cutoff-start).days+1):
        d=start+timedelta(days=n);add('dim_calendar',date_id=d.isoformat(),month=d.strftime('%Y-%m'),quarter=(d.month-1)//3+1)
    prices={p['product_id']:int(Decimal(p['unit_price'])*100) for p in rows['dim_product']}
    devices=0;ticket_no=0;edge_cases=[]
    boundaries=[(cutoff-timedelta(days=30),0),(cutoff-timedelta(days=30),30),
                (cutoff-timedelta(days=31),31),(cutoff-timedelta(days=29),0),
                (date(2024,2,29),30),(date(2025,12,31),1),
                (date(2026,8,31),30),(date(2026,9,1),31)]
    for index in range(orders):
        oid=f'{tag}O{index+1:07}';customer=rng.randint(1,300);region=f'R{(customer-1)%4+1}'
        paid=boundaries[index][0] if index<len(boundaries) else start+timedelta(days=rng.randint(0,(cutoff-start).days))
        cancelled=index>=len(boundaries) and index%23==0
        pending=index>=len(boundaries) and index%29==0 and not cancelled
        status='已取消' if cancelled else '待支付' if pending else '已支付'
        channel=f'CH{rng.choices((1,2,3,4),weights=(1,18,1,1) if profile=="shift" else (4,5,2,1))[0]}'
        total=0
        if status=='已支付':
            for line in range(1,1+(1 if index<len(boundaries) else rng.choice((1,1,2,3)))):
                pid=f'P{(index%12+1) if index<len(boundaries) else rng.choices(range(1,13),weights=([15]+[1]*11) if profile=="shift" else [1]*12)[0]:03}'
                sid=f'{pid}-S{rng.randint(1,2)}';item=f'{oid}-L{line}';quantity=1 if index<len(boundaries) else rng.choice((1,1,2,3))
                discount=rng.choice((100,95,90,80) if profile=='shift' else (100,100,95,90))
                amount=prices[pid]*quantity*discount//100;total+=amount
                add('fact_order_item',item_id=item,order_id=oid,sku_id=sid,quantity=quantity,amount=money(amount),cost=money(prices[pid]*quantity*58//100))
                refund_probability=.28 if profile=='shift' else .08
                if index in (4,5,6) or rng.random()<refund_probability:
                    parts=(amount//3,amount-amount//3) if index%3==0 else (amount//2,)
                    for part,value in enumerate(parts):
                        when=paid+timedelta(days=part+1)
                        if when<=cutoff:add('fact_refund',refund_id=f'RF-{item}-{part}',item_id=item,refund_date=when.isoformat(),amount=money(value))
                for unit in range(quantity):
                    devices+=1;did=f'{tag}D{devices:08}'
                    add('dim_device',device_id=did,product_id=pid,manufactured_date=(paid-timedelta(days=40)).isoformat())
                    add('bridge_order_device',device_id=did,item_id=item)
                    delay=boundaries[index][1] if index<len(boundaries) else rng.choice((0,1,5,15,30,31,45,None))
                    activated=paid+timedelta(days=delay) if delay is not None else None
                    if activated is not None and activated<=cutoff:
                        add('fact_device_activation',device_id=did,activation_date=activated.isoformat())
                        add('fact_device_binding',device_id=did,customer_id=f'C{customer:04}',bound_date=activated.isoformat())
                        for day in range(min(4,(cutoff-activated).days)+1):
                            when=activated+timedelta(days=day);fw='V3' if day==4 else f'V{devices%3+1}'
                            online=int(rng.random()>(.35 if profile=='shift' else .08));active=int(online and rng.random()<.8)
                            add('fact_device_day',device_id=did,event_date=when.isoformat(),firmware_id=fw,online=online,active=active,event_count=rng.randint(1,9) if active else 0)
                            if active and day==0:add('fact_device_event',event_id=f'EV-{did}',device_id=did,event_date=when.isoformat(),event_type=('饮水','喂食','如厕')[(int(pid[1:])-1)//4])
                        if activated+timedelta(days=4)<=cutoff:add('fact_firmware_upgrade',upgrade_id=f'UP-{did}',device_id=did,firmware_id='V3',upgrade_date=(activated+timedelta(days=4)).isoformat(),success=1)
                    if index<len(boundaries) or rng.random()<(.25 if profile=='shift' else .09):
                        delays=(0,30,31) if index<len(boundaries) else tuple(rng.randint(1,50) for _ in range(rng.choice((1,1,2))))
                        for lag in delays:
                            when=paid+timedelta(days=lag)
                            if when>cutoff:continue
                            ticket_no+=1;tid=f'{tag}T{ticket_no:07}';employee=f'E{rng.randint(1,6)}';resolved=when+timedelta(days=2)
                            add('fact_service_ticket',ticket_id=tid,device_id=did,fault_id=f'F{rng.randint(1,4)}',employee_id=employee,created_date=when.isoformat(),status='已解决' if resolved<=cutoff else '已创建')
                            add('fact_ticket_status',status_id=f'TS-{tid}',ticket_id=tid,event_date=when.isoformat(),status='已创建')
                            add('fact_support_contact',contact_id=f'CO-{tid}',ticket_id=tid,employee_id=employee,contact_date=when.isoformat())
                            if resolved<=cutoff:
                                add('fact_ticket_status',status_id=f'TS-{tid}-2',ticket_id=tid,event_date=resolved.isoformat(),status='已解决')
                                add('fact_repair',repair_id=f'RE-{tid}',ticket_id=tid,repair_date=resolved.isoformat(),cost=money(rng.randint(2000,12000)))
                                add('fact_feedback',feedback_id=f'FB-{tid}',ticket_id=tid,score=rng.randint(1,5),feedback_date=resolved.isoformat())
                                if ticket_no%17==0:
                                    replacement=f'{tag}X{ticket_no:08}';add('dim_device',device_id=replacement,product_id=pid,manufactured_date=(paid-timedelta(days=20)).isoformat())
                                    add('fact_replacement',replacement_id=f'RP-{tid}',ticket_id=tid,old_device_id=did,new_device_id=replacement,replace_date=resolved.isoformat())
                    if index<len(boundaries):edge_cases.append(dict(kind='cohort_boundary',order=oid,device=did,paid_date=paid.isoformat(),activation_delay=delay))
        add('fact_order',order_id=oid,customer_id=f'C{customer:04}',channel_id=channel,region_id=region,paid_date=paid.isoformat() if status=='已支付' else None,paid_amount=money(total),status=status)
        if status=='已支付':
            # Multiple payment records must not multiply order-level money.
            parts=(total//2,total-total//2) if index%7==0 else (total,)
            for n,value in enumerate(parts):add('fact_payment',payment_id=f'PAY-{oid}-{n}',order_id=oid,paid_date=paid.isoformat(),amount=money(value))
            if paid+timedelta(days=1)<=cutoff:add('fact_shipment',shipment_id=f'SH-{oid}',order_id=oid,warehouse_id='W'+region[1:],ship_date=(paid+timedelta(days=1)).isoformat())
    # Movement totals reconcile to snapshots, including asynchronous updates.
    for sku_index,sku in enumerate(rows['dim_sku']):
        for w in range(1,5):
            sid=sku['sku_id'];wid=f'W{w}';balance=100+rng.randint(0,200)
            add('fact_stock_movement',movement_id=f'MV-{sid}-{w}-open',sku_id=sid,warehouse_id=wid,movement_date=START,quantity=balance)
            for n,when in enumerate(months()):
                # Some SKU/warehouse pairs deliberately miss a monthly snapshot.
                if (n+sku_index+w)%11==0:continue
                delta=rng.randint(-min(balance,15),20);balance+=delta
                add('fact_stock_movement',movement_id=f'MV-{sid}-{w}-{n}',sku_id=sid,warehouse_id=wid,movement_date=when.isoformat(),quantity=delta)
                add('fact_inventory_day',stock_date=when.isoformat(),sku_id=sid,warehouse_id=wid,quantity=balance)
    for i in range(1,241):
        when=start+timedelta(days=rng.randint(0,(cutoff-start).days));purchase=f'{tag}PO{i:04}';item=f'{tag}PI{i:04}';quantity=rng.randint(10,100)
        add('fact_purchase_order',purchase_id=purchase,supplier_id=f'S{i%6+1}',purchase_date=when.isoformat())
        add('fact_purchase_item',purchase_item_id=item,purchase_id=purchase,sku_id=rows['dim_sku'][i%24]['sku_id'],quantity=quantity,amount=money(quantity*10000))
        for n,value in enumerate((quantity//2,quantity-quantity//2)):
            receipt=when+timedelta(days=3+n*14)
            if receipt<=cutoff:add('fact_receipt',receipt_id=f'{tag}RC{i:04}-{n}',purchase_item_id=item,warehouse_id=f'W{i%4+1}',receipt_date=receipt.isoformat(),quantity=value)
    for i,product in enumerate(rows['dim_product']):
        campaign=f'{tag}CA{i:03}';when=date(2026,1+i%9,1)
        add('fact_campaign',campaign_id=campaign,product_id=product['product_id'],start_date=when.isoformat(),end_date=(when+timedelta(days=6)).isoformat(),budget=money(100000+i*10000))
        for n in range(7):add('fact_campaign_day',campaign_id=campaign,event_date=(when+timedelta(days=n)).isoformat(),impressions=2000+i*100+n,clicks=100+n,spend=money((100+n)*40))
    for i in range(1,121):
        when=date(2026,1,1)+timedelta(days=i*2)
        add('fact_subscription',subscription_id=f'{tag}SU{i:04}',customer_id=f'C{i:04}',start_date=when.isoformat(),end_date=min(when+timedelta(days=30),cutoff).isoformat(),amount='19.90')
        add('fact_app_session',session_id=f'{tag}SE{i:04}',customer_id=f'C{i:04}',event_date=when.isoformat(),duration_seconds=rng.randint(10,600))
    # Retrieval distractors are explicitly independent synthetic theme series.
    # They are never substituted for fact-level sales/inventory/cohort metrics.
    for table,meta in catalog['tables'].items():
        if meta['core']:continue
        base=int(hashlib.sha256((table+str(seed)).encode()).hexdigest()[:8],16)%91
        for n,when in enumerate(months('2025-10-01','2026-09-30')):
            for product in rows['dim_product']:
                count=base+n+int(product['product_id'][1:])+1
                add(table,period_date=when.isoformat(),product_id=product['product_id'],record_count=count,amount=money(count*(100+base)),source_version=catalog['version'])
    keys={n:[next(iter(t['columns']))] for n,t in catalog['tables'].items()}
    keys.update(fact_device_day=['device_id','event_date'],fact_inventory_day=['sku_id','warehouse_id','stock_date'],
        fact_product_price=['sku_id','effective_date'],fact_product_cost=['sku_id','effective_date'],fact_campaign_day=['campaign_id','event_date'])
    for n,t in catalog['tables'].items():
        if not t['core']:keys[n]=['period_date','product_id']
        for column in ('status','category','channel_name','region_name','fault_name','firmware_name','event_type'):
            if column in t['columns']:t['columns'][column]['enum']=sorted({r[column] for r in rows[n] if r[column] is not None})
    watermark=dict(source_kind='synthetic-benchmark',source_version=seed,event_time=AS_OF+'T23:59:59+08:00',ingested_at='2026-10-23T00:00:00+08:00',batch_id='synthetic_'+profile)
    return dict(catalog=catalog,rows=rows,keys=keys,watermark=watermark,seed=seed,profile=profile,edge_cases=edge_cases)


def invariants(data):
    """Business controls independent of both SQL reference generation and model."""
    r=data['rows'];cutoff=AS_OF;errors=[]
    by_order=defaultdict(Decimal);payments=defaultdict(Decimal);refunds=defaultdict(Decimal)
    items={v['item_id']:v for v in r['fact_order_item']};orders={v['order_id']:v for v in r['fact_order']}
    for v in items.values():by_order[v['order_id']]+=Decimal(v['amount'])
    for v in r['fact_payment']:payments[v['order_id']]+=Decimal(v['amount'])
    for v in r['fact_refund']:
        refunds[v['item_id']]+=Decimal(v['amount'])
        if v['refund_date']<orders[items[v['item_id']]['order_id']]['paid_date']:errors.append('refund precedes payment')
    for oid,o in orders.items():
        if Decimal(o['paid_amount'])!=by_order[oid] or by_order[oid]!=payments[oid]:errors.append('header/item/payment mismatch')
        if o['status']!='已支付' and (by_order[oid]!=0 or o['paid_date'] is not None):errors.append('unpaid order appears paid')
    for item,amount in refunds.items():
        if amount>Decimal(items[item]['amount']):errors.append('refund exceeds paid line')
    bridged=defaultdict(int)
    for b in r['bridge_order_device']:bridged[b['item_id']]+=1
    if any(bridged[i]!=v['quantity'] for i,v in items.items()):errors.append('sold device quantity mismatch')
    movements=defaultdict(list)
    for v in r['fact_stock_movement']:movements[v['sku_id'],v['warehouse_id']].append(v)
    for v in r['fact_inventory_day']:
        if v['quantity']!=sum(m['quantity'] for m in movements[v['sku_id'],v['warehouse_id']] if m['movement_date']<=v['stock_date']):errors.append('stock movement reconciliation')
    received=defaultdict(int)
    for v in r['fact_receipt']:received[v['purchase_item_id']]+=v['quantity']
    if any(received[v['purchase_item_id']]>v['quantity'] for v in r['fact_purchase_item']):errors.append('over-receipt')
    for n,records in r.items():
        for v in records:
            for c,meta in data['catalog']['tables'][n]['columns'].items():
                if meta['type']=='DATE' and v[c] is not None and c not in {'end_date'} and v[c]>cutoff:errors.append('future effective record')
    return dict(passed=not errors,errors=sorted(set(errors)),controls=['header_item_payment_decimal','refund_limit_and_time','sold_device_cardinality','stock_movement_snapshots','receipt_ceiling','effective_time_cutoff'],
        row_count=sum(map(len,r.values())),table_count=len(r),core_tables=sum(t['core'] for t in data['catalog']['tables'].values()))
