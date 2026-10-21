"""Real global/regional receipt questions and persistent permission acceptance."""
import json,socket,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.start_simulated import running
from src.petkit.settings import DATA,artifacts
from src.petkit.reference import expected

def free():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def main():
    keys=json.loads((DATA/'simulated_credentials.json').read_text(encoding='utf-8'))
    a,b=free(),free()
    while a==b:b=free()
    results={};identities={};denials={}
    question='2026年9月各仓库采购收货数量'
    with running('llm',a,b) as services:
        def call(subject,path,body=None):
            req=Request(services['api_url']+path,headers={'Authorization':'Bearer '+keys[subject],'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
            with urlopen(req,timeout=10) as response:return json.load(response)
        def job(subject,q):
            task=call(subject,'/jobs',dict(question=q))['id'];deadline=time.monotonic()+100
            while time.monotonic()<deadline:
                value=call(subject,'/jobs/'+task)
                if value['state'] not in {'queued','running'}:return value
                time.sleep(.15)
            raise TimeoutError('收货问数任务未完成')
        for subject in ['global_analyst','regional_supply']:
            identities[subject]=call(subject,'/me');results[subject]=job(subject,question)
        for subject in ['regional_sales','regional_service','restricted_viewer']:
            answer=job(subject,question).get('result') or {}
            denials[subject]=answer.get('status')=='denied' and not answer.get('result')
        answer=job('regional_supply','各型号净销售额').get('result') or {}
        denials['supply_sales_metric']=answer.get('status')=='denied' and not answer.get('result')
        try:call('regional_supply','/jobs/'+results['global_analyst']['id'])
        except HTTPError as exc:history_isolated=exc.code in {403,404}
        else:history_isolated=False
        children=services['processes']
    assert all(p.poll() is not None for p in children)
    folder,manifest=artifacts();catalog=json.loads((folder/'catalog.json').read_text(encoding='utf-8'))
    import duckdb
    raw={}
    with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
        for name,t in catalog['tables'].items():
            if t['core']:
                cur=c.execute('SELECT * FROM "'+name+'"');cols=[d[0] for d in cur.description]
                raw[name]=[dict(zip(cols,row)) for row in cur.fetchall()]
    grades={}
    for subject,value in results.items():
        regions=identities[subject]['regions'];records=dict(raw)
        warehouses={r['warehouse_id']:r['warehouse_name'] for r in raw['dim_warehouse']}
        visible={r['warehouse_id'] for r in raw['dim_warehouse'] if not regions or r['region_id'] in regions}
        records['fact_receipt']=[r for r in raw['fact_receipt'] if r['warehouse_id'] in visible]
        reference=expected(dict(metric_id='received_units',group='warehouse',month='2026-09'),records)
        answer=value.get('result') or {};passed=answer.get('status')=='success' and answer.get('generation')==manifest['generation'];observed={}
        if passed:
            result=answer['result'];idx=result['columns'].index('received_units')
            for row in result['rows']:
                names={warehouses.get(v,v) for v in row if isinstance(v,str) and warehouses.get(v,v) in reference}
                if len(names)!=1:passed=False;break
                name=names.pop()
                if name in observed:passed=False;break
                observed[name]=(row[idx],)
            passed=passed and observed==reference
        grades[subject]=dict(passed=passed,regions=regions,expected=reference,observed=observed,answer=answer,job_id=value['id'])
    report=dict(generation=manifest['generation'],question=question,real_model_questions=2,oracle='independent Python sum of fact_receipt.quantity by warehouse and receipt month with trusted employee region filter',grades=grades,denials=denials,history_isolated=history_isolated,children_stopped=True,passed=all(g['passed'] for g in grades.values()) and all(denials.values()) and history_isolated)
    (ROOT/'reports/petkit500_receipt_model_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(generation=manifest['generation'],passed=report['passed'],global_units=sum(v[0] for v in grades['global_analyst']['observed'].values()),regional_units=sum(v[0] for v in grades['regional_supply']['observed'].values()),permission_denials=denials),ensure_ascii=False),flush=True)
    assert report['passed'],'收货问数验收未全部通过，已保留真实结果'

if __name__=='__main__':main()
