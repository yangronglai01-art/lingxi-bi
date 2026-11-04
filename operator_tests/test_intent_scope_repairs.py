import json
from pathlib import Path
import pytest
from src.enterprise.intent import shape,derive,Clarify,verify_sql
from src.petkit.catalog import catalog_payload
from src.petkit.guard import check
from src.petkit.planning import examples

def catalog():
    c=catalog_payload();c['as_of']='2026-10-16';return c

@pytest.mark.parametrize('example',examples('2026-10-16'))
def test_polite_approved_question_preserves_intent(example):
    c=catalog()
    assert derive('请查询'+example['question']+'情况',c)==derive(example['question'],c)

@pytest.mark.parametrize('question',['请查询各固件版本设备离线率只看华东情况','各型号净销售额同比','各型号销售额'])
def test_new_vocabulary_does_not_ignore_conditions(question):
    with pytest.raises(Clarify):derive(question,catalog())

def test_derived_alias_renaming_preserves_bindings():
    c=catalog()
    a='SELECT x.item_id, r.refunded FROM fact_order_item x LEFT JOIN (SELECT item_id, SUM(amount) AS refunded FROM fact_refund GROUP BY item_id) r ON x.item_id=r.item_id'
    b=a.replace('x.','outer_items.').replace(' x LEFT',' outer_items LEFT').replace('r.','refunds.').replace(') r ON',') refunds ON')
    assert shape(a,c)==shape(b,c)

def test_wrong_metric_still_rejected():
    c=catalog();e=examples(c['as_of'])[0]
    wrong=e['sql'].replace('i.amount-COALESCE(r.refunded,0)','i.amount')
    assert wrong!=e['sql']
    with pytest.raises(Clarify):verify_sql(wrong,derive(e['question'],c),c)
