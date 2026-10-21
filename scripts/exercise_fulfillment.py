"""Local mock WMS drill: stage, publish, duplicate, historical rollback and replay."""
import json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import duckdb
from src.petkit import ingestion,releases,row_policy
from src.petkit.settings import DATA
from src.petkit.access import Principal

def main():
    prior=json.loads((ROOT/'reports/petkit500_order_ingestion_acceptance.json').read_text(encoding='utf-8'))
    source=json.loads(Path(prior['batch_file']).read_text(encoding='utf-8'))
    base=json.loads((DATA/'current.json').read_text())['generation']
    suffix=uuid.uuid4().hex[:10]; events=[]; before={}
    with duckdb.connect(str(releases.folder_for(base)/'warehouse.duckdb'),read_only=True) as c:
        for order in [e for e in source['events'] if e['kind']=='order.paid']:
            region=c.execute('SELECT region_id FROM fact_order WHERE order_id=?',[order['order_id']]).fetchone()[0]
            warehouse=c.execute('SELECT warehouse_id FROM dim_warehouse WHERE region_id=? ORDER BY warehouse_id LIMIT 1',[region]).fetchone()[0]
            if c.execute('SELECT 1 FROM fact_shipment WHERE order_id=?',[order['order_id']]).fetchone(): raise ValueError('本演练要求上阶段新订单尚未发货；不能重复运行以制造新的物流事实')
            e=dict(event_id=f'wms-{suffix}-{region}',kind='order.shipped',shipment_id=f'WMS-{suffix}-{region}',order_id=order['order_id'],warehouse_id=warehouse,ship_date='2026-09-02')
            events.append(e)
            for sku,q in c.execute('SELECT sku_id,SUM(quantity) FROM fact_order_item WHERE order_id=? GROUP BY sku_id',[order['order_id']]).fetchall():
                snapshots=c.execute('SELECT stock_date,quantity FROM fact_inventory_day WHERE sku_id=? AND warehouse_id=? ORDER BY stock_date',[sku,warehouse]).fetchall()
                before[region]=dict(sku=sku,warehouse=warehouse,quantity=int(q),snapshots=[(day.isoformat(),v) for day,v in snapshots])
        sales_before=c.execute('SELECT SUM(paid_amount) FROM fact_order').fetchone()[0]
    path=DATA/'ingestion/inbox'/f'fulfillment-{suffix}.json'
    releases.write(path,dict(version=1,source='mock_wms',batch_id='fulfillment-'+suffix,events=events))
    report=dict(parent=base,batch_file=str(path),before=before,completed=False,real_llm_called=False)
    out=ROOT/'reports/petkit500_fulfillment_acceptance.json'
    def save(): releases.write(out,report)
    save()
    # Parent must be revalidated under the new code before a historical rollback.
    report['parent_receipt']=releases.validate(base)['id'];save()
    staged=ingestion.ingest(path);report['staged']=staged;save()
    gen=staged['candidate']; receipt=releases.validate(gen)
    report['receipt']=receipt;save()
    def check(generation):
        with duckdb.connect(str(releases.folder_for(generation)/'warehouse.duckdb'),read_only=True) as c:
            assert c.execute('SELECT SUM(paid_amount) FROM fact_order').fetchone()[0]==sales_before
            for region,state in before.items():
                for day,qty in state['snapshots']:
                    actual=c.execute('SELECT quantity FROM fact_inventory_day WHERE stock_date=? AND sku_id=? AND warehouse_id=?',[day,state['sku'],state['warehouse']]).fetchone()[0]
                    assert actual==qty-(state['quantity'] if day>='2026-09-02' else 0)
        isolation={}
        for region in ['R1','R2']:
            with duckdb.connect(str(releases.folder_for(generation)/'warehouse.duckdb'),read_only=True) as c:
                row_policy.install(c,Principal(subject='regional-warehouse-test',regions=(region,),managed=True),['fact_shipment','fact_stock_movement','fact_inventory_day'])
                visible=c.execute('SELECT shipment_id FROM fact_shipment WHERE shipment_id IN (?,?)',[e['shipment_id'] for e in events]).fetchall()
                assert visible==[(next(e['shipment_id'] for e in events if e['shipment_id'].endswith(region)),)]
                movements=c.execute('SELECT quantity FROM fact_stock_movement WHERE movement_id LIKE ?',['WMS-'+suffix+'%']).fetchall()
                assert movements==[(-2,)]
                isolation[region]=dict(visible_new_shipments=1,new_stock_delta=-2,other_region_hidden=True)
        return isolation
    report['region_isolation']=check(gen)
    report['publication']=releases.activate(gen,actor='mock-wms/publish',expected_current=base)
    duplicate=ingestion.ingest(path);assert duplicate['state']=='already_applied' and duplicate['applied']==0
    report['duplicate']=duplicate;save()
    report['rollback']=releases.activate(base,actor='mock-wms/rollback-drill',expected_current=gen,allow_historical=True);save()
    replay=ingestion.ingest(path);assert replay['applied']==2
    receipt=releases.validate(replay['candidate']); report['replay']=replay;report['final_receipt']=receipt;save()
    report['replay_isolation']=check(replay['candidate'])
    report['final_publication']=releases.activate(replay['candidate'],actor='mock-wms/replay-publish',expected_current=base)
    report.update(completed=True,stock_units_shipped=4,events=2,sales_unchanged=True,snapshot_corrections_verified=True,rollback_replay_once=True);save()
    print(json.dumps(dict(completed=True,generation=replay['candidate'],events=2,stock_units_shipped=4,source_events=receipt['gates']['source_reconciliation']['events']),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
