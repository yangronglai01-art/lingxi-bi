"""Bounded end-to-end load experiment against the canonical HTTPS API."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

import httpx


def attempt(client, question, timeout):
    started=time.monotonic(); job=None; terminal=False
    result=dict(outcome='transport_error',elapsed_seconds=0)
    try:
        response=client.post('/api/v1/analytics/queries',headers={'Idempotency-Key':'load-'+uuid.uuid4().hex},json={'question':question})
        if response.status_code!=202:
            return dict(outcome='rejected_'+str(response.status_code),elapsed_seconds=time.monotonic()-started)
        job=response.json()['id']; path='/api/v1/analytics/queries/'+job
        while time.monotonic()-started<timeout:
            response=client.get(path);response.raise_for_status();state=response.json()
            if state['state'] in {'completed','failed','interrupted','cancelled'}:
                terminal=True; answer=state.get('result') or {}
                result=dict(outcome='ok' if state['state']=='completed' and answer.get('status')=='ok' else 'not_ok',
                    generation_mode=answer.get('generation_mode'),source_kind=answer.get('source_kind'))
                break
            time.sleep(.25)
        else:result=dict(outcome='timeout')
    except (httpx.HTTPError,ValueError,KeyError,TypeError):pass
    finally:
        if job and not terminal:
            try:client.post('/api/v1/analytics/queries/'+job+'/cancel')
            except httpx.HTTPError:pass
    result['elapsed_seconds']=time.monotonic()-started
    return result


def summarize(records,wall_seconds):
    times=sorted(r['elapsed_seconds'] for r in records)
    successful=[r for r in records if r['outcome']=='ok']
    def percentile(values,p):return sorted(values)[math.ceil(p*len(values))-1] if values else None
    return dict(attempted=len(records),successful=len(successful),outcomes={o:sum(r['outcome']==o for r in records) for o in sorted({r['outcome'] for r in records})},
        all_attempts_p95_seconds=percentile(times,.95),all_attempts_p99_seconds=percentile(times,.99),
        success_p95_seconds=percentile([r['elapsed_seconds'] for r in successful],.95),
        achieved_success_requests_per_second=len(successful)/wall_seconds if wall_seconds>0 else None,
        all_enterprise_source=bool(successful) and all(r.get('source_kind')=='enterprise' for r in successful),
        generation_modes=sorted({r.get('generation_mode','unknown') or 'unknown' for r in successful}),
        note='Closed-loop single-question experiment, not a capacity or accuracy certification. Correlate with real resource/queue metrics and representative workloads; cache/template and model paths differ.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('api','question','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--requests',type=int,default=10);p.add_argument('--concurrency',type=int,default=1);p.add_argument('--timeout',type=int,default=120)
    a=p.parse_args();u=urlsplit(a.api)
    if u.scheme!='https' or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        p.error('HTTPS origin required, without credentials, path, query or fragment')
    if not 1<=a.requests<=200 or not 1<=a.concurrency<=8 or not 1<=a.timeout<=300:p.error('Experiment exceeds bounded budget')
    token=os.environ.get('LINGXI_LOAD_TOKEN')
    if not token:p.error('Inject LINGXI_LOAD_TOKEN through secure environment')
    started=time.monotonic()
    with httpx.Client(base_url=a.api,headers={'Authorization':'Bearer '+token},timeout=10,follow_redirects=False,
                      limits=httpx.Limits(max_connections=a.concurrency)) as client:
        with ThreadPoolExecutor(max_workers=a.concurrency) as pool:
            records=list(pool.map(lambda _:attempt(client,a.question,a.timeout),range(a.requests)))
    result=summarize(records,time.monotonic()-started)
    result.update(concurrency=a.concurrency,records=records)
    with Path(a.out).open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print('Bounded experiment recorded; no capacity certification inferred.')
    if result['successful']!=a.requests:raise SystemExit(2)


if __name__=='__main__':main()
