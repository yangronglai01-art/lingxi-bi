"""Real 500-table mock feed, duplicate, quarantine, rollback and replay drill."""
from datetime import date,timedelta
import json
from pathlib import Path
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.petkit import ingestion,releases,control
from src.petkit.settings import DATA,AS_OF

def state(generation):
    import duckdb
    with duckdb.connect(str(releases.folder_for(generation)/'warehouse.duckdb'),read_only=True) as c:
        counts={t:c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['fact_refund','fact_device_activation','fact_device_binding','fact_service_ticket','fact_ticket_status','fact_support_contact']}
        counts['refund_amount']=round(c.execute('SELECT SUM(amount) FROM fact_refund').fetchone()[0],2)
    return counts

def main():
    import duckdb
    base=json.loads((DATA/'current.json').read_text(encoding='utf-8'))['generation']
    report=dict(parent=base,real_llm_called=False,completed=False)
    out=ROOT/'reports/petkit500_ingestion_exercise.json'
    def save(): out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    save(); releases.validate(base)
    folder=releases.folder_for(base); suffix=uuid.uuid4().hex[:10]
    batch=dict(version=1,source='simulated_gateway',batch_id='landing-'+suffix,events=[])
    with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
        items=c.execute('''SELECT i.item_id,o.paid_date FROM fact_order_item i JOIN fact_order o ON i.order_id=o.order_id
          WHERE o.region_id='R1' AND i.amount-COALESCE((SELECT SUM(r.amount) FROM fact_refund r WHERE r.item_id=i.item_id),0)>=20 ORDER BY i.item_id LIMIT 3''').fetchall()
        devices=c.execute('''SELECT b.device_id,o.paid_date FROM bridge_order_device b
          JOIN fact_order_item i ON b.item_id=i.item_id JOIN fact_order o ON i.order_id=o.order_id
          WHERE o.region_id='R2' AND o.paid_date<=? AND NOT EXISTS(SELECT 1 FROM fact_device_activation a WHERE a.device_id=b.device_id)
          AND NOT EXISTS(SELECT 1 FROM fact_service_ticket t WHERE t.device_id=b.device_id) ORDER BY b.device_id LIMIT 3''',[date.fromisoformat(AS_OF)-timedelta(days=30)]).fetchall()
    assert len(items)==len(devices)==3
    for i,(item,paid) in enumerate(items):
        batch['events'].append(dict(event_id=f'{suffix}-r{i}',kind='refund.created',refund_id=f'ING-RF-{suffix}-{i}',item_id=item,refund_date=(paid+timedelta(days=1)).isoformat(),amount='10.00'))
    for i,(device,paid) in enumerate(devices):
        batch['events'].append(dict(event_id=f'{suffix}-a{i}',kind='device.activated',device_id=device,activation_date=(paid+timedelta(days=1)).isoformat()))
        batch['events'].append(dict(event_id=f'{suffix}-t{i}',kind='ticket.created',ticket_id=f'ING-T-{suffix}-{i}',device_id=device,fault_id='F1',employee_id='E1',created_date=(paid+timedelta(days=2)).isoformat()))
    inbox=DATA/'ingestion/inbox'; inbox.mkdir(parents=True,exist_ok=True)
    path=inbox/(batch['batch_id']+'.json'); releases.write(path,batch)
    report.update(batch_file=str(path),batch_id=batch['batch_id'],events=9,before=state(base)); save()
    first=ingestion.ingest(path)
    second=ingestion.ingest(path)
    assert first['applied']==second['applied']==9 and first['candidate']!=second['candidate']
    assert json.loads((DATA/'current.json').read_text())['generation']==base
    report.update(first_unpublished=first,second_unpublished=second,unpublished_retry_not_lost=True); save()
    releases.validate(first['candidate'])
    releases.validate(second['candidate'])
    releases.activate(second['candidate'],actor='mock-feed/publish',expected_current=base)
    duplicate=ingestion.ingest(path)
    assert duplicate['state']=='already_applied' and duplicate['applied']==0
    published=state(second['candidate'])
    for table in ['fact_refund','fact_device_activation','fact_device_binding','fact_service_ticket','fact_ticket_status','fact_support_contact']:
        assert published[table]-report['before'][table]==3
    assert abs(published['refund_amount']-report['before']['refund_amount']-30)<.011
    report.update(after=published,published_duplicate_noop=True); save()
    conflict=json.loads(json.dumps(batch)); conflict['events'][0]['amount']='11.00'
    conflict_path=inbox/(batch['batch_id']+'-conflict.json'); releases.write(conflict_path,conflict)
    try: ingestion.ingest(conflict_path)
    except ValueError: pass
    else: raise AssertionError('冲突批次被接受')
    bad=dict(version=1,source=batch['source'],batch_id=batch['batch_id']+'-invalid',events=[
        dict(event_id=suffix+'-valid-before-fail',kind='refund.created',refund_id='ING-GOOD-'+suffix,item_id=items[0][0],refund_date=(items[0][1]+timedelta(days=1)).isoformat(),amount='1.00'),
        dict(event_id=suffix+'-overrefund',kind='refund.created',refund_id='ING-BAD-'+suffix,item_id=items[0][0],refund_date=(items[0][1]+timedelta(days=1)).isoformat(),amount='99999999.99')])
    bad_path=inbox/(batch['batch_id']+'-invalid.json'); releases.write(bad_path,bad)
    try: ingestion.ingest(bad_path)
    except ValueError: pass
    else: raise AssertionError('超额退款批次被接受')
    assert state(second['candidate'])==published
    report.update(conflicting_batch_quarantined=True,invalid_batch_atomic_quarantine=True); save()
    releases.activate(base,actor='mock-feed/rollback',expected_current=second['candidate'],allow_historical=True)
    replay=ingestion.ingest(path)
    assert replay['applied']==9
    receipt=releases.validate(replay['candidate'])
    releases.activate(replay['candidate'],actor='mock-feed/replay',expected_current=base)
    assert state(replay['candidate'])==published
    try: releases.activate(first['candidate'],expected_current=replay['candidate'])
    except ValueError: pass
    else: raise AssertionError('过期父版本的候选覆盖了新数据')
    report.update(completed=True,rollback_replay_not_lost=True,replay_exactly_once=True,stale_parent_rejected=True,
        final_generation=replay['candidate'],replay=replay,release_gates=receipt['gates'])
    save()
    print(json.dumps({k:report[k] for k in ['completed','events','final_generation','unpublished_retry_not_lost','published_duplicate_noop','rollback_replay_not_lost','replay_exactly_once','stale_parent_rejected']},ensure_ascii=False),flush=True)

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
