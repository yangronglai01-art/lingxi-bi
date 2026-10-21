from datetime import date
import pytest
from test_fulfillment import stock,event as shipment
from src.petkit.ingestion_models import Purchase,Receipt
from src.petkit.ingestion import apply_transaction
from src.petkit.procurement import verify,check_procurement

def purchase(**changes):
    return Purchase.model_validate(dict(event_id='p1',kind='purchase.created',purchase_id='PO1',supplier_id='SUP1',purchase_date='2026-09-01',items=[dict(purchase_item_id='PI1',sku_id='S1',quantity=5,amount='100.00')])|changes)

def receipt(**changes):
    return Receipt.model_validate(dict(event_id='r1',kind='purchase.received',receipt_id='RC1',purchase_item_id='PI1',warehouse_id='W1',receipt_date='2026-09-02',quantity=2)|changes)

@pytest.fixture
def ledger(stock):
    for ddl in ['dim_supplier(supplier_id VARCHAR)','dim_sku(sku_id VARCHAR)','fact_purchase_order(purchase_id VARCHAR,supplier_id VARCHAR,purchase_date DATE)','fact_purchase_item(purchase_item_id VARCHAR,purchase_id VARCHAR,sku_id VARCHAR,quantity INTEGER,amount DOUBLE)','fact_receipt(receipt_id VARCHAR,purchase_item_id VARCHAR,warehouse_id VARCHAR,receipt_date DATE,quantity INTEGER)']:
        stock.execute('CREATE TABLE '+ddl)
    stock.execute("INSERT INTO dim_supplier VALUES('SUP1')")
    stock.execute("INSERT INTO dim_sku VALUES('S1')")
    yield stock

def test_partial_receipts_across_warehouses_and_source_reconciliation(ledger):
    events=[purchase(),receipt(),receipt(event_id='r2',receipt_id='RC2',warehouse_id='W2',quantity=3)]
    apply_transaction(ledger,events)
    for event in events:verify(ledger,event)
    check_procurement(ledger)
    assert ledger.execute("SELECT quantity FROM fact_inventory_day WHERE stock_date='2026-09-04' AND warehouse_id='W1'").fetchone()==(6,)
    assert ledger.execute("SELECT quantity FROM fact_inventory_day WHERE stock_date='2026-09-02' AND warehouse_id='W2'").fetchone()==(3,)
    with pytest.raises(ValueError,match='超过'):apply_transaction(ledger,[receipt(event_id='r3',receipt_id='RC3',quantity=1)])
    assert ledger.execute('SELECT count(*) FROM fact_receipt').fetchone()==(2,)

def test_overreceipt_rolls_back_purchase_and_all_inventory_changes(ledger):
    with pytest.raises(ValueError,match='超过'):
        apply_transaction(ledger,[purchase(),receipt(quantity=3),receipt(event_id='r2',receipt_id='RC2',warehouse_id='W2',quantity=3)])
    assert ledger.execute('SELECT count(*) FROM fact_purchase_order').fetchone()==(0,)
    assert ledger.execute('SELECT count(*) FROM fact_receipt').fetchone()==(0,)
    assert ledger.execute('SELECT count(*) FROM fact_stock_movement').fetchone()==(2,)
    assert ledger.execute('SELECT sum(quantity) FROM fact_inventory_day').fetchone()==(14,)

@pytest.mark.parametrize('change',[dict(supplier_id='missing'),dict(purchase_date='2025-09-30'),dict(purchase_date='2026-10-01'),dict(items=[dict(purchase_item_id='PI1',sku_id='missing',quantity=1,amount='1.00')])])
def test_invalid_purchase_is_atomic(ledger,change):
    with pytest.raises(ValueError):apply_transaction(ledger,[purchase(**change)])
    assert ledger.execute('SELECT count(*) FROM fact_purchase_order').fetchone()==(0,)

@pytest.mark.parametrize('change',[dict(warehouse_id='missing'),dict(purchase_item_id='missing'),dict(receipt_date='2026-08-31'),dict(receipt_date='2026-10-17')])
def test_invalid_receipt_rolls_back_new_purchase(ledger,change):
    with pytest.raises(ValueError):apply_transaction(ledger,[purchase(),receipt(**change)])
    assert ledger.execute('SELECT count(*) FROM fact_purchase_order').fetchone()==(0,)

@pytest.mark.parametrize('quantity',[True,0,-1,1.2,10001,'2'])
def test_strict_receipt_quantities(quantity):
    with pytest.raises(ValueError):receipt(quantity=quantity)

@pytest.mark.parametrize('kind',['receipt','movement','snapshot','purchase'])
def test_source_verification_detects_tampered_rows(ledger,kind):
    apply_transaction(ledger,[purchase(),receipt()])
    sql={'receipt':"UPDATE fact_receipt SET quantity=1",'movement':"UPDATE fact_stock_movement SET quantity=1 WHERE movement_id='RC1:stock'",'snapshot':"DELETE FROM fact_inventory_day WHERE stock_date='2026-09-02'",'purchase':"UPDATE fact_purchase_item SET amount=90"}[kind]
    ledger.execute(sql)
    with pytest.raises(ValueError):verify(ledger,purchase() if kind=='purchase' else receipt())

def test_receipt_stock_funds_shipment_in_same_atomic_batch(ledger):
    ledger.execute('DELETE FROM fact_stock_movement');ledger.execute('DELETE FROM fact_inventory_day')
    apply_transaction(ledger,[purchase(),receipt(quantity=3,receipt_date='2026-09-01'),shipment()])
    assert ledger.execute("SELECT quantity FROM fact_inventory_day WHERE stock_date='2026-09-02'").fetchone()==(0,)
    check_procurement(ledger)

def test_shipment_before_receipt_in_batch_cannot_spend_future_input(ledger):
    ledger.execute('DELETE FROM fact_stock_movement');ledger.execute('DELETE FROM fact_inventory_day')
    with pytest.raises(ValueError,match='库存不足'):apply_transaction(ledger,[purchase(),shipment(),receipt(quantity=3)])
    assert ledger.execute('SELECT count(*) FROM fact_purchase_order').fetchone()==(0,)

def test_global_gate_detects_manual_overreceipt_without_source(ledger):
    apply_transaction(ledger,[purchase(),receipt()])
    ledger.execute("INSERT INTO fact_receipt VALUES('bad','PI1','W2','2026-09-03',4)")
    with pytest.raises(ValueError,match='不一致'):check_procurement(ledger)

def test_regional_receipt_views_hide_other_warehouse_and_purchase_totals(ledger):
    from src.petkit import row_policy
    from src.petkit.access import Principal
    apply_transaction(ledger,[purchase(),receipt(),receipt(event_id='r2',receipt_id='RC2',warehouse_id='W2',quantity=3)])
    principal=Principal(subject='regional-test',role='analyst',regions=('R1',),managed=True)
    assert principal.allowed(dict(name='fact_receipt',domain='supply'))
    assert not principal.allowed(dict(name='fact_purchase_order',domain='supply'))
    assert not principal.allowed(dict(name='fact_purchase_item',domain='supply'))
    row_policy.install(ledger,principal,['fact_receipt','fact_stock_movement','fact_inventory_day'])
    assert ledger.execute('SELECT receipt_id FROM fact_receipt').fetchall()==[('RC1',)]
    assert ledger.execute("SELECT movement_id FROM fact_stock_movement WHERE movement_id LIKE 'RC%'").fetchall()==[('RC1:stock',)]
