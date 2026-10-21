from datetime import date
import pytest,duckdb
from src.petkit.catalog import METRICS
from src.petkit.planning import plan,template_sql
from src.petkit.reference import expected
from src.petkit.retrieval import retrieve
from src.petkit.access import Principal
from src.petkit.guard import check
from src.petkit.semantic import validate_metric,MetricError
from src.petkit.result_contract import validate
from src.engine.executor import QueryResult

METRIC=next(m for m in METRICS if m['id']=='received_units')

@pytest.fixture
def context():return retrieve('各仓库采购收货数量',Principal(),METRIC)

@pytest.mark.parametrize('month,want',[(None,{'甲仓':(7,),'乙仓':(9,)}),('2026-09',{'甲仓':(5,),'乙仓':(9,)}),('2026-08',{'甲仓':(2,)})])
def test_receipt_quantity_uses_receipt_date_and_sum_not_document_count(month,want,context):
    raw={n:[] for n in ['dim_product','dim_device','fact_order','fact_order_item']}
    raw.update(dim_warehouse=[dict(warehouse_id='W1',warehouse_name='甲仓'),dict(warehouse_id='W2',warehouse_name='乙仓')],fact_receipt=[dict(receipt_id=str(i),warehouse_id=w,receipt_date=date.fromisoformat(day),quantity=q) for i,(w,day,q) in enumerate([('W1','2026-08-31',2),('W1','2026-09-01',2),('W1','2026-09-02',3),('W2','2026-09-01',9)])])
    assert expected(dict(metric_id='received_units',group='warehouse',month=month),raw)==want
    with duckdb.connect() as c:
        c.execute('CREATE TABLE fact_receipt(receipt_id VARCHAR,warehouse_id VARCHAR,receipt_date DATE,quantity INTEGER)')
        c.executemany('INSERT INTO fact_receipt VALUES(?,?,?,?)',[(r['receipt_id'],r['warehouse_id'],r['receipt_date'],r['quantity']) for r in raw['fact_receipt']])
        c.execute('CREATE TABLE dim_warehouse(warehouse_id VARCHAR,warehouse_name VARCHAR)')
        c.executemany('INSERT INTO dim_warehouse VALUES(?,?)',[(r['warehouse_id'],r['warehouse_name']) for r in raw['dim_warehouse']])
        sql=check(template_sql('received_units',month=month),context).sql;validate_metric(sql,context)
        assert {k:(v,) for k,v in c.execute(sql).fetchall()}==want

@pytest.mark.parametrize('aggregate',['COUNT(*)','SUM(DISTINCT r.quantity)','SUM(r.quantity*100)','AVG(r.quantity)'])
def test_receipt_gate_rejects_count_distinct_or_amount_substitution(context,aggregate):
    sql='SELECT w.warehouse_name,'+aggregate+' AS received_units FROM fact_receipt r JOIN dim_warehouse w ON r.warehouse_id=w.warehouse_id GROUP BY w.warehouse_name'
    with pytest.raises(MetricError):validate_metric(check(sql,context).sql,context)

def test_receipt_gate_rejects_join_fanout(context):
    sql='SELECT SUM(r.quantity) FROM fact_receipt r JOIN dim_warehouse w ON r.warehouse_id=w.warehouse_id JOIN fact_receipt other ON other.warehouse_id=w.warehouse_id'
    with pytest.raises(MetricError):validate_metric(check(sql,context).sql,context)

@pytest.mark.parametrize('value',[None,-1,1.5,float('nan'),True])
def test_receipt_output_unit_contract(value):
    with pytest.raises(MetricError):validate(METRIC,QueryResult(columns=['warehouse_name','received_units'],rows=[['甲仓',value]],row_count=1))

def test_receipt_output_requires_explicit_alias():
    with pytest.raises(MetricError):validate(METRIC,QueryResult(columns=['count'],rows=[[2]],row_count=1))

@pytest.mark.parametrize('role',['sales','service','viewer'])
def test_receipt_metric_denies_other_department_roles(role):
    with pytest.raises(PermissionError):retrieve('各仓库采购收货数量',Principal(role=role),METRIC)

def test_receipt_plan_is_explicit_and_minimal():
    result=plan('2026年9月各仓库采购收货数量',demo=True)
    assert result['metric']['id']=='received_units' and result['month']=='2026-09'
    assert set(METRIC['dependencies'])=={'fact_receipt','dim_warehouse'}
    assert plan('请同时查看采购收货数量和期末库存',demo=False)['status']=='clarification'

def test_supply_role_sees_receipts_but_cannot_query_sales_or_purchase_totals():
    principal=Principal(role='supply',regions=('R1',),managed=True)
    ctx=retrieve('采购收货数量',principal,METRIC)
    assert set(METRIC['dependencies'])<=set(ctx['tables'])
    assert not principal.allowed(dict(name='fact_purchase_item',domain='supply'))
    sales=next(m for m in METRICS if m['id']=='net_sales')
    with pytest.raises(PermissionError):retrieve('净销售额',principal,sales)
