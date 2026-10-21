import pytest
from src.petkit.retrieval import current_catalog
from src.petkit.semantic import validate_metric,MetricError

@pytest.mark.parametrize('wrapped',[False,True])
def test_event_filter_must_not_shrink_left_joined_cohort(wrapped):
    cat=current_catalog()
    context=dict(tables=cat['tables'],metric={'id':'activation30'},data_as_of=cat['as_of'])
    base="""SELECT o.order_id,a.activation_date FROM fact_order o
      LEFT JOIN fact_device_activation a ON o.order_id=a.device_id
      WHERE o.paid_date<=DATE '2026-09-16'"""
    sql=(f"WITH c AS ({base}) SELECT c.order_id FROM c WHERE c.activation_date IS NULL OR c.activation_date<DATE '2026-08-01'"
         if wrapped else base+" AND (a.activation_date IS NULL OR a.activation_date<DATE '2026-08-01')")
    with pytest.raises(MetricError,match='分母'): validate_metric(sql,context)

def test_event_window_in_on_preserves_cohort():
    cat=current_catalog()
    context=dict(tables=cat['tables'],metric={'id':'activation30'},data_as_of=cat['as_of'])
    validate_metric("""SELECT o.order_id,a.activation_date FROM fact_order o
      LEFT JOIN fact_device_activation a ON o.order_id=a.device_id AND a.activation_date<DATE '2026-08-01'
      WHERE o.paid_date<=DATE '2026-09-16'""",context)
