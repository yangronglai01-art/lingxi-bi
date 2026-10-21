"""One real-model regional job on the newly published data, graded per product."""
import json
from pathlib import Path
import socket
import sys
import time
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running
from src.petkit.settings import DATA,artifacts
from src.petkit.reference import expected

def free():
    with socket.socket() as s: s.bind(('127.0.0.1',0)); return s.getsockname()[1]

def main():
    key=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))['regional_sales']
    a,b=free(),free()
    while a==b: b=free()
    with running('llm',a,b) as services:
        def call(path,body=None):
            request=Request(services['api_url']+path,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
            with urlopen(request,timeout=10) as response: return json.load(response)
        identity=call('/me')
        question='请按产品型号列出各型号净销售额，按订单行实付减去对应退款。'
        task=call('/jobs',{'question':question})['id']; deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            job=call('/jobs/'+task)
            if job['state'] not in {'queued','running'}: break
            time.sleep(.15)
        answer=job.get('result'); children=services['processes']
    assert all(p.poll() is not None for p in children)
    folder,manifest=artifacts()
    import duckdb
    cat=json.loads((folder/'catalog.json').read_text(encoding='utf-8')); raw={}
    with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
        for name,t in cat['tables'].items():
            if t['core']:
                cur=c.execute(f'SELECT * FROM "{name}"'); fields=[d[0] for d in cur.description]
                raw[name]=[dict(zip(fields,row)) for row in cur.fetchall()]
    allowed_orders={r['order_id'] for r in raw['fact_order'] if r['region_id'] in identity['regions']}
    raw['fact_order_item']=[r for r in raw['fact_order_item'] if r['order_id'] in allowed_orders]
    allowed_items={r['item_id'] for r in raw['fact_order_item']}
    raw['fact_refund']=[r for r in raw['fact_refund'] if r['item_id'] in allowed_items]
    reference=expected(dict(metric_id='net_sales',group='model',month=None),raw)
    passed=bool(answer and answer['status']=='success' and answer['generation']==manifest['generation'])
    aliases={r['product_id']:r['product_name'] for r in raw['dim_product']}; observed={}
    if passed:
        for row in answer['result']['rows']:
            names={aliases.get(v,v) for v in row if isinstance(v,str) and aliases.get(v,v) in reference}
            if len(names)!=1: passed=False; break
            name=names.pop()
            if name in observed: passed=False; break
            numbers=[v for v in row if isinstance(v,(int,float)) and not isinstance(v,bool)]
            observed[name]=numbers
        passed=passed and observed.keys()==reference.keys() and all(any(abs(v-reference[k][0])<=.011 for v in observed[k]) for k in reference)
    report=dict(generation=manifest['generation'],job_id=task,real_model=True,regions=identity['regions'],passed=passed,
        oracle='independent per-product raw-row Python arithmetic with trusted regional filter',answer=answer,worker_state=job['state'],children_stopped=True)
    (ROOT/'reports/petkit500_released_model_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(generation=manifest['generation'],passed=passed,worker_state=job['state']),ensure_ascii=False),flush=True)
    assert passed,'新版本区域模型问数未通过独立核验；已保存实际结果'

if __name__=='__main__': main()
