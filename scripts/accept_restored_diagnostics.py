"""Final restored application: ops authorization, read-only snapshot and warehouse job."""
import hashlib,json,socket,sqlite3,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running
from src.petkit.settings import DATA

def free():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def jobs_hash(root):
    with sqlite3.connect(root/'control.sqlite3') as c:values=c.execute('SELECT id,state,updated FROM jobs ORDER BY id').fetchall()
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()

def main():
    backup=json.loads((ROOT/'reports/petkit500_backup_drill.json').read_text(encoding='utf-8'));restored=Path(backup['restored_directory'])
    keys=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'));original=jobs_hash(DATA)
    assert restored.resolve()!=DATA.resolve();a,b=free(),free()
    while a==b:b=free()
    with running('demo',a,b,data_dir=restored) as services:
        def call(subject,path,body=None):
            req=Request(services['api_url']+path,headers={'Authorization':'Bearer '+keys[subject],'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
            try:
                with urlopen(req,timeout=10) as response:return json.load(response),response.status
            except HTTPError as exc:return json.load(exc),exc.code
        before=jobs_hash(restored);snapshot,status=call('global_analyst','/ops/health');assert status==200 and snapshot['ready']
        assert jobs_hash(restored)==before
        for subject in ['regional_sales','regional_service','regional_supply','restricted_viewer']:assert call(subject,'/ops/health')[1]==403
        serialized=json.dumps(snapshot);assert services['session'] not in serialized and all(value not in serialized for value in keys.values())
        task,status=call('regional_supply','/jobs',dict(question='2026年9月各仓库采购收货数量'));assert status==202
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            value,status=call('regional_supply','/jobs/'+task['id']);assert status==200
            if value['state'] not in {'queued','running'}:break
            time.sleep(.1)
        answer=value.get('result') or {};assert answer.get('status')=='success'
        result=answer['result'];assert len(result['rows'])==1 and result['rows'][0][result['columns'].index('received_units')]==10
        children=services['processes']
    assert all(p.poll() is not None for p in children) and jobs_hash(DATA)==original
    report=dict(generation=snapshot['generation'],restored_directory=str(restored),ops_snapshot_ready=True,ops_read_only=True,unauthorized_roles_denied=True,raw_session_and_credentials_redacted=True,regional_received_units=10,original_jobs_unchanged=True,children_stopped=True,real_llm_called=False,passed=True)
    (ROOT/'reports/petkit500_restored_diagnostics_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=True,ops_ready=True,regional_received_units=10,children_stopped=True)),flush=True)

if __name__=='__main__':main()
