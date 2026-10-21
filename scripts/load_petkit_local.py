"""Bounded localhost demo load, without external LLM requests."""
import concurrent.futures
import json
from pathlib import Path
import socket
import statistics
import sys
import time
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.start_interview import running

def free():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0)); return s.getsockname()[1]

def main():
    a,b=free(),free()
    while a==b: b=free()
    report=dict(kind='bounded local demo load',real_llm_called=False,not_production_capacity=True,rounds=[])
    with running('demo',a,b) as services:
        def ask(i):
            q=['各型号净销售额','各型号售出30天激活率','各型号期末库存'][i%3]
            start=time.monotonic()
            try:
                req=Request(services['api_url']+'/ask',headers={'Authorization':'Bearer '+services['token'],'Content-Type':'application/json'},data=json.dumps({'question':q}).encode())
                with urlopen(req,timeout=90) as response: answer=json.load(response)
                return dict(status=answer['status'],seconds=round(time.monotonic()-start,4),rows=answer['result']['row_count'] if answer.get('result') else 0,error=answer.get('error',''))
            except Exception as exc: return dict(status='transport_error',seconds=round(time.monotonic()-start,4),error=str(exc))
        warmup=ask(0); assert warmup['status']=='success',warmup
        report['warmup']=warmup
        for concurrency in [1,4,8]:
            start=time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                rows=list(pool.map(ask,range(12)))
            elapsed=time.monotonic()-start; latencies=sorted(r['seconds'] for r in rows)
            record=dict(concurrency=concurrency,requests=12,success=sum(r['status']=='success' for r in rows),elapsed_seconds=round(elapsed,3),qps=round(12/elapsed,2),p50=statistics.median(latencies),p95=latencies[min(len(latencies)-1,int(.95*len(latencies)))],rows=rows)
            report['rounds'].append(record)
            print(json.dumps({k:v for k,v in record.items() if k!='rows'}),flush=True)
        children=services['processes']
    report['children_stopped']=all(p.poll() is not None for p in children)
    (ROOT/'reports/petkit500_local_load.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert all(r['success']==r['requests'] for r in report['rounds'])

if __name__=='__main__': main()
