"""Mock purchasing: partial receipts, overreceipt quarantine, rollback and replay."""
import json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import duckdb
from src.petkit import ingestion,releases,control,row_policy
from src.petkit.settings import DATA
from src.petkit.access import Principal

def main():
    base=json.loads((DATA/'current.json').read_text())['generation']
    suffix=uuid.uuid4().hex[:10];events=[];states={}
    with duckdb.connect(str(releases.folder_for(base)/'warehouse.duckdb'),read_only=True) as c:
        supplier=c.execute('SELECT supplier_id FROM dim_supplier ORDER BY supplier_id LIMIT 1').fetchone()[0]
        sku=c.execute('SELECT sku_id FROM dim_sku ORDER BY sku_id LIMIT 1').fetchone()[0]
        for region in ['R1','R2']:
            warehouse=c.execute('SELECT warehouse_id FROM dim_warehouse WHERE region_id=? ORDER BY warehouse_id LIMIT 1',[region]).fetchone()[0]
            prefix=f'PROC-{suffix}-{region}'
            events.append(dict(event_id=prefix+'-created',kind='purchase.created',purchase_id=prefix,supplier_id=supplier,purchase_date='2026-09-01',items=[dict(purchase_item_id=prefix+'-item',sku_id=sku,quantity=10,amount='1000.00')]))
            for index,day in enumerate(['2026-09-02','2026-09-03']):
                events.append(dict(event_id=prefix+f'-received{index}',kind='purchase.received',receipt_id=prefix+f'-receipt{index}',purchase_item_id=prefix+'-item',warehouse_id=warehouse,receipt_date=day,quantity=5))
            snapshots=c.execute('SELECT stock_date,quantity FROM fact_inventory_day WHERE sku_id=? AND warehouse_id=? ORDER BY stock_date',[sku,warehouse]).fetchall()
            states[region]=dict(warehouse=warehouse,sku=sku,snapshots=[(d.isoformat(),q) for d,q in snapshots])
        sales=c.execute('SELECT SUM(paid_amount) FROM fact_order').fetchone()[0]
    path=DATA/'ingestion/inbox'/f'procurement-{suffix}.json'
    batch=dict(version=1,source='mock_procurement',batch_id='procurement-'+suffix,events=events);releases.write(path,batch)
    out=ROOT/'reports/petkit500_procurement_acceptance.json'
    report=dict(parent=base,batch_file=str(path),events=6,stock_units_received=20,before=states,completed=False,real_llm_called=False)
    def save():releases.write(out,report)
    save();report['parent_receipt']=releases.validate(base)['id'];save()
    staged=ingestion.ingest(path);assert staged['applied']==6
    report['staged']=staged;save();report['receipt']=releases.validate(staged['candidate']);save()
    def check(generation):
        folder=releases.folder_for(generation)
        with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
            assert c.execute('SELECT SUM(paid_amount) FROM fact_order').fetchone()[0]==sales
            for region,state in states.items():
                for day,quantity in state['snapshots']:
                    increment=5*(day>='2026-09-02')+5*(day>='2026-09-03')
                    actual=c.execute('SELECT quantity FROM fact_inventory_day WHERE stock_date=? AND sku_id=? AND warehouse_id=?',[day,state['sku'],state['warehouse']]).fetchone()[0]
                    assert actual==quantity+increment
                latest=c.execute('SELECT quantity FROM fact_inventory_day WHERE sku_id=? AND warehouse_id=? ORDER BY stock_date DESC LIMIT 1',[state['sku'],state['warehouse']]).fetchone()[0]
                assert latest==state['snapshots'][-1][1]+10
        scope={}
        for region in states:
            with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
                p=Principal(subject='procurement-region-test',role='analyst',regions=(region,),managed=True)
                assert not p.allowed(dict(name='fact_purchase_order',domain='supply'))
                row_policy.install(c,p,['fact_receipt','fact_stock_movement','fact_inventory_day'])
                receipts=c.execute('SELECT receipt_id,quantity FROM fact_receipt WHERE receipt_id LIKE ? ORDER BY receipt_id',['PROC-'+suffix+'%']).fetchall()
                assert len(receipts)==2 and sum(q for _,q in receipts)==10 and all(region in rid for rid,_ in receipts)
                assert c.execute('SELECT SUM(quantity) FROM fact_stock_movement WHERE movement_id LIKE ?',['PROC-'+suffix+'%']).fetchone()[0]==10
                scope[region]=dict(visible_receipts=2,received_units=10,other_region_hidden=True,purchase_totals_denied=True)
        return scope
    report['region_isolation']=check(staged['candidate'])
    report['publication']=releases.activate(staged['candidate'],actor='mock-procurement/publish',expected_current=base);save()
    duplicate=ingestion.ingest(path);assert duplicate['state']=='already_applied' and duplicate['applied']==0
    report['duplicate']=duplicate
    # A valid new purchase followed by a one-unit overreceipt must not persist either.
    bad_purchase=dict(events[0]);bad_purchase.update(event_id='bad-'+suffix,purchase_id='BAD-'+suffix,items=[dict(purchase_item_id='BAD-ITEM-'+suffix,sku_id=sku,quantity=1,amount='100.00')])
    bad_receipt=dict(events[1]);bad_receipt.update(event_id='over-'+suffix,receipt_id='OVER-'+suffix,quantity=1)
    bad_path=path.with_name('overreceipt-'+suffix+'.json')
    releases.write(bad_path,dict(version=1,source='mock_procurement',batch_id='overreceipt-'+suffix,events=[bad_purchase,bad_receipt]))
    try:ingestion.ingest(bad_path)
    except ValueError as exc:
        assert '超过采购数量' in str(exc)
        report['overreceipt_quarantined']=True
    else:raise AssertionError('超收批次未被隔离')
    with control.db() as c:
        attempt=c.execute('SELECT candidate,state FROM ingestion_attempts WHERE batch_id=? ORDER BY created DESC LIMIT 1',('overreceipt-'+suffix,)).fetchone()
        assert attempt['state']=='quarantined'
        with duckdb.connect(str(releases.folder_for(attempt['candidate'])/'warehouse.duckdb'),read_only=True) as db:
            assert db.execute('SELECT count(*) FROM fact_purchase_order WHERE purchase_id=?',[bad_purchase['purchase_id']]).fetchone()[0]==0
    report['failed_batch_atomic']=True;save()
    report['rollback']=releases.activate(base,actor='mock-procurement/rollback',expected_current=staged['candidate'],allow_historical=True);save()
    replay=ingestion.ingest(path);assert replay['applied']==6
    report['replay']=replay;report['final_receipt']=releases.validate(replay['candidate']);save()
    report['replay_scope']=check(replay['candidate'])
    report['final_publication']=releases.activate(replay['candidate'],actor='mock-procurement/replay-publish',expected_current=base)
    assert ingestion.ingest(path)['state']=='already_applied'
    report.update(completed=True,sales_unchanged=True,rollback_replay_once=True,snapshot_corrections_verified=True);save()
    print(json.dumps(dict(completed=True,generation=replay['candidate'],stock_units_received=20,source_events=report['final_receipt']['gates']['source_reconciliation']['events']),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
