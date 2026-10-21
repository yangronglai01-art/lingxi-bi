"""Independent raw-record arithmetic for release gates; never calls SQL templates."""
from collections import defaultdict
from datetime import date,timedelta
from .settings import AS_OF

def expected(case,raw):
    """Independent arithmetic over raw records, no joins/aggregations via template SQL."""
    products={r['product_id']:r['product_name'] for r in raw['dim_product']}
    devices={r['device_id']:r['product_id'] for r in raw['dim_device']}
    items={r['item_id']:r for r in raw['fact_order_item']}
    orders={r['order_id']:r for r in raw['fact_order']}
    metric,group,month=case['metric_id'],case['group'],case['month']
    def included(dt): return not month or dt.strftime('%Y-%m')==month
    output={}
    if metric=='net_sales':
        sku={r['sku_id']:r['product_id'] for r in raw['dim_sku']}
        channels={r['channel_id']:r['channel_name'] for r in raw['dim_channel']}
        refunds=defaultdict(float)
        for r in raw['fact_refund']: refunds[r['item_id']]+=r['amount']
        totals=defaultdict(float)
        for r in raw['fact_order_item']:
            o=orders[r['order_id']]
            if included(o['paid_date']):
                key=(products[sku[r['sku_id']]] if group=='model' else channels[o['channel_id']] if group=='channel' else o['paid_date'].strftime('%Y-%m'))
                totals[key]+=r['amount']-refunds[r['item_id']]
        output={key:(round(value,2),) for key,value in totals.items()}
    elif metric in {'activation30','service30'}:
        events=defaultdict(list)
        source,field=('fact_device_activation','activation_date') if metric=='activation30' else ('fact_service_ticket','created_date')
        for r in raw[source]: events[r['device_id']].append(r[field])
        cohorts=defaultdict(set); hits=defaultdict(set)
        cutoff=date.fromisoformat(AS_OF)-timedelta(days=30)
        for r in raw['bridge_order_device']:
            paid=orders[items[r['item_id']]['order_id']]['paid_date']
            if paid<=cutoff and included(paid):
                key=products[devices[r['device_id']]]
                cohorts[key].add(r['device_id'])
                if any(0<=(dt-paid).days<=30 for dt in events[r['device_id']]): hits[key].add(r['device_id'])
        output={key:(len(ids),len(hits[key]),round(len(hits[key])*100/len(ids),2)) for key,ids in cohorts.items()}
    elif metric=='offline_rate':
        names={r['firmware_id']:r['firmware_name'] for r in raw['dim_firmware']}
        totals=defaultdict(lambda:[0,0])
        for r in raw['fact_device_day']:
            if included(r['event_date']):
                key=names[r['firmware_id']] if group=='firmware' else products[devices[r['device_id']]]
                totals[key][0]+=1; totals[key][1]+=int(not r['online'])
        output={key:(value[0],round(value[1]*100/value[0],2)) for key,value in totals.items()}
    elif metric=='tickets':
        faults={r['fault_id']:r['fault_name'] for r in raw['dim_fault']}
        totals=defaultdict(int)
        for r in raw['fact_service_ticket']:
            if included(r['created_date']): totals[faults[r['fault_id']] if group=='fault' else products[devices[r['device_id']]]]+=1
        output={key:(value,) for key,value in totals.items()}
    elif metric=='received_units':
        warehouses={r['warehouse_id']:r['warehouse_name'] for r in raw['dim_warehouse']}
        totals=defaultdict(int)
        for r in raw['fact_receipt']:
            if included(r['receipt_date']): totals[warehouses[r['warehouse_id']]]+=r['quantity']
        output={key:(value,) for key,value in totals.items()}
    elif metric=='inventory':
        sku={r['sku_id']:r['product_id'] for r in raw['dim_sku']}
        latest={}
        for r in raw['fact_inventory_day']:
            if included(r['stock_date']):
                key=(r['sku_id'],r['warehouse_id'])
                if key not in latest or r['stock_date']>latest[key]['stock_date']: latest[key]=r
        totals=defaultdict(int)
        for r in latest.values(): totals[products[sku[r['sku_id']]]]+=r['quantity']
        output={key:(value,) for key,value in totals.items()}
    return output


