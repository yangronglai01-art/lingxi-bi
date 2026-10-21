from types import SimpleNamespace
import pytest
from scripts.expanded_eval_support import regional_rows,grade

def raw():
    data={name:[] for name in ['dim_channel','dim_firmware','dim_warehouse','dim_fault','dim_device']}
    data['dim_product']=[dict(product_id='P1',product_name='A'),dict(product_id='P2',product_name='B'),dict(product_id='P3',product_name='C')]
    return data

def answer(metric,columns,rows):
    return SimpleNamespace(status='success',mock_used=False,truncated=False,context={'metric':{'id':metric}},
        result=SimpleNamespace(columns=columns,rows=rows))

def case(metric='net_sales',**kwargs):
    return dict(expected_status='success',metric_id=metric,**kwargs)

def test_regional_oracle_filters_order_device_and_warehouse_facts():
    data=raw()
    data.update(fact_order=[dict(order_id='o1',region_id='R1'),dict(order_id='o2',region_id='R2')],
        fact_order_item=[dict(item_id='i1',order_id='o1'),dict(item_id='i2',order_id='o2')],
        fact_refund=[dict(item_id='i1'),dict(item_id='i2')],
        bridge_order_device=[dict(item_id='i1',device_id='d1'),dict(item_id='i2',device_id='d2')],
        fact_device_day=[dict(device_id='d1'),dict(device_id='d2')],
        fact_service_ticket=[dict(device_id='d1'),dict(device_id='d2')],
        dim_warehouse=[dict(warehouse_id='w1',region_id='R1'),dict(warehouse_id='w2',region_id='R2')],
        fact_inventory_day=[dict(warehouse_id='w1'),dict(warehouse_id='w2')],
        fact_receipt=[dict(warehouse_id='w1'),dict(warehouse_id='w2')])
    selected=regional_rows(data,['R1'])
    assert selected['fact_order']==[data['fact_order'][0]]
    for table in ['fact_order_item','fact_refund','bridge_order_device','fact_device_day','fact_service_ticket','fact_inventory_day','fact_receipt']:
        assert selected[table]==[data[table][0]]
        assert len(data[table])==2
    assert regional_rows(data,[])==data

def test_swapped_cohort_named_values_fail_even_if_numeric_set_matches():
    value=answer('activation30',['name','devices','matched_devices','rate_percent'],[['A',10,20,50]])
    assert not grade(case('activation30'),value,{'A':(20,10,50)},raw())['passed']

def test_unexpected_extra_numeric_column_fails():
    value=answer('net_sales',['name','sales','unverified'],[['A',10,999]])
    assert not grade(case(),value,{'A':(10,)},raw())['passed']

def test_duplicate_groups_fail():
    value=answer('net_sales',['name','sales'],[['A',10],['A',10]])
    assert not grade(case(),value,{'A':(10,)},raw())['passed']

@pytest.mark.parametrize('rows,passed',[
    ([['B',30],['C',20]],True),([['C',20],['B',30]],False),([['B',30],['A',10]],False),
])
def test_topn_checks_subset_and_sort(rows,passed):
    value=answer('net_sales',['name','sales'],rows)
    assert grade(case(top_n=2,descending=True),value,{'A':(10,),'B':(30,),'C':(20,)},raw())['passed']==passed

def test_policy_status_with_business_result_is_not_a_pass():
    value=answer('net_sales',['name','sales'],[['A',10]])
    value.status='denied'
    assert not grade(dict(expected_status='denied'),value,None,raw())['passed']


def test_equivalent_id_and_name_are_one_group():
    value=answer('net_sales',['product_id','product_name','sales'],[['P1','A',10]])
    assert grade(case(),value,{'A':(10,)},raw())['passed']

def test_conflicting_id_and_name_are_not_accepted():
    value=answer('net_sales',['product_id','product_name','sales'],[['P2','A',10]])
    assert not grade(case(),value,{'A':(10,)},raw())['passed']
