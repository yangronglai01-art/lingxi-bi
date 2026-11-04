"""Independent exact arithmetic for the existing 37 registered policy questions."""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
from .oracle import wire,percent


def answer(data,spec):
    r=data['rows'];metric=spec['metric_id'];group=spec['group'];month=spec['month']
    def in_month(value):return value is not None and (not month or value[:7]==month)
    products={x['product_id']:x['product_name'] for x in r['dim_product']}
    skus={x['sku_id']:x['product_id'] for x in r['dim_sku']}
    devices={x['device_id']:x['product_id'] for x in r['dim_device']}
    orders={x['order_id']:x for x in r['fact_order']};items={x['item_id']:x for x in r['fact_order_item']}
    if metric=='net_sales':
        channels={x['channel_id']:x['channel_name'] for x in r['dim_channel']};refunds=defaultdict(Decimal);totals=defaultdict(Decimal)
        for f in r['fact_refund']:refunds[f['item_id']]+=Decimal(f['amount'])
        for i in items.values():
            o=orders[i['order_id']]
            if not in_month(o['paid_date']):continue
            key=products[skus[i['sku_id']]] if group=='model' else channels[o['channel_id']] if group=='channel' else o['paid_date'][:7]
            totals[key]+=Decimal(i['amount'])-refunds[i['item_id']]
        return ['dimension','net_sales'],[[k,wire(v)] for k,v in totals.items()]
    if metric in {'activation30','service30'}:
        table,field=('fact_device_activation','activation_date') if metric=='activation30' else ('fact_service_ticket','created_date')
        events=defaultdict(list)
        for e in r[table]:events[e['device_id']].append(date.fromisoformat(e[field]))
        den=defaultdict(set);hits=defaultdict(set);cutoff=date.fromisoformat(data['catalog']['as_of'])-timedelta(days=30)
        for b in r['bridge_order_device']:
            paid=orders[items[b['item_id']]['order_id']]['paid_date']
            if not in_month(paid):continue
            paid=date.fromisoformat(paid)
            if paid>cutoff:continue
            key=products[devices[b['device_id']]];den[key].add(b['device_id'])
            if any(0<=(e-paid).days<=30 for e in events[b['device_id']]):hits[key].add(b['device_id'])
        return ['product_name','devices','matched_devices','rate_percent'],[[k,len(v),len(hits[k]),wire(percent(len(hits[k]),len(v)))] for k,v in den.items()]
    if metric=='offline_rate':
        firmware={x['firmware_id']:x['firmware_name'] for x in r['dim_firmware']};totals=defaultdict(lambda:[0,0])
        for x in r['fact_device_day']:
            if not in_month(x['event_date']):continue
            key=firmware[x['firmware_id']] if group=='firmware' else products[devices[x['device_id']]]
            totals[key][0]+=1;totals[key][1]+=int(x['online']==0)
        return ['dimension','device_days','offline_percent'],[[k,n,wire(percent(off,n))] for k,(n,off) in totals.items()]
    if metric=='tickets':
        faults={x['fault_id']:x['fault_name'] for x in r['dim_fault']};totals=defaultdict(int)
        for x in r['fact_service_ticket']:
            if in_month(x['created_date']):totals[faults[x['fault_id']] if group=='fault' else products[devices[x['device_id']]]]+=1
        return ['dimension','tickets'],[[k,v] for k,v in totals.items()]
    if metric=='received_units':
        names={x['warehouse_id']:x['warehouse_name'] for x in r['dim_warehouse']};totals=defaultdict(int)
        for x in r['fact_receipt']:
            if in_month(x['receipt_date']):totals[names[x['warehouse_id']]]+=x['quantity']
        return ['warehouse_name','received_units'],[[k,v] for k,v in totals.items()]
    if metric=='inventory':
        latest={};totals=defaultdict(int)
        for x in r['fact_inventory_day']:
            if not in_month(x['stock_date']):continue
            key=x['sku_id'],x['warehouse_id']
            if key not in latest or x['stock_date']>latest[key]['stock_date']:latest[key]=x
        for x in latest.values():totals[products[skus[x['sku_id']]]]+=x['quantity']
        return ['product_name','stock_quantity'],[[k,v] for k,v in totals.items()]
    raise ValueError('Unregistered metric')
