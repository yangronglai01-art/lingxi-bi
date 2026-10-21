"""Bounded closed-loop HTTP load on a fresh restored instance; no paid LLM."""
import argparse
import platform
import http.client
from urllib.parse import urlsplit
import concurrent.futures
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import uuid
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running
from scripts.backup_simulated import backup,restore_drill
from scripts.accept_durable_stability import free,job_fingerprint,sha
from src.petkit.settings import DATA
from src.petkit.reference import expected
from scripts.windows_process_metrics import snapshot as process_snapshot
from scripts.local_load_transport import LocalLoadClient

def distribution(values):
    if not values:return None
    ordered=sorted(values)
    return dict(count=len(values),p50=round(ordered[math.ceil(.5*len(values))-1],4),
        p95=round(ordered[math.ceil(.95*len(values))-1],4),maximum=round(ordered[-1],4))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=600)
    args=parser.parse_args()
    if not 60<=args.seconds<=7200:parser.error('seconds must be between 60 and 7200')
    probe=process_snapshot(os.getpid())
    assert probe['rss_mb']>0 and probe['handles']>0 and probe['cpu_seconds']>=0
    try:process_snapshot(4294967295)
    except OSError:pass
    else:raise AssertionError('Sampler must reject an invalid PID')
    run=ROOT/'reports/soak_runs'/uuid.uuid4().hex[:12]
    run.mkdir(parents=True)
    report=dict(kind='bounded Windows resource and durable-job soak',mode='demo',
        real_llm_called=False,production_capacity_measured=False,completed=False,stages=[])
    def save(): (run/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    report.update(transport='HTTP/1.1 per-thread persistent connections, expire idle sockets after 4s; no automatic retry',
        prior_runs=[str(p.relative_to(ROOT)) for p in (ROOT/'reports/soak_runs').glob('*/results.json') if p.parent!=run])
    report.update(code_sha256={name:sha(ROOT/name) for name in [
        'scripts/accept_resource_soak.py','scripts/local_load_transport.py',
        'scripts/windows_process_metrics.py','src/petkit/service.py','src/petkit/control.py']},
        environment=dict(python=platform.python_version(),platform=platform.platform(),logical_cpu_count=os.cpu_count()))
    load_client=None
    original_jobs=job_fingerprint(DATA)
    original_pointer=sha(DATA/'current.json')
    original_gen=json.loads((DATA/'current.json').read_text(encoding='utf-8'))['generation']
    original_warehouse=sha(DATA/original_gen/'warehouse.duckdb')
    children=[]
    try:
        drill=restore_drill(backup())
        data=Path(drill['restored_directory'])
        generation=json.loads((data/'current.json').read_text(encoding='utf-8'))['generation']
        warehouse=data/generation/'warehouse.duckdb'
        warehouse_before=sha(warehouse)
        credentials=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))
        report.update(generation=generation,isolated_directory=str(data),backup_integrity_verified=True,
            pattern='Each lane submits one job, polls until terminal, then submits the next; no arrival-rate SLA',
            steady_seconds=args.seconds,concurrency=4,job_timeout_seconds=90,
            minimum_stage_job_completions=1,stop_conditions=['wrong result','unexpected HTTP/status','job timeout','unhealthy readiness','worker restart','resource growth threshold'])
        save()
        raw={}
        import duckdb
        cat=json.loads((data/generation/'catalog.json').read_text(encoding='utf-8'))
        with duckdb.connect(str(warehouse),read_only=True) as c:
            for name,table in cat['tables'].items():
                if table['core']:
                    result=c.execute('SELECT * FROM "'+name+'"')
                    columns=[v[0] for v in result.description]
                    raw[name]=[dict(zip(columns,row)) for row in result.fetchall()]
        questions={
            'global_analyst':('各型号净销售额','net_sales'),
            'regional_sales':('各型号净销售额','net_sales'),
            'regional_service':('各型号售出30天售后设备率','service30'),
            'regional_supply':('2026年9月各仓库采购收货数量','received_units'),
            'restricted_viewer':('各型号净销售额',None),
        }
        with sqlite3.connect(data/'control.sqlite3') as c:
            regions={subject:json.loads(value) for subject,value in c.execute('SELECT subject,regions FROM users')}
        references={}
        for subject,(_,metric) in questions.items():
            if not metric: continue
            allowed=regions[subject]
            rows=dict(raw)
            orders={r['order_id'] for r in raw['fact_order'] if not allowed or r['region_id'] in allowed}
            rows['fact_order']=[r for r in raw['fact_order'] if r['order_id'] in orders]
            rows['fact_order_item']=[r for r in raw['fact_order_item'] if r['order_id'] in orders]
            items={r['item_id'] for r in rows['fact_order_item']}
            rows['fact_refund']=[r for r in raw['fact_refund'] if r['item_id'] in items]
            rows['bridge_order_device']=[r for r in raw['bridge_order_device'] if r['item_id'] in items]
            warehouses={r['warehouse_id'] for r in raw['dim_warehouse'] if not allowed or r['region_id'] in allowed}
            rows['fact_receipt']=[r for r in raw['fact_receipt'] if r['warehouse_id'] in warehouses]
            references[subject]=expected(dict(metric_id=metric,group='warehouse' if metric=='received_units' else 'model',
                month='2026-09' if metric=='received_units' else None),rows)
        a,b=free(),free()
        while a==b:b=free()
        with running('demo',a,b,data_dir=data) as services:
            children=services['processes']
            load_client=LocalLoadClient(services['api_url'])
            def request(subject,path,body=None,key=None):
                headers={'Authorization':'Bearer '+credentials[subject],'Content-Type':'application/json'}
                if key:headers['Idempotency-Key']=key
                return load_client.request(path,headers,body)
            def wait(subject,task):
                deadline=time.monotonic()+90
                polls=0
                while time.monotonic()<deadline:
                    job,status,_,_=request(subject,'/jobs/'+task)
                    polls+=1
                    if status!=200:raise RuntimeError('Unexpected job polling status '+str(status))
                    if job['state'] not in {'queued','running'}:return job,polls
                    time.sleep(.25)
                raise TimeoutError('Job exceeded 90 seconds')
            warm,status,_,_=request('global_analyst','/jobs',dict(question=questions['global_analyst'][0]),'warm-'+run.name)
            assert status==202 and wait('global_analyst',warm['id'])[0]['result']['status']=='success'
            # Warm all policy/retrieval routes before taking resource baselines.
            for subject,(question,metric) in questions.items():
                value,status,_,_=request(subject,'/jobs',dict(question=question),'warm-'+subject+'-'+run.name)
                assert status==202
                answer=wait(subject,value['id'])[0]['result']
                assert answer['status']==('success' if metric else 'denied')
            baseline={name:process_snapshot(process.pid) for name,process in services['current_processes'].items()}
            baseline_disk=sum(p.stat().st_size for p in data.iterdir() if p.is_file() and p.name.startswith('control.sqlite3'))
            report.update(warmup_excluded=True,resource_baseline=baseline,
                resource_growth_limits=dict(worker_rss_mb=1536,api_rss_mb=512,ui_rss_mb=256,handles_per_process=512,control_disk_mb=512))
            save()
            subjects=list(questions)
            for stage_index,(concurrency,seconds) in enumerate([(4,args.seconds)]):
                stop=threading.Event()
                lock=threading.Lock()
                details=[]
                samples=[]
                errors=[]
                counts=dict(submitted=0,rejected_429=0,polls=0,monitor_errors=0)
                begin=time.monotonic()
                until=begin+seconds
                def monitor():
                    next_progress=60
                    while not stop.is_set() and time.monotonic()<until:
                        try:
                            value,status,_,_=request('global_analyst','/ops/health')
                            if status!=200 or not value['ready'] or value['supervisor']['restarts']!=0:
                                raise RuntimeError('Health degraded or worker restarted')
                            resources={name:process_snapshot(process.pid) for name,process in services['current_processes'].items()}
                            disk=sum(p.stat().st_size for p in data.iterdir() if p.is_file() and p.name.startswith('control.sqlite3'))
                            for name,current in resources.items():
                                limit={'worker':1536,'api':512,'ui':256}[name]
                                if current['rss_mb']-baseline[name]['rss_mb']>limit or current['handles']-baseline[name]['handles']>512:
                                    raise RuntimeError('Resource growth threshold exceeded: '+name)
                            if disk-baseline_disk>512*1024**2:raise RuntimeError('Control-store growth threshold exceeded')
                            elapsed=time.monotonic()-begin
                            with lock:samples.append(dict(pending=value['queue']['pending'],queued=value['queue']['queued'],
                                max_employee_pending=value['queue']['max_employee_pending'],elapsed_seconds=round(elapsed,3),
                                resources=resources,control_disk_mb=round(disk/1024**2,3)))
                            with (run/'resource_samples.jsonl').open('a',encoding='utf-8') as stream:
                                stream.write(json.dumps(samples[-1],ensure_ascii=False)+'\n')
                            if elapsed>=next_progress:
                                next_progress+=60
                                print(json.dumps(dict(elapsed_seconds=round(elapsed),completed=len(details),
                                    pending=value['queue']['pending'],resources=resources),ensure_ascii=False),flush=True)
                        except Exception as exc:
                            with lock:errors.append('monitor:'+type(exc).__name__+':'+str(exc)[:200]);counts['monitor_errors']+=1
                            stop.set()
                            break
                        stop.wait(1)
                def lane(number):
                    iteration=0
                    while not stop.is_set() and time.monotonic()<until:
                        subject=subjects[(number+iteration)%len(subjects)]
                        iteration+=1
                        try:
                            value,status,submission,headers=request(subject,'/jobs',dict(question=questions[subject][0]),
                                f'{run.name}-{stage_index}-{number}-{iteration}')
                            if status==429:
                                if headers.get('Retry-After',headers.get('retry-after'))!='5':raise RuntimeError('Missing Retry-After')
                                with lock:counts['rejected_429']+=1
                                stop.wait(5)
                                continue
                            if status!=202:raise RuntimeError('Unexpected submit status '+str(status))
                            with lock:counts['submitted']+=1
                            job,polls=wait(subject,value['id'])
                            answer=job.get('result') or {}
                            good=job['state']=='completed'
                            if questions[subject][1]:
                                result=answer.get('result') or {}
                                rows=result.get('rows',[])
                                observed={r[0]:r[1:] for r in rows}
                                wanted=references[subject]
                                good=good and answer.get('status')=='success' and len(rows)==len(observed) and observed.keys()==wanted.keys()
                                good=good and all(len(observed[k])==len(v) and all(abs(float(x)-float(y))<=.011
                                    for x,y in zip(observed[k],v)) for k,v in wanted.items())
                            else:good=good and answer.get('status')=='denied' and not answer.get('result')
                            if not good:raise RuntimeError('Independent result oracle mismatch')
                            detail=dict(subject=subject,id=value['id'],answer_status=answer.get('status'),oracle_passed=good,
                                completed_elapsed_seconds=round(time.monotonic()-begin,3),
                                submission_seconds=submission,end_to_end_seconds=job['updated']-job['created'],
                                worker_execution_seconds=answer.get('elapsed_ms',0)/1000)
                            with lock:details.append(detail);counts['polls']+=polls
                        except Exception as exc:
                            with lock:errors.append('lane:'+type(exc).__name__+':'+str(exc)[:120])
                            stop.set()
                            break
                watcher=threading.Thread(target=monitor,daemon=True)
                watcher.start()
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                    list(pool.map(lane,range(concurrency)))
                stop.set();watcher.join(timeout=20)
                elapsed=time.monotonic()-begin
                successes=[d for d in details if d['answer_status']=='success']
                stage=dict(concurrency=concurrency,offering_seconds=seconds,elapsed_including_drain_seconds=round(elapsed,3),
                    completed=len(details),success=len(successes),expected_denied=len(details)-len(successes),
                    success_tasks_per_second=round(len(successes)/elapsed,3),all_terminal_tasks_per_second=round(len(details)/elapsed,3),
                    submission_seconds=distribution([d['submission_seconds'] for d in details]),
                    successful_end_to_end_seconds=distribution([d['end_to_end_seconds'] for d in successes]),
                    all_terminal_end_to_end_seconds=distribution([d['end_to_end_seconds'] for d in details]),
                    peak_sampled_pending=max((v['pending'] for v in samples),default=None),health_samples=len(samples),
                    errors=errors,counts=counts,details=details,queue_samples=samples)
                stage['resource_summary']={}
                for name in baseline:
                    values=[s['resources'][name] for s in samples]
                    if not values:raise RuntimeError('No resource samples')
                    stage['resource_summary'][name]=dict(baseline=baseline[name],last=values[-1],
                        peak_rss_mb=max(v['rss_mb'] for v in values),peak_handles=max(v['handles'] for v in values),
                        rss_growth_mb=round(values[-1]['rss_mb']-baseline[name]['rss_mb'],3),
                        handle_growth=values[-1]['handles']-baseline[name]['handles'])
                stage['minute_windows']=[]
                for lower in range(0,seconds,60):
                    selected=[d for d in successes if lower<=d['completed_elapsed_seconds']<lower+60]
                    stage['minute_windows'].append(dict(start_seconds=lower,success_count=len(selected),
                        successful_end_to_end_seconds=distribution([d['end_to_end_seconds'] for d in selected])))
                stage['control_disk_first_mb']=samples[0]['control_disk_mb']
                stage['control_disk_last_mb']=samples[-1]['control_disk_mb']
                report['stages'].append(stage)
                save()
                print(json.dumps({k:stage[k] for k in ['concurrency','offering_seconds','completed','success','expected_denied',
                    'success_tasks_per_second','successful_end_to_end_seconds','peak_sampled_pending','errors']},ensure_ascii=False),flush=True)
                if errors or not details:raise RuntimeError('Stage failed; no further ramp')
                assert elapsed>=seconds,'Load duration was shorter than requested'
            snapshot,status,_,_=request('global_analyst','/ops/health')
            assert status==200 and snapshot['ready'] and snapshot['supervisor']['restarts']==0
            assert snapshot['queue']['pending']==0
            report['final_queue_pending']=0
            report['worker_restarts']=0
        report['warehouse_unchanged']=sha(warehouse)==warehouse_before
        assert report['warehouse_unchanged']
        report['completed']=True
    except Exception as exc:
        report['failure']=dict(type=type(exc).__name__,message=str(exc)[:250])
        raise
    finally:
        if load_client:load_client.close()
        report['client_connections_created']=len(load_client.connections) if load_client else 0
        report['client_connections_closed']=load_client.closed if load_client else True
        report['client_idle_renewals']=load_client.idle_renewals if load_client else 0
        report['original_jobs_unchanged']=job_fingerprint(DATA)==original_jobs
        report['original_pointer_unchanged']=sha(DATA/'current.json')==original_pointer
        report['original_warehouse_unchanged']=sha(DATA/original_gen/'warehouse.duckdb')==original_warehouse
        report['children_stopped']=all(p.poll() is not None for p in children)
        save()
    assert all(report[k] for k in ['original_jobs_unchanged','original_pointer_unchanged','original_warehouse_unchanged','children_stopped'])
    print(json.dumps(dict(report=str(run/'results.json'),completed=report['completed'],
        tasks=sum(s['completed'] for s in report['stages']),children_stopped=report['children_stopped'])),flush=True)

if __name__=='__main__':main()
