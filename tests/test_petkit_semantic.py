import pytest
from src.petkit.retrieval import retrieve
from src.petkit.access import Principal
from src.petkit.catalog import METRICS
from src.petkit.guard import check
from src.petkit.semantic import validate_metric,MetricError


@pytest.mark.parametrize('where,allowed',[
    ("o.paid_date <= DATE '2026-09-16'",True),
    ("o.paid_date <= DATE '2026-10-16' - INTERVAL '30 days'",True),
    ("o.paid_date < DATE '2026-10-01'",False),
    ("o.paid_date <= DATE '2026-09-16' OR 1=1",False),
])
def test_mature_cohort_constraint(where,allowed):
    ctx=retrieve('售出30天售后设备率',Principal(),next(m for m in METRICS if m['id']=='service30'))
    sql=check('SELECT o.order_id FROM fact_order o WHERE '+where,ctx).sql
    if allowed: validate_metric(sql,ctx)
    else:
        with pytest.raises(MetricError): validate_metric(sql,ctx)


def test_every_order_cte_requires_own_maturity_constraint():
    ctx=retrieve('售出30天售后设备率',Principal(),next(m for m in METRICS if m['id']=='service30'))
    sql="""WITH mature AS (SELECT order_id FROM fact_order WHERE paid_date<=DATE '2026-09-16'),
      immature AS (SELECT order_id FROM fact_order WHERE paid_date<DATE '2026-10-01')
      SELECT a.order_id FROM mature a JOIN immature b ON a.order_id=b.order_id"""
    with pytest.raises(MetricError): validate_metric(check(sql,ctx).sql,ctx)
