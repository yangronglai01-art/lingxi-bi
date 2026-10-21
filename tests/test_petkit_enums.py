import pytest
from src.petkit.access import Principal
from src.petkit.retrieval import retrieve
from src.petkit.catalog import METRICS
from src.petkit.guard import check
from src.petkit.semantic import validate_metric,MetricError


@pytest.mark.parametrize('sql,valid',[
 ("SELECT order_id FROM fact_order WHERE status='已支付'",True),
 ("SELECT order_id FROM fact_order WHERE status='paid'",False),
 ("SELECT order_id FROM fact_order WHERE status IN ('paid','已支付')",False),
 ("SELECT order_id FROM fact_order WHERE 'paid'=status",False),
 ("WITH x AS (SELECT order_id,status FROM fact_order) SELECT order_id FROM x WHERE status='paid'",False),
])
def test_authoritative_enum_values(sql,valid):
    ctx=retrieve('净销售额',Principal(),next(m for m in METRICS if m['id']=='net_sales'))
    checked=check(sql,ctx)
    if valid: validate_metric(checked.sql,ctx)
    else:
        with pytest.raises(MetricError): validate_metric(checked.sql,ctx)


def test_metric_documents_are_auditable_retrieval_evidence():
    ctx=retrieve('净销售额',Principal(),None)
    assert any(e['reason']=='metric_dependency:net_sales' for e in ctx['evidence'])
    assert 'fact_order_item' in ctx['tables']
    assert ctx['tables']['fact_order']['columns']['status']['enum']==['已支付']
