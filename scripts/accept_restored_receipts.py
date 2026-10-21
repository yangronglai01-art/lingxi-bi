"""No-paid-model warehouse acceptance in the independent restored instance."""
import json,socket,sys,time,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running
from src.petkit.settings import DATA

def free():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def main():
    backup=json.loads((ROOT/'reports/petkit500_backup_drill.json').read_text(encoding='utf-8'))
    restored=Path(backup['restored_directory']);assert restored.resolve()!=DATA.resolve()
    key=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))['regional_supply']
    a,b=free(),free()
    while a==b:b=free()
    with running('demo',a,b,data_dir=restored) as services:
        def call(path,body=None):
            request=Request(services['api_url']+path,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
            with urlopen(request,timeout=10) as response:return json.load(response)
        identity=call('/me');assert identity['role']=='supply' and identity['regions']==['R1']
        question='2026年9月各仓库采购收货数量'
        examples=call('/examples')['questions'];assert question in examples and '各型号净销售额' not in examples
        task=call('/jobs',dict(question=question))['id'];deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            job=call('/jobs/'+task)
            if job['state'] not in {'queued','running'}:break
            time.sleep(.1)
        answer=job.get('result') or {};assert answer.get('status')=='success'
        result=answer['result'];idx=result['columns'].index('received_units')
        assert len(result['rows'])==1 and result['rows'][0][idx]==10
        with sqlite3.connect(DATA/'control.sqlite3') as c:assert c.execute('SELECT count(*) FROM jobs WHERE id=?',(task,)).fetchone()[0]==0
        children=services['processes']
    assert all(p.poll() is not None for p in children)
    report=dict(generation=answer['generation'],restored_directory=str(restored),warehouse_role='supply',regions=identity['regions'],regional_received_units=10,examples_filtered=True,original_instance_untouched=True,real_llm_called=False,children_stopped=True,passed=True)
    (ROOT/'reports/petkit500_restored_receipts_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=True,regional_received_units=10,real_llm_called=False)),flush=True)

if __name__=='__main__':main()
