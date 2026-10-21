"""Start the restored application without overwriting the original instance."""
import json
from pathlib import Path
import socket
import sys
import time
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running
from src.petkit.settings import DATA

def free():
    with socket.socket() as s: s.bind(('127.0.0.1',0)); return s.getsockname()[1]

def main():
    report=json.loads((ROOT/'reports/petkit500_backup_drill.json').read_text(encoding='utf-8'))
    restored=Path(report['restored_directory'])
    key=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))['regional_sales']
    a,b=free(),free()
    while a==b: b=free()
    def call(base,path,body=None):
        req=Request(base+path,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
        with urlopen(req,timeout=10) as response: return json.load(response)
    with running('demo',a,b,data_dir=restored) as services:
        base=services['api_url']
        assert call(base,'/ready')['ready']
        identity=call(base,'/me'); assert identity['regions'] and identity['subject']=='regional_sales'
        task=call(base,'/jobs',{'question':'各型号净销售额'})['id']
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            job=call(base,'/jobs/'+task)
            if job['state'] not in {'queued','running'}: break
            time.sleep(.15)
        assert job['state']=='completed' and job['result']['status']=='success',job.get('error')
        # Verify that new task exists only in restored control storage.
        import sqlite3
        with sqlite3.connect(DATA/'control.sqlite3') as c:
            assert c.execute('SELECT count(*) FROM jobs WHERE id=?',(task,)).fetchone()[0]==0
        children=services['processes']
    assert all(p.poll() is not None for p in children)
    report.update(restored_application_ready=True,restored_regional_job_succeeded=True,original_instance_untouched=True,
        restored_job_id=task,children_stopped=True,scope='offline hash checks followed by real localhost application startup in independent restored directory')
    (ROOT/'reports/petkit500_backup_drill.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['restored_application_ready','restored_regional_job_succeeded','original_instance_untouched','children_stopped']}),flush=True)

if __name__=='__main__': main()
