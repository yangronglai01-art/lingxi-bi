"""Small paid-model development pilot, not a blind benchmark or load test."""
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date,timedelta
from pathlib import Path

os.environ['PROJECT_PROFILE']='petkit'
os.environ['PETKIT_MODE']='llm'
os.environ.pop('PETKIT_EMBEDDING_MODEL',None)
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
import duckdb
from src import config
from src.engine.engine import QueryEngine
from src.petkit.settings import artifacts,AS_OF

CASES=[
 dict(id='L01',question='请按产品型号汇总净销售额，退款扣回原支付月份，列出各型号金额。',metric_id='net_sales',group='model',month=None),
 dict(id='L02',question='只看2026年9月，分别列出不同销售渠道的净销售额。',metric_id='net_sales',group='channel',month='2026-09'),
 dict(id='L03',question='2026年8月按型号计算售出30天激活率，只统计观察满30天的设备，同时列出分子、分母和百分数。',metric_id='activation30',group='model',month='2026-08'),
 dict(id='L04',question='2026年9月按型号统计售出30天售后设备率，同一设备多次报修只算一次，同时列出分子、分母和百分数。',metric_id='service30',group='model',month='2026-09'),
 dict(id='L05',question='请比较2026年8月不同固件版本的设备离线率，同时返回设备日记录数和百分数。',metric_id='offline_rate',group='firmware',month='2026-08'),
 dict(id='L06',question='请按型号展示最近一个库存快照日的结存数量，不要累加多个日期。',metric_id='inventory',group='model',month=None),
]


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
    elif metric=='inventory':
        sku={r['sku_id']:r['product_id'] for r in raw['dim_sku']}
        latest=max(r['stock_date'] for r in raw['fact_inventory_day'] if included(r['stock_date']))
        totals=defaultdict(int)
        for r in raw['fact_inventory_day']:
            if r['stock_date']==latest: totals[products[sku[r['sku_id']]]]+=r['quantity']
        output={key:(value,) for key,value in totals.items()}
    return output


def assess(answer,reference,raw):
    if answer.status!='success' or answer.result is None:
        return dict(passed=False,reason='没有成功查询结果')
    aliases={}
    for table,key,label in [('dim_product','product_id','product_name'),('dim_channel','channel_id','channel_name'),('dim_firmware','firmware_id','firmware_name')]:
        aliases.update({r[key]:r[label] for r in raw[table]})
    found={}
    for row in answer.result.rows:
        keys=list({aliases.get(v,v) for v in row if isinstance(v,str) and aliases.get(v,v) in reference})
        if len(keys)!=1 or keys[0] in found:
            return dict(passed=False,reason='分组键缺失或重复，需人工复核')
        numbers=[float(v) for v in row if isinstance(v,(float,int)) and not isinstance(v,bool)]
        found[keys[0]]=numbers
    if found.keys()!=reference.keys():
        return dict(passed=False,reason='分组集合不同')
    for key,values in found.items():
        expected_values=sorted(float(v) for v in reference[key])
        actual_values=sorted(values)
        remaining=list(actual_values)
        missing=[]
        for value in expected_values:
            match=next((i for i,a in enumerate(remaining) if abs(a-value)<=.011),None)
            if match is None: missing.append(value)
            else: remaining.pop(match)
        if missing:
            return dict(passed=False,reason='数值或返回字段不符，需要人工核查',group=key,actual=actual_values,expected=expected_values)
    return dict(passed=True,reason='分组和数值与独立Python计算一致')


def run():
    folder,manifest=artifacts()
    cat=json.loads((folder/'catalog.json').read_text(encoding='utf-8'))
    conn=duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True)
    try:
        raw={}
        for name,table in cat['tables'].items():
            if table['core']:
                cursor=conn.execute(f'SELECT * FROM "{name}"')
                cols=[c[0] for c in cursor.description]
                raw[name]=[dict(zip(cols,row)) for row in cursor.fetchall()]
    finally: conn.close()
    out=root/('reports/petkit500_llm_pilot_followup.json' if os.getenv('PETKIT_PILOT_IDS') else 'reports/petkit500_llm_pilot.json')
    report=dict(kind='development_pilot',model=config.MODEL_NAME,generation=manifest['generation'],
        synthetic=True,real_model=True,cases=[],not_blind_benchmark=True)
    for case in CASES:
        if os.getenv('PETKIT_PILOT_IDS') and case['id'] not in os.environ['PETKIT_PILOT_IDS'].split(','): continue
        begin=time.monotonic()
        answer=QueryEngine().ask(case['question'])
        record=dict(id=case['id'],question=case['question'],status=answer.status,
            error=answer.error.replace(config.API_KEY,'[REDACTED]') if config.API_KEY else answer.error,
            sql=answer.sql,plan=answer.plan,mock_used=answer.mock_used,
            elapsed_seconds=round(time.monotonic()-begin,3),
            rows=answer.result.rows if answer.result else None,
            columns=answer.result.columns if answer.result else None,
            context_tables=answer.context.get('table_names',[]),
            assessment=assess(answer,expected(case,raw),raw))
        report['cases'].append(record)
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:record[k] for k in ['id','status','error','elapsed_seconds','assessment']},ensure_ascii=False),flush=True)
        # Avoid spending further calls when provider credentials/model/transport are unusable.
        if answer.status=='error' and any(token in answer.error for token in ['401','402','403','404','429','请求大模型失败','未配置API_KEY']):
            report['stopped']='模型服务不可用；其余问题未继续调用'
            break
    report['passed']=sum(c['assessment']['passed'] for c in report['cases'])
    report['attempted']=len(report['cases'])
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    run()
