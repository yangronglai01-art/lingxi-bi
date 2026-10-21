from datetime import date
import json
import pytest
from src.petkit.ingestion_models import Batch,Refund,Activation,Ticket
from src.petkit import ingestion,control

def refund(event_id='e1',amount='10.00'):
    return dict(event_id=event_id,kind='refund.created',refund_id='RF-'+event_id,item_id='I1',refund_date='2026-01-02',amount=amount)

@pytest.fixture
def ledger(tmp_path):
    import duckdb
    c=duckdb.connect(str(tmp_path/"tiny.duckdb"))
    for ddl in [
        'CREATE TABLE fact_order(order_id VARCHAR,paid_date DATE,customer_id VARCHAR)',
        'CREATE TABLE fact_order_item(item_id VARCHAR,order_id VARCHAR,amount DOUBLE)',
        'CREATE TABLE fact_refund(refund_id VARCHAR,item_id VARCHAR,refund_date DATE,amount DOUBLE)',
        'CREATE TABLE bridge_order_device(device_id VARCHAR,item_id VARCHAR)',
        'CREATE TABLE fact_shipment(shipment_id VARCHAR,order_id VARCHAR,warehouse_id VARCHAR,ship_date DATE)',
        'CREATE TABLE fact_device_activation(device_id VARCHAR,activation_date DATE)',
        'CREATE TABLE fact_device_binding(device_id VARCHAR,customer_id VARCHAR,bound_date DATE)',
        'CREATE TABLE dim_fault(fault_id VARCHAR)', 'CREATE TABLE dim_employee(employee_id VARCHAR)',
        'CREATE TABLE fact_service_ticket(ticket_id VARCHAR,device_id VARCHAR,fault_id VARCHAR,employee_id VARCHAR,created_date DATE,status VARCHAR)',
        'CREATE TABLE fact_ticket_status(status_id VARCHAR,ticket_id VARCHAR,event_date DATE,status VARCHAR)',
        'CREATE TABLE fact_support_contact(contact_id VARCHAR,ticket_id VARCHAR,employee_id VARCHAR,contact_date DATE)']:
        c.execute(ddl)
    c.execute("INSERT INTO fact_order VALUES('O1',DATE '2026-01-01','C1')")
    c.execute("INSERT INTO fact_order_item VALUES('I1','O1',100)")
    c.execute("INSERT INTO bridge_order_device VALUES('D1','I1')")
    c.execute("INSERT INTO dim_fault VALUES('F1')"); c.execute("INSERT INTO dim_employee VALUES('E1')")
    yield c
    c.close()

@pytest.mark.parametrize('amount',['-1.00','0.00','1.001','NaN',10,True])
def test_money_is_explicit_positive_two_decimal_string(amount):
    with pytest.raises(ValueError): Refund.model_validate(refund(amount=amount))

def test_batch_forbids_unknown_fields_duplicate_events_and_bool_version():
    payload=dict(version=1,source='mock',batch_id='b1',events=[refund()])
    assert Batch.model_validate(payload).events[0].amount=='10.00'
    for change in [dict(version=True),dict(events=[refund(),refund()]),dict(role='analyst')]:
        with pytest.raises(ValueError): Batch.model_validate({**payload,**change})

def test_atomic_batch_rolls_back_earlier_valid_refund(ledger):
    good=Refund.model_validate(refund())
    bad=Refund.model_validate(refund('e2','99.00'))
    with pytest.raises(ValueError,match='累计退款'): ingestion.apply_transaction(ledger,[good,bad])
    assert ledger.execute('SELECT count(*) FROM fact_refund').fetchone()[0]==0

def test_activation_and_ticket_write_consistent_related_rows(ledger):
    activation=Activation.model_validate(dict(event_id='a1',kind='device.activated',device_id='D1',activation_date='2026-01-02'))
    ticket=Ticket.model_validate(dict(event_id='t1',kind='ticket.created',ticket_id='T1',device_id='D1',fault_id='F1',employee_id='E1',created_date='2026-01-03'))
    ingestion.apply_transaction(ledger,[activation,ticket])
    assert ledger.execute('SELECT customer_id FROM fact_device_binding').fetchone()[0]=='C1'
    assert ledger.execute('SELECT status FROM fact_service_ticket').fetchone()[0]=='已创建'
    assert ledger.execute('SELECT count(*) FROM fact_ticket_status').fetchone()[0]==1
    assert ledger.execute('SELECT count(*) FROM fact_support_contact').fetchone()[0]==1
    with pytest.raises(ValueError): ingestion.apply_transaction(ledger,[activation])
    assert ledger.execute('SELECT count(*) FROM fact_device_activation').fetchone()[0]==1

@pytest.mark.parametrize('kind,day', [('activation','2025-12-31'),('activation','2026-10-17'),('ticket','2025-12-31'),('ticket','2026-10-17')])
def test_out_of_window_business_events_rejected(ledger,kind,day):
    event=(Activation.model_validate(dict(event_id='a1',kind='device.activated',device_id='D1',activation_date=day)) if kind=='activation'
        else Ticket.model_validate(dict(event_id='t1',kind='ticket.created',ticket_id='T1',device_id='D1',fault_id='F1',employee_id='E1',created_date=day)))
    with pytest.raises(ValueError): ingestion.apply_transaction(ledger,[event])

def test_invalid_batch_is_quarantined_without_changing_current(tmp_path,monkeypatch):
    monkeypatch.setattr(control,'DATA',tmp_path); monkeypatch.setattr(ingestion,'DATA',tmp_path)
    incoming=tmp_path/'bad.json'; incoming.write_text('{"version":1,"sql":"DELETE FROM fact_order"}')
    current=tmp_path/'current.json'; current.write_text('{"generation":"untouched"}')
    with pytest.raises(ValueError,match='隔离'): ingestion.ingest(incoming)
    assert current.read_text()=='{"generation":"untouched"}'
    with control.db() as c: row=c.execute('SELECT state,archive FROM ingestion_attempts').fetchone()
    assert row['state']=='quarantined'
    from pathlib import Path
    assert Path(row['archive']).read_bytes()==incoming.read_bytes()

@pytest.mark.parametrize('kind',['device.activated','ticket.created'])
def test_lifecycle_cannot_precede_existing_shipment(ledger,kind):
    ledger.execute("INSERT INTO fact_shipment VALUES('SH1','O1','W1','2026-01-03')")
    payload=(dict(event_id='a',kind=kind,device_id='D1',activation_date='2026-01-02') if kind=='device.activated' else dict(event_id='t',kind=kind,ticket_id='T1',device_id='D1',fault_id='F1',employee_id='E1',created_date='2026-01-02'))
    event=(Activation if kind=='device.activated' else Ticket).model_validate(payload)
    with pytest.raises(ValueError,match='早于已有发货'): ingestion.apply_transaction(ledger,[event])
