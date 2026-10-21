import copy
import pytest
import duckdb
from src.petkit.ingestion_models import PaidOrder,Batch
from src.petkit.ingestion import apply_transaction,verify_order

def payload():
    return dict(event_id='oe1',kind='order.paid',order_id='O1',customer_id='C1',channel_id='CH1',paid_date='2026-09-01',paid_amount='100.00',items=[dict(item_id='I1',sku_id='S1',quantity=1,amount='100.00',devices=[dict(device_id='D1',manufactured_date='2026-08-01')])])

@pytest.fixture
def ledger():
    c=duckdb.connect()
    for ddl in ['dim_customer(customer_id VARCHAR,region_id VARCHAR)','dim_channel(channel_id VARCHAR)','dim_sku(sku_id VARCHAR,product_id VARCHAR)','fact_product_cost(sku_id VARCHAR,effective_date DATE,cost DOUBLE)','fact_order(order_id VARCHAR,customer_id VARCHAR,channel_id VARCHAR,region_id VARCHAR,paid_date DATE,paid_amount DOUBLE,status VARCHAR)','fact_order_item(item_id VARCHAR,order_id VARCHAR,sku_id VARCHAR,quantity INTEGER,amount DOUBLE,cost DOUBLE)','fact_payment(payment_id VARCHAR,order_id VARCHAR,paid_date DATE,amount DOUBLE)','dim_device(device_id VARCHAR,product_id VARCHAR,manufactured_date DATE)','bridge_order_device(device_id VARCHAR,item_id VARCHAR)']:
        c.execute('CREATE TABLE '+ddl)
    c.execute("INSERT INTO dim_customer VALUES('C1','R2')")
    c.execute("INSERT INTO dim_channel VALUES('CH1')")
    c.execute("INSERT INTO dim_sku VALUES('S1','P1')")
    c.execute("INSERT INTO fact_product_cost VALUES('S1','2026-01-01',30),('S1','2026-10-01',999)")
    yield c
    c.close()

def test_order_aggregate_derives_region_and_effective_cost(ledger):
    event=PaidOrder.model_validate(payload())
    apply_transaction(ledger,[event]); verify_order(ledger,event)
    assert ledger.execute('SELECT region_id FROM fact_order').fetchone()==('R2',)
    assert ledger.execute('SELECT cost FROM fact_order_item').fetchone()==(30.0,)
    assert ledger.execute('SELECT product_id FROM dim_device').fetchone()==('P1',)
    with pytest.raises(ValueError): apply_transaction(ledger,[event])
    assert ledger.execute('SELECT count(*) FROM fact_payment').fetchone()==(1,)
    ledger.execute('UPDATE fact_payment SET amount=99')
    with pytest.raises(ValueError,match='不一致'): verify_order(ledger,event)

@pytest.mark.parametrize('change',[{'paid_amount':'99.00'},{'region_id':'R1'},{'paid_date':True}])
def test_order_rejects_unbalanced_or_spoofed_header(change):
    with pytest.raises(ValueError): PaidOrder.model_validate({**payload(),**change})

@pytest.mark.parametrize('change',[{'quantity':2},{'quantity':True},{'amount':'0.00'},{'devices':[dict(device_id='D1',manufactured_date='2026-09-02')]}])
def test_order_rejects_invalid_item(change):
    value=payload(); value['items'][0].update(change)
    with pytest.raises(ValueError): PaidOrder.model_validate(value)

@pytest.mark.parametrize('change',[{'customer_id':'missing'},{'channel_id':'missing'},{'paid_date':'2026-10-01'}])
def test_bad_order_rolls_back_prior_order(ledger,change):
    good=PaidOrder.model_validate(payload()); value=payload()
    value.update(order_id='O2',event_id='oe2',**change)
    with pytest.raises(ValueError): apply_transaction(ledger,[good,PaidOrder.model_validate(value)])
    for table in ['fact_order','fact_order_item','fact_payment','dim_device','bridge_order_device']:
        assert ledger.execute('SELECT count(*) FROM '+table).fetchone()==(0,)

def test_duplicate_device_across_orders_rolls_back_batch(ledger):
    first=payload(); second=copy.deepcopy(first)
    second.update(order_id='O2',event_id='oe2'); second['items'][0]['item_id']='I2'
    with pytest.raises(ValueError): apply_transaction(ledger,[PaidOrder.model_validate(first),PaidOrder.model_validate(second)])
    assert ledger.execute('SELECT count(*) FROM fact_order').fetchone()==(0,)
