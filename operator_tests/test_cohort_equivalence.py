import copy,json
from pathlib import Path
import pytest
import sqlglot
from sqlglot import exp
from src.enterprise.intent import derive,verify_sql,Clarify
from src.enterprise.cohort_equivalence import reference_alternatives
from src.petkit.catalog import catalog_payload
from src.petkit.planning import template_sql
from src.petkit.guard import check
from src.petkit.semantic import validate_metric

def catalog():
    c=catalog_payload();c['as_of']='2026-10-16';return c

def sample(metric='activation30',month='2026-05'):
    c=catalog();word='激活率' if metric=='activation30' else '售后设备率'
    i=derive(f'2026年{int(month[-2:])}月各型号售出30天{word}',c)
    tree=sqlglot.parse_one(template_sql(i.metric,i.group,i.month,i.as_of),read='starrocks')
    for x in tree.find_all(exp.Interval):x.set('unit',exp.Var(this='DAY'))
    list(tree.find_all(exp.CTE))[1].this.set('distinct',None)
    return c,i,tree.sql(dialect='starrocks')

@pytest.mark.parametrize('metric',['activation30','service30'])
@pytest.mark.parametrize('month',['2026-05','2026-07'])
def test_bounded_equivalent_cohort_is_accepted(metric,month):
    c,i,sql=sample(metric,month)
    assert verify_sql(sql,i,c)['limited_equivalence']=='date_day_unit_and_redundant_outcome_distinct'

@pytest.mark.parametrize('mutation',[
    lambda s:s.replace('COUNT(DISTINCT c.device_id)','COUNT(c.device_id)'),
    lambda s:s.replace('COUNT(DISTINCT e.device_id)','COUNT(e.device_id)'),
    lambda s:s.replace('COUNT(DISTINCT e.device_id)','SUM(e.device_id)'),
    lambda s:s.replace("INTERVAL '30' DAY","INTERVAL '31' DAY"),
    lambda s:s.replace("INTERVAL '30' DAY","INTERVAL '29' DAY"),
    lambda s:s.replace('BETWEEN c.paid_date AND','BETWEEN c.paid_date + INTERVAL \'1\' DAY AND'),
    lambda s:s.replace('LEFT JOIN outcomes','JOIN outcomes'),
    lambda s:s.replace("= '2026-05'","= '2026-07'"),
    lambda s:s.replace("<= CAST('2026-10-16' AS DATE) - INTERVAL '30' DAY", "<= CAST('2026-10-16' AS DATE)"),
    lambda s:s.replace('COUNT(DISTINCT c.device_id)','COUNT(*)'),
    lambda s:s.replace('NULLIF(COUNT(DISTINCT c.device_id), 0)','COUNT(DISTINCT c.device_id)'),
    lambda s:s.replace('100.0 * COUNT','1.0 * COUNT'),
])
def test_business_changes_still_rejected(mutation):
    c,i,sql=sample();changed=mutation(sql)
    assert changed!=sql
    with pytest.raises((Clarify,PermissionError)):verify_sql(changed,i,c)

@pytest.mark.parametrize('metric',['activation30','service30'])
@pytest.mark.parametrize('typ',['DATETIME','TIMESTAMP','TIMESTAMPTZ','VARCHAR'])
def test_other_date_types_do_not_enable_alternatives(metric,typ):
    c,i,sql=sample(metric)
    c['tables']['fact_order']['columns']['paid_date']['type']=typ
    assert reference_alternatives(i,c,template_sql(i.metric,i.group,i.month,i.as_of))==[]

def test_dedup_not_removed_for_sum_consumer():
    c,i,_=sample();reference=template_sql(i.metric,i.group,i.month,i.as_of)
    changed=reference.replace('COUNT(DISTINCT e.device_id)','SUM(e.device_id)')
    assert len(reference_alternatives(i,c,changed))==1

def test_event_cte_multiple_consumers_not_deduplicated():
    c,i,_=sample();reference=template_sql(i.metric,i.group,i.month,i.as_of)
    reference=reference.replace('GROUP BY p.product_name','LEFT JOIN outcomes extra ON c.device_id=extra.device_id GROUP BY p.product_name')
    assert len(reference_alternatives(i,c,reference))==1

@pytest.mark.parametrize('index',range(4))
def test_captured_first_answers_pass_all_gates(index):
    fixtures=Path(__file__).parent/'fixtures/cohort_saved_sql_20261031.json'
    row=json.loads(fixtures.read_text(encoding='utf-8'))[index]
    c=catalog();intent=derive(row['question'],c)
    metric=next(m for m in c['metrics'] if m['id']==intent.metric)
    context=dict(tables=c['tables'],relationships=c['relationships'],metric=metric,data_as_of=c['as_of'])
    checked=check(row['sql'],context)
    validate_metric(checked.sql,context)
    assert verify_sql(checked.sql,intent,c)['limited_equivalence']
