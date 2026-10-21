from datetime import date
import pytest
import duckdb
from src.petkit.ingestion_models import Shipment
from src.petkit.ingestion import apply_transaction
from src.petkit.fulfillment import verify,check_inventory

def event(**changes):
    return Shipment.model_validate(dict(event_id='ship-event',kind='order.shipped',shipment_id='SH1',order_id='O1',warehouse_id='W1',ship_date='2026-09-02')|changes)

@pytest.fixture
def stock():
    c=duckdb.connect()
    for definition in ['fact_order(order_id VARCHAR,paid_date DATE,region_id VARCHAR)','dim_warehouse(warehouse_id VARCHAR,region_id VARCHAR)','fact_order_item(item_id VARCHAR,order_id VARCHAR,sku_id VARCHAR,quantity INTEGER)','dim_device(device_id VARCHAR,manufactured_date DATE)','bridge_order_device(device_id VARCHAR,item_id VARCHAR)','fact_device_activation(device_id VARCHAR,activation_date DATE)','fact_service_ticket(device_id VARCHAR,created_date DATE)','fact_shipment(shipment_id VARCHAR,order_id VARCHAR,warehouse_id VARCHAR,ship_date DATE)','fact_stock_movement(movement_id VARCHAR,sku_id VARCHAR,warehouse_id VARCHAR,movement_date DATE,quantity INTEGER)','fact_inventory_day(stock_date DATE,sku_id VARCHAR,warehouse_id VARCHAR,quantity INTEGER)']:
        c.execute('CREATE TABLE '+definition)
    c.execute("INSERT INTO fact_order VALUES('O1','2026-09-01','R1'),('O2','2026-09-01','R1')")
    c.execute("INSERT INTO dim_warehouse VALUES('W1','R1'),('W2','R2')")
    c.execute("INSERT INTO fact_order_item VALUES('I1','O1','S1',2),('I2','O1','S1',1),('I3','O2','S1',2)")
    c.execute("INSERT INTO dim_device VALUES('D1','2026-08-01')")
    c.execute("INSERT INTO bridge_order_device VALUES('D1','I1')")
    c.execute("INSERT INTO fact_stock_movement VALUES('opening','S1','W1','2026-09-01',10),('later','S1','W1','2026-09-04',-6)")
    c.execute("INSERT INTO fact_inventory_day VALUES('2026-09-01','S1','W1',10),('2026-09-04','S1','W1',4)")
    yield c
    c.close()

def test_aggregate_same_sku_and_rebuild_later_snapshots(stock):
    apply_transaction(stock,[event()]);verify(stock,event())
    assert stock.execute("SELECT quantity FROM fact_stock_movement WHERE movement_id='SH1:stock:S1'").fetchone()==(-3,)
    assert stock.execute('SELECT stock_date,quantity FROM fact_inventory_day ORDER BY stock_date').fetchall()==[(date(2026,9,1),10),(date(2026,9,2),7),(date(2026,9,4),1)]
    stock.execute("UPDATE fact_stock_movement SET quantity=-2 WHERE movement_id='SH1:stock:S1'")
    with pytest.raises(ValueError,match='出库流水'):verify(stock,event())

@pytest.mark.parametrize('changes',[{'warehouse_id':'W2'},{'warehouse_id':'missing'},{'order_id':'missing'},{'ship_date':'2026-08-31'},{'ship_date':'2026-10-17'}])
def test_invalid_shipment_is_atomic(stock,changes):
    with pytest.raises(ValueError):apply_transaction(stock,[event(**changes)])
    assert stock.execute('SELECT count(*) FROM fact_shipment').fetchone()==(0,)
    assert stock.execute('SELECT sum(quantity) FROM fact_stock_movement').fetchone()==(4,)

def test_future_stock_shortage_rolls_back_entire_batch(stock):
    # First order leaves positive balances; second would overdraw a later date.
    with pytest.raises(ValueError,match='库存不足'):
        apply_transaction(stock,[event(),event(event_id='second',order_id='O2',shipment_id='SH2')])
    assert stock.execute('SELECT count(*) FROM fact_shipment').fetchone()==(0,)
    assert stock.execute('SELECT count(*) FROM fact_stock_movement').fetchone()==(2,)
    assert stock.execute('SELECT count(*) FROM fact_inventory_day').fetchone()==(2,)

def test_duplicate_order_under_different_shipment_id_rejected(stock):
    apply_transaction(stock,[event()])
    with pytest.raises(ValueError,match='已经发货'):apply_transaction(stock,[event(event_id='again',shipment_id='SH2')])
    assert stock.execute('SELECT sum(quantity) FROM fact_stock_movement').fetchone()==(1,)

@pytest.mark.parametrize('table,column',[('fact_device_activation','activation_date'),('fact_service_ticket','created_date')])
def test_ship_after_activation_or_service_rejected(stock,table,column):
    stock.execute('INSERT INTO '+table+' VALUES(?,?)',['D1','2026-09-01'])
    with pytest.raises(ValueError,match='晚于'):apply_transaction(stock,[event()])

def test_inventory_gate_detects_negative_days_without_snapshot(stock):
    stock.execute("INSERT INTO fact_stock_movement VALUES('bad','S1','W1','2026-09-03',-11),('replenish','S1','W1','2026-09-04',11)")
    with pytest.raises(ValueError,match='库存不足'):check_inventory(stock)

def test_schema_forbids_caller_quantity_and_invalid_date():
    for changes in [dict(quantity=1),dict(ship_date=True),dict(ship_date='2026-9-2')]:
        with pytest.raises(ValueError):event(**changes)
