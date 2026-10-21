"""Raw-row regional oracle scoping and strict result grading, independent of SQL."""
import math

def regional_rows(raw,regions):
    if not regions:return dict(raw)
    result=dict(raw);regions=set(regions)
    result['fact_order']=[r for r in raw['fact_order'] if r['region_id'] in regions]
    orders={r['order_id'] for r in result['fact_order']}
    result['fact_order_item']=[r for r in raw['fact_order_item'] if r['order_id'] in orders]
    items={r['item_id'] for r in result['fact_order_item']}
    for table in ['fact_refund','bridge_order_device']:
        result[table]=[r for r in raw[table] if r['item_id'] in items]
    devices={r['device_id'] for r in result['bridge_order_device']}
    for table in ['fact_device_day','fact_service_ticket']:
        result[table]=[r for r in raw[table] if r['device_id'] in devices]
    warehouses={r['warehouse_id'] for r in raw['dim_warehouse'] if r['region_id'] in regions}
    for table in ['fact_inventory_day','fact_receipt']:
        result[table]=[r for r in raw[table] if r['warehouse_id'] in warehouses]
    return result

def grade(case,answer,reference,raw):
    if case['expected_status']!='success':
        passed=answer.status==case['expected_status'] and answer.result is None
        return dict(passed=passed,reason='expected terminal policy/ambiguity state' if passed else 'unexpected state or leaked result')
    if answer.status!='success' or answer.result is None or answer.mock_used or answer.truncated:
        return dict(passed=False,reason='not a complete real-model success')
    if (answer.context.get('metric') or {}).get('id')!=case['metric_id']:
        return dict(passed=False,reason='registered metric gate was not bound')
    aliases={}
    for table,key,label in [('dim_product','product_id','product_name'),('dim_channel','channel_id','channel_name'),
                            ('dim_firmware','firmware_id','firmware_name'),('dim_warehouse','warehouse_id','warehouse_name'),
                            ('dim_fault','fault_id','fault_name')]:
        aliases.update({r[key]:r[label] for r in raw[table]})
    wanted=dict(reference)
    if case.get('top_n'):
        wanted=dict(sorted(wanted.items(),key=lambda item:(-item[1][0],item[0]))[:case['top_n']])
    # Known optional numerator is accepted only after independent raw-row arithmetic.
    if case['metric_id']=='offline_rate' and 'offline_device_days' in answer.result.columns:
        device_products={r['device_id']:r['product_id'] for r in raw['dim_device']}
        products={r['product_id']:r['product_name'] for r in raw['dim_product']}
        firmware={r['firmware_id']:r['firmware_name'] for r in raw['dim_firmware']}
        offline={key:0 for key in wanted}
        for row in raw['fact_device_day']:
            if (not case['month'] or row['event_date'].strftime('%Y-%m')==case['month']) and row['online']==0:
                key=firmware[row['firmware_id']] if case['group']=='firmware' else products[device_products[row['device_id']]]
                offline[key]+=1
        wanted={key:tuple(values)+(offline[key],) for key,values in wanted.items()}
    found={};ordered=[]
    for row in answer.result.rows:
        labels=list({aliases.get(value,value) for value in row if isinstance(value,str)})
        numbers=[float(value) for value in row if isinstance(value,(float,int)) and not isinstance(value,bool)]
        if len(labels)!=1 or labels[0] in found or not all(math.isfinite(value) for value in numbers):
            return dict(passed=False,reason='missing/duplicate group or nonfinite numbers')
        found[labels[0]]=numbers;ordered.append(labels[0])
    if found.keys()!=wanted.keys():return dict(passed=False,reason='group set mismatch')
    if not all(len(found[key])==len(values) and all(abs(a-b)<=.011 for a,b in
        zip(sorted(found[key]),sorted(float(v) for v in values))) for key,values in wanted.items()):
        return dict(passed=False,reason='numeric values or number of numeric columns mismatch')
    if case.get('descending') and any(wanted[ordered[i]][0]<wanted[ordered[i+1]][0] for i in range(len(ordered)-1)):
        return dict(passed=False,reason='descending order not followed')
    if case['metric_id'] in {'activation30','service30'}:
        columns=answer.result.columns
        fields=['devices','matched_devices','rate_percent']
        if not all(columns.count(field)==1 for field in fields):return dict(passed=False,reason='cohort named column contract mismatch')
        for row in answer.result.rows:
            label=next(aliases.get(value,value) for value in row if isinstance(value,str))
            if any(abs(row[columns.index(field)]-reference[label][i])>.011 for i,field in enumerate(fields)):
                return dict(passed=False,reason='cohort numerator/denominator/percent labels mismatched')
    return dict(passed=True,reason='exact group set and numeric multiset match raw-row Python oracle')
