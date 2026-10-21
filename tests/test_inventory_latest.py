from datetime import date
import duckdb
import pytest
from src.petkit.planning import template_sql
from src.petkit.reference import expected

@pytest.mark.parametrize('month,answer',[(None,{'A':(37,),'B':(70,)}),('2026-08',{'A':(10,)}),('2026-09',{'A':(37,),'B':(70,)})])
def test_asynchronous_inventory_updates_keep_other_skus_and_warehouses(month,answer):
    rows=[('2026-08-31','S1','W1',10),('2026-09-01','S1','W1',9),('2026-09-02','S1','W1',7),('2026-09-01','S1','W2',30),('2026-09-01','S2','W1',70)]
    raw={name:[] for name in ['dim_device','fact_order','fact_order_item']}
    raw.update(dim_product=[dict(product_id='P1',product_name='A'),dict(product_id='P2',product_name='B')],dim_sku=[dict(sku_id='S1',product_id='P1'),dict(sku_id='S2',product_id='P2')],fact_inventory_day=[dict(stock_date=date.fromisoformat(day),sku_id=sku,warehouse_id=w,quantity=q) for day,sku,w,q in rows])
    assert expected(dict(metric_id='inventory',group='model',month=month),raw)==answer
    with duckdb.connect() as c:
        c.execute('CREATE TABLE fact_inventory_day(stock_date DATE,sku_id VARCHAR,warehouse_id VARCHAR,quantity INTEGER)')
        c.executemany('INSERT INTO fact_inventory_day VALUES(?,?,?,?)',rows)
        c.execute("CREATE TABLE dim_sku AS SELECT * FROM (VALUES ('S1','P1'),('S2','P2')) t(sku_id,product_id)")
        c.execute("CREATE TABLE dim_product AS SELECT * FROM (VALUES ('P1','A'),('P2','B')) t(product_id,product_name)")
        actual={k:(v,) for k,v in c.execute(template_sql('inventory',month=month)).fetchall()}
        assert actual==answer
