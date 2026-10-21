"""Supplement finalization of an already completed load phase; preserve failures."""
import json,sqlite3,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.local_load_transport import LocalLoadClient
from scripts.start_simulated import running
from scripts.accept_durable_stability import free,job_fingerprint,sha
from src.petkit.settings import DATA

def main():
    path=max((ROOT/'reports/soak_runs').glob('*/results.json'),key=lambda p:p.stat().st_mtime)
    source=json.loads(path.read_text(encoding='utf-8'))
    assert not source['completed']
    assert source['failure']['message']=='HTTP phase=send path=/ops/health error=ConnectionAbortedError'
    stage=source['stages'][0]
    assert len(source['stages'])==1 and stage['offering_seconds']==600 and not stage['errors']
    assert stage['completed']==stage['counts']['submitted'] and all(d['oracle_passed'] for d in stage['details'])
    assert source['children_stopped'] and source['original_jobs_unchanged']
    data=Path(source['isolated_directory'])
    with sqlite3.connect(data/'control.sqlite3') as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]==0
        actual={r[0]:r[1:] for r in c.execute('SELECT id,state,result FROM jobs')}
        for detail in stage['details']:
            state,result=actual[detail['id']]
            assert state=='completed' and json.loads(result)['status']==detail['answer_status']
    before=job_fingerprint(data)
    original=job_fingerprint(DATA)
    generation=source['generation'];warehouse=sha(DATA/generation/'warehouse.duckdb')
    original_pointer=sha(DATA/'current.json')
    keys=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))
    a,b=free(),free()
    while a==b:b=free()
    client=None;children=[]
    with running('demo',a,b,data_dir=data) as services:
        children=services['processes'];client=LocalLoadClient(services['api_url'])
        try:
            headers={'Authorization':'Bearer '+keys['global_analyst']}
            initial,status,_,_=client.request('/ops/health',headers)
            assert status==200 and initial['ready'] and initial['queue']['pending']==0
            # Uvicorn's normal keep-alive expiry must not poison a later probe.
            time.sleep(6)
            final,status,_,_=client.request('/ops/health',headers)
            assert status==200 and final['ready'] and final['queue']['pending']==0 and final['supervisor']['restarts']==0
            assert client.idle_renewals==1 and len(client.connections)==2
        finally:client.close()
    assert all(p.poll() is not None for p in children)
    assert job_fingerprint(data)==before and job_fingerprint(DATA)==original
    assert sha(data/generation/'warehouse.duckdb')==warehouse
    assert sha(DATA/generation/'warehouse.duckdb')==warehouse and sha(DATA/'current.json')==original_pointer
    result=dict(source_report=path.relative_to(ROOT).as_posix(),date='2026-10-21',
        original_script_completed=False,load_phase_seconds=600,load_phase_tasks=stage['completed'],
        load_phase_oracle_passed=True,pending_zero_before_relaunch=True,job_results_persisted=True,
        finalization_supplement_passed=True,idle_seconds_tested=6,idle_renewals=client.idle_renewals,
        client_connections_created=len(client.connections),client_connections_closed=client.closed,
        final_queue_pending=0,worker_restarts_in_supplement=0,original_report_unchanged=True,
        restored_jobs_unchanged=True,original_jobs_unchanged=True,original_pointer_unchanged=True,
        original_warehouse_unchanged=True,children_stopped=True,real_llm_called=False,
        separate_load_and_finalization_phases=True,first_short_connection_timeout_root_cause_resolved=False)
    (ROOT/'reports/petkit500_soak_finalization_supplement.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
