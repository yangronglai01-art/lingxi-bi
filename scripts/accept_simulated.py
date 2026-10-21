"""Actual local HTTP acceptance of the persistent single-machine instance."""
from collections import defaultdict
from datetime import date,timedelta
import json
import argparse
from pathlib import Path
import socket
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running
from src.petkit import control
from src.petkit.settings import DATA,artifacts,AS_OF

def free():
    with socket.socket() as s: s.bind(('127.0.0.1',0)); return s.getsockname()[1]

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--llm',action='store_true'); args=parser.parse_args()
    mode='llm' if args.llm else 'demo'
    keys=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))
    a,b=free(),free()
    while a==b: b=free()
    report=dict(kind='persistent simulated instance acceptance',cases=[],real_llm_called=args.llm)
    def call(base,subject,path,body=None):
        req=Request(base+path,headers={'Authorization':'Bearer '+keys[subject],'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
        with urlopen(req,timeout=10) as response: return json.load(response)
    def wait(base,subject,job):
        deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            value=call(base,subject,'/jobs/'+job)
            if value['state'] not in {'queued','running'}: return value
            time.sleep(.15)
        raise TimeoutError('后台任务未按期结束')
    with running(mode,a,b) as services:
        base=services['api_url']
        for subject,question in [('global_analyst','各型号净销售额'),('regional_sales','各型号净销售额'),('regional_service','各型号售出30天激活率'),('restricted_viewer','各型号净销售额')]:
            identity=call(base,subject,'/me')
            job=call(base,subject,'/jobs',{'question':question})['id']
            value=wait(base,subject,job)
            assert value['state']=='completed',value.get('error')
            answer=value['result']; expected_status='denied' if subject=='restricted_viewer' else 'success'
            assert answer['status']==expected_status,(subject,answer['error'])
            report['cases'].append(dict(subject=subject,regions=identity['regions'],job=job,status=answer['status']))
            if subject=='regional_sales': sales=answer; sales_scope=identity['regions']; sales_job=job
            if subject=='global_analyst': global_answer=answer
            if subject=='regional_service': activation=answer; service_scope=identity['regions']
        try: call(base,'regional_service','/jobs/'+sales_job)
        except HTTPError as exc: assert exc.code==404
        else: raise AssertionError('跨用户任务泄露')
        ops=call(base,'global_analyst','/ops/summary')
        assert ops['audit']
        processes=services['processes']
    assert all(p.poll() is not None for p in processes)
    import duckdb
    folder,_=artifacts()
    with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
        orders={r[0]:r for r in c.execute('SELECT order_id,region_id,paid_date FROM fact_order').fetchall()}
        items={r[0]:r for r in c.execute('SELECT item_id,order_id,amount FROM fact_order_item').fetchall()}
        refunds=c.execute('SELECT item_id,amount FROM fact_refund').fetchall()
        bridge=c.execute('SELECT device_id,item_id FROM bridge_order_device').fetchall()
        devices=dict(c.execute('SELECT device_id,product_id FROM dim_device').fetchall())
        products=dict(c.execute('SELECT product_id,product_name FROM dim_product').fetchall())
        activations=dict(c.execute('SELECT device_id,activation_date FROM fact_device_activation').fetchall())
    expected_sales=sum(r[2] for r in items.values() if orders[r[1]][1] in sales_scope)-sum(amount for item,amount in refunds if orders[items[item][1]][1] in sales_scope)
    sales_column=next(i for i,c in enumerate(sales['result']['columns']) if c.lower() in {'net_sales','net_sales_amount','净销售额'})
    actual_sales=sum(r[sales_column] for r in sales['result']['rows'])
    assert abs(expected_sales-actual_sales)<.02
    expected_global=sum(r[2] for r in items.values())-sum(amount for _,amount in refunds)
    assert abs(sum(r[next(i for i,c in enumerate(global_answer['result']['columns']) if c.lower() in {'net_sales','net_sales_amount','净销售额'})] for r in global_answer['result']['rows'])-expected_global)<.02
    cohort=defaultdict(lambda:[0,0]); cutoff=date.fromisoformat(AS_OF)-timedelta(days=30)
    for device,item in bridge:
        _,region,paid=orders[items[item][1]]
        if region not in service_scope or paid>cutoff: continue
        value=cohort[products[devices[device]]]; value[0]+=1
        event=activations.get(device)
        if event and paid<=event<=paid+timedelta(days=30): value[1]+=1
    actual={}
    aliases={name:name for name in products.values()}; aliases.update(products)
    for row in activation['result']['rows']:
        model=next((aliases[v] for v in row if isinstance(v,str) and v in aliases),None)
        if model is None or model in actual: raise AssertionError('激活率分组缺失或重复')
        actual[model]=[float(v) for v in row if isinstance(v,(int,float)) and not isinstance(v,bool)]
    assert set(actual)==set(cohort)
    for model,(den,num) in cohort.items():
        remaining=list(actual[model])
        for expected in [den,num,round(100*num/den,2)]:
            index=next((i for i,v in enumerate(remaining) if abs(v-expected)<.011),None)
            assert index is not None,(model,actual[model],[den,num,round(100*num/den,2)])
            remaining.pop(index)
    report.update(regional_sales_python_oracle=True,global_sales_python_oracle=True,regional_activation_python_oracle=True,cross_user_denied=True)
    principal=control.principal_for('regional_sales')
    queued=control.submit(principal,'各型号净销售额')
    cancelled=control.submit(principal,'各型号净销售额')
    control.cancel(principal,cancelled)
    with running(mode,a,b) as services:
        restored=wait(services['api_url'],'regional_sales',queued)
        assert restored['state']=='completed' and restored['result']['status']=='success'
        assert call(services['api_url'],'regional_sales','/jobs/'+cancelled)['state']=='cancelled'
        assert call(services['api_url'],'global_analyst','/ready')['ready']
        processes=services['processes']
    assert all(p.poll() is not None for p in processes)
    report.update(queued_job_survives_restart=True,cancel_survives_restart=True,children_stopped=True)
    (ROOT/('reports/petkit500_simulated_llm_acceptance.json' if args.llm else 'reports/petkit500_simulated_acceptance.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False),flush=True)

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
