"""Actual 500-table candidate validation, publication, rollback and republish."""
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.petkit import releases,control
from src.petkit.builder import build
from src.petkit.settings import DATA

def main():
    initial=json.loads((DATA/'current.json').read_text(encoding='utf-8'))['generation']
    report=dict(started=time.time(),baseline=initial,real_llm_called=False,operations=[])
    out=ROOT/'reports/petkit500_release_exercise.json'
    def save(): out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    baseline=releases.validate(initial)
    report['baseline_orders']=baseline['gates']['data_quality']['orders']; save()
    candidate=build(orders=report['baseline_orders']+200,publish=False)['generation']
    report['candidate']=candidate
    assert json.loads((DATA/'current.json').read_text())['generation']==initial
    candidate_receipt=releases.validate(candidate)
    report['candidate_gates']=candidate_receipt['gates']; save()
    principal=control.principal_for('regional_sales')
    job=control.submit(principal,'各型号净销售额','release-exercise-'+str(time.time_ns()))
    published=releases.activate(candidate,expected_current=initial)
    assert control.get_job(principal,job)['state']=='superseded'
    report['old_queued_job_superseded']=True
    report['operations'].append(dict(action='publish',id=published['id'],target=candidate)); save()
    rollback=releases.activate(initial,actor='release-drill/rollback',expected_current=candidate)
    assert json.loads((DATA/'current.json').read_text())['generation']==initial
    report['operations'].append(dict(action='rollback',id=rollback['id'],target=initial)); save()
    republished=releases.activate(candidate,actor='release-drill/republish',expected_current=initial)
    report['operations'].append(dict(action='republish',id=republished['id'],target=candidate))
    report.update(completed=True,final_generation=candidate,orders=candidate_receipt['gates']['data_quality']['orders'],business_reference_passed=candidate_receipt['gates']['business_oracle']['passed'])
    save()
    print(json.dumps({k:v for k,v in report.items() if k not in {'operations','candidate_gates'}},ensure_ascii=False),flush=True)

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
